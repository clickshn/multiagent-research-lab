"""ADR-025 Amendment(session-19) 판정 — 1회차 Researcher 호출 짝 비교 + pass/fail 순 뒤집힘.

bench 결과 JSON 두 개(기준, 비교)를 읽기만 한다. LLM 호출 없음.
`compare_bench_runs.py`는 `prompt_version`·`candidate_score_exposed`가 다르면 대조를
거부한다(회차 간 대조용). 이 스크립트는 **그 조건이 다른 것이 의도된** 전/후 비교용이다.

판정 규칙은 ADR-025 Amendment(session-19)를 그대로 옮긴다 — 여기서 새로 정하지 않는다.

    python scripts/compare_adr025_pairs.py BASE.json AFTER.json [--out OUT.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RISK_CASES = ("GS-011", "GS-013", "GS-015", "GS-017", "GS-018", "GS-021")
HARM_THRESHOLD = 2  # Amendment: 순감소 ≥ 2 또는 순 뒤집힘 ≥ 2


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _first_pass(row: dict) -> list[dict] | None:
    items = row.get("items")
    if items is None:
        return None
    return [i for i in items if i["revision"] == 0]


def _signature(items: list[dict]) -> list[tuple[str, list[str]]]:
    return [(i["topic"], [c["doc_id"] for c in i["candidates"]]) for i in items]


def compare(base: dict, after: dict) -> dict:
    base_rows = {r["case_id"]: r for r in base["rows"]}
    after_rows = {r["case_id"]: r for r in after["rows"]}
    if set(base_rows) != set(after_rows):
        raise SystemExit("케이스 집합이 다르다 — 비교하지 않는다")

    # 1회차 (케이스, 항목) 일치 검사 — 30건 전체
    mismatched: list[dict] = []
    for case_id in sorted(base_rows):
        b, a = _first_pass(base_rows[case_id]), _first_pass(after_rows[case_id])
        if b is None or a is None:
            mismatched.append({"case_id": case_id, "reason": "items 없음"})
            continue
        if _signature(b) != _signature(a):
            mismatched.append(
                {
                    "case_id": case_id,
                    "reason": "1회차 항목 또는 후보 불일치",
                    "base": _signature(b),
                    "after": _signature(a),
                }
            )
    risk_mismatch = [m for m in mismatched if m["case_id"] in RISK_CASES]

    # 1차 지표 — 위험군 1회차 짝 비교
    primary: dict | None = None
    if not risk_mismatch:
        pairs = []
        for case_id in RISK_CASES:
            expected = set(base_rows[case_id]["score"]["expected_doc_ids"])
            for bi, ai in zip(
                _first_pass(base_rows[case_id]), _first_pass(after_rows[case_id]), strict=True
            ):
                if not expected & {c["doc_id"] for c in bi["candidates"]}:
                    continue
                b_hit = bool(expected & set(bi["supporting"]))
                a_hit = bool(expected & set(ai["supporting"]))
                pairs.append(
                    {
                        "case_id": case_id,
                        "topic": bi["topic"],
                        "expected_ranks": [
                            c["rank"] for c in bi["candidates"] if c["doc_id"] in expected
                        ],
                        "base_hit": b_hit,
                        "after_hit": a_hit,
                        "base_supporting": bi["supporting"],
                        "after_supporting": ai["supporting"],
                    }
                )
        dec = sum(p["base_hit"] and not p["after_hit"] for p in pairs)
        inc = sum(p["after_hit"] and not p["base_hit"] for p in pairs)
        primary = {
            "eligible_pairs": len(pairs),
            "base_hits": sum(p["base_hit"] for p in pairs),
            "after_hits": sum(p["after_hit"] for p in pairs),
            "decrease": dec,
            "increase": inc,
            "net_decrease": dec - inc,
            "supporting_changed": [
                p for p in pairs if set(p["base_supporting"]) != set(p["after_supporting"])
            ],
            "pairs": pairs,
        }

    # pass/fail 순 뒤집힘 — 층별
    flips: dict[str, dict] = {}
    for case_id in sorted(base_rows):
        br, ar = base_rows[case_id], after_rows[case_id]
        s = br["stratum"]
        f = flips.setdefault(s, {"n": 0, "pass_to_fail": [], "fail_to_pass": []})
        f["n"] += 1
        bp, ap = br["score"].get("passed"), ar["score"].get("passed")
        if bp and ap is False:
            f["pass_to_fail"].append(case_id)
        elif bp is False and ap:
            f["fail_to_pass"].append(case_id)
    p2f = sum(len(f["pass_to_fail"]) for f in flips.values())
    f2p = sum(len(f["fail_to_pass"]) for f in flips.values())
    net_flip = p2f - f2p

    cited_changed = [
        c
        for c in sorted(base_rows)
        if set(base_rows[c]["score"].get("cited_doc_ids") or [])
        != set(after_rows[c]["score"].get("cited_doc_ids") or [])
    ]

    if primary is None:
        verdict = "중단 — 위험군 1회차 짝 불일치 (Outliner·검색 변화)"
    elif primary["net_decrease"] >= HARM_THRESHOLD or net_flip >= HARM_THRESHOLD:
        verdict = "해악"
    else:
        verdict = "무해 확인"
    observations = []
    if primary is not None and (primary["decrease"] or primary["increase"]):
        observations.append(
            f"1차 지표 감소 {primary['decrease']} · 증가 {primary['increase']}"
        )
    if p2f or f2p:
        observations.append(f"pass→fail {p2f} · fail→pass {f2p}")

    return {
        "base": {"label": base["label"], "prompt_version": base["prompt_version"],
                 "candidate_score_exposed": base["candidate_score_exposed"],
                 "generated_at": base["generated_at"]},
        "after": {"label": after["label"], "prompt_version": after["prompt_version"],
                  "candidate_score_exposed": after["candidate_score_exposed"],
                  "generated_at": after["generated_at"]},
        "rule": "ADR-025 Amendment(session-19): 해악 = 1차 순감소 ≥2 또는 pass/fail 순 뒤집힘 ≥2",
        "verdict": verdict,
        "observations": observations,
        "first_pass_mismatch": mismatched,
        "primary": primary,
        "secondary_risk_selection": {
            "base": {k: base["risk_selection"][k] for k in
                     ("selected", "eligible", "first_pass_selected", "first_pass_eligible")},
            "after": {k: after["risk_selection"][k] for k in
                      ("selected", "eligible", "first_pass_selected", "first_pass_eligible")},
        },
        "pass_fail": {"by_stratum": flips, "pass_to_fail": p2f, "fail_to_pass": f2p,
                      "net_flip": net_flip},
        "cited_doc_ids_changed": cited_changed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    result = compare(_load(args.base), _load(args.after))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    p = result["primary"]
    print("판정:", result["verdict"])
    print("1회차 불일치 케이스:", [m["case_id"] for m in result["first_pass_mismatch"]])
    if p:
        print(f"1차: 적격 {p['eligible_pairs']} · hit {p['base_hits']} → {p['after_hits']} "
              f"(감소 {p['decrease']}, 증가 {p['increase']}, 순감소 {p['net_decrease']})")
    pf = result["pass_fail"]
    print(f"pass→fail {pf['pass_to_fail']} · fail→pass {pf['fail_to_pass']} · 순 {pf['net_flip']}")
    print("인용 집합 변경:", result["cited_doc_ids_changed"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
