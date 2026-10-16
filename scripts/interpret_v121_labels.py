"""v1.2.1 해석 — 필터 득실을 "필터·선택"과 "라벨 정확도"로 나눈다 (사전 등록 `9454c23` §8).

**판정을 바꾸지 않는다.** 판정은 `analyze_t3_selection.py`의 출력이다. 이 스크립트는 그 출력의 1회차 적격 항목을
라벨 감사(`docs/eval/v1.2.1-label-audit.json`, Claude 독립 감사 — 사람 아님)에 대어 분류만 한다.

분류 (strict에서 해악 ⇔ 선택값 v가 어느 정답 문서의 라벨에도 없다):

- **L−** 해악이고, 감사가 v를 어느 정답 문서의 **빠진 값**으로 판정 (본 감사 `missing`, 보충 감사 `dropped`=빠짐) → 라벨 정확도
- **U**  해악이고, 어느 정답 문서가 감사 대상이 아니거나 v의 판정이 애매(보충 감사 `dropped`=애매) → 귀속하지 않음
- **S**  해악이고 위 둘이 아니다 → 필터·선택
- **L+** 해악이 아니고, v가 정답 문서 라벨에 있는데 v를 가진 정답 문서마다 감사가 그 값을 **틀림**으로 판정 → 라벨 오류가 해악을 가렸다

`--compare` 로 T3a·N1 분석 결과를 주면 케이스별 e2e·해악을 나란히 적는다(관찰).

    python scripts/interpret_v121_labels.py <v1.2.1 분석 JSON> --out <결과 JSON> \\
        [--compare docs/eval/t3-analysis-v1.2-t3-tool-on.json docs/eval/n1-analysis-v1.2-n1-null-pass.json]

LLM 호출 0건. 인덱스를 읽지 않는다 — 분석 JSON과 감사·export 파일만 읽는다.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT = REPO_ROOT / "docs/eval/v1.2.1-label-audit.json"
NEW_EXPORT = REPO_ROOT / "data/corpus"
OLD_EXPORT = REPO_ROOT / "data/corpus-archive/ontology-20260917-120733"
NEW_ID = "ontology-20261002-114500"
OLD_ID = "ontology-20260917-120733"


def load_labels(root: Path, export_id: str) -> dict[str, list[str]]:
    labels: dict[str, list[str]] = {}
    for path in sorted(root.glob(f"*/{export_id}.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                if record.get("indexable"):
                    labels[record["doc_id"]] = list(record["ontology"]["tech_domains"])
    return labels


def audit_tables(audit: dict) -> tuple[dict, dict, dict]:
    """doc_id → {빠진 값}, {애매한 탈락 값}, {틀린 붙은 값}."""
    missing: dict[str, set[str]] = defaultdict(set)
    unclear_dropped: dict[str, set[str]] = defaultdict(set)
    wrong: dict[str, set[str]] = defaultdict(set)
    audited: set[str] = set()
    for doc in audit["docs"]:
        audited.add(doc["doc_id"])
        missing[doc["doc_id"]] |= {m["value"] for m in doc["missing"]}
        wrong[doc["doc_id"]] |= {o["value"] for o in doc["others"] if o["verdict"] == "틀림"}
        if doc["agent"]["verdict"] == "틀림":
            wrong[doc["doc_id"]].add("Agent")
    for doc in audit["supplementary"]["docs"]:
        audited.add(doc["doc_id"])
        for d in doc["dropped"]:
            (missing if d["verdict"] == "빠짐" else unclear_dropped)[doc["doc_id"]].add(d["value"])
        wrong[doc["doc_id"]] |= {a["value"] for a in doc["added"] if a["verdict"] == "틀림"}
    return {"audited": audited, "missing": missing}, unclear_dropped, wrong


def classify(item: dict, expected: list[str], labels: dict, tables, unclear_dropped, wrong) -> str | None:
    value = item.get("value")
    if item.get("outcome") != "chosen" or not item.get("eligible"):
        return None
    if item["harm"]:
        if any(value in tables["missing"].get(d, ()) for d in expected):
            return "L-"
        if any(d not in tables["audited"] or value in unclear_dropped.get(d, ()) for d in expected):
            return "U"
        return "S"
    holders = [d for d in expected if value in labels.get(d, ())]
    if holders and all(value in wrong.get(d, ()) for d in holders):
        return "L+"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("analysis", type=Path)
    parser.add_argument("--compare", type=Path, nargs="*", default=[])
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    new_labels = load_labels(NEW_EXPORT, NEW_ID)
    old_labels = load_labels(OLD_EXPORT, OLD_ID)
    tables, unclear_dropped, wrong = audit_tables(audit)

    counts = {s: defaultdict(int) for s in ("A", "B")}
    case_sets = {s: defaultdict(set) for s in ("A", "B")}
    detail = []
    compat = {s: {"new": [0, 0], "old_labels": [0, 0]} for s in ("A", "B")}
    for case in analysis["cases"]:
        s = case["stratum"]
        if s not in ("A", "B"):
            continue
        expected = case["expected_doc_ids"]
        new_union = {v for d in expected for v in new_labels.get(d, ())}
        old_union = {v for d in expected for v in old_labels.get(d, ())}
        for item in case["items"]:
            if item.get("outcome") == "chosen":
                compat[s]["new"][1] += 1
                compat[s]["new"][0] += item["value"] in new_union
                if old_union:
                    compat[s]["old_labels"][1] += 1
                    compat[s]["old_labels"][0] += item["value"] in old_union
            kind = classify(item, expected, new_labels, tables, unclear_dropped, wrong)
            if kind:
                counts[s][kind] += 1
                case_sets[s][kind].add(case["case_id"])
                detail.append({"case_id": case["case_id"], "stratum": s, "item_index": item["item_index"],
                               "topic": item["topic"], "value": item["value"], "class": kind,
                               "expected_doc_ids": expected,
                               "new_labels": {d: new_labels.get(d) for d in expected},
                               "old_labels": {d: old_labels.get(d) for d in expected}})

    harm_items = {s: sum(counts[s][k] for k in ("L-", "S", "U")) for s in ("A", "B")}

    def conclusion(s: str) -> str:
        n = harm_items[s]
        if n == 0:
            return f"해악 항목 0 — L+ {counts[s]['L+']}항목만 적는다"
        if counts[s]["L-"] * 2 > n:
            return "해악의 주 원인은 라벨 정확도"
        if counts[s]["S"] * 2 > n:
            return "해악의 주 원인은 필터·선택"
        return "가르지 못함"

    comparison = []
    others = [json.loads(p.read_text(encoding="utf-8")) for p in args.compare]
    for case in analysis["cases"]:
        row = {"case_id": case["case_id"], "stratum": case["stratum"], "v1.2.1": case["passed"]}
        for other in others:
            match = next(c for c in other["cases"] if c["case_id"] == case["case_id"])
            row[other["run_label"]] = match["passed"]
        row["values_v1.2.1"] = [it["value"] for it in case["items"]]
        for other in others:
            match = next(c for c in other["cases"] if c["case_id"] == case["case_id"])
            row[f"values_{other['run_label']}"] = [it["value"] for it in match["items"]]
        comparison.append(row)

    report = {
        "preregistration": "9454c23",
        "analysis_file": args.analysis.name,
        "auditor": audit["auditor"],
        "classes": {s: {k: {"items": counts[s][k], "cases": sorted(case_sets[s][k])}
                        for k in ("L-", "S", "U", "L+")} for s in ("A", "B")},
        "harm_items": harm_items,
        "conclusion": {s: conclusion(s) for s in ("A", "B")},
        "compatibility_first_pass": {s: {k: {"k": v[0], "n": v[1]} for k, v in compat[s].items()}
                                     for s in ("A", "B")},
        "detail": detail,
        "case_comparison": comparison,
    }
    for s in ("A", "B"):
        c = report["classes"][s]
        print(f"층 {s}: 해악 {harm_items[s]} — L− {c['L-']['items']} · S {c['S']['items']} · U {c['U']['items']} "
              f"| L+ {c['L+']['items']} {c['L+']['cases']} → {report['conclusion'][s]}")
        cp = report["compatibility_first_pass"][s]
        print(f"   호환(새 라벨) {cp['new']['k']}/{cp['new']['n']} · (옛 라벨, 있는 것만) "
              f"{cp['old_labels']['k']}/{cp['old_labels']['n']}")
    for d in detail:
        print("  ", d["class"], d["case_id"], d["item_index"], d["value"], d["new_labels"])
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("→", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
