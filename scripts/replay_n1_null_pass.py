"""v1.2-N1 1단계 — T3a 선택값 그대로 null-통과 검색 재생 (사전 등록 `docs/plans/v1.2-n1-preregistration.md` §4).

**왜 있나.** null-통과 정책(메타 없는 문서는 `tech_domain` 필터를 통과)의 검색 단계 해악을 **LLM 없이** 먼저 잰다.
T3a(`bench-v1.2-t3-tool-on.json`)에 기록된 1회차 선택값과 트레이스 `search_text`·`k`를 그대로 써서 항목마다
세 번 검색한다 — 무필터(적격 판정), strict(T3a 기록과 같아야 한다 = 무결성), null-통과(대상).

- 해악·이득·적격의 정의는 T3a 사전 등록 §3.4 + Amendment 1 그대로다. "기록된 후보" 자리에 null-통과 재생 결과를 둔다.
  집계는 `analyze_t3_selection.harm_metrics`를 그대로 쓴다.
- 추가 기록(관찰): null-통과 top-k에 들어온 메타 없는 문서 수, 층 A 밀어냄(strict top-k에는 정답이 있었는데
  null-통과 top-k에는 없는 항목과 그 위에 들어온 메타 없는 문서), T3a(strict) 대비 해악·이득.
- **관문(§4.5):** 층 A 해악 케이스 ≥ 2 **또는** 층 B 해악 케이스 ≥ 2 → e2e를 하지 않는다.

**양성 대조(§4.4)를 본 재생보다 먼저 돈다.** T3a 입력의 메모리 사본에서 층 A 적격 케이스 2개(케이스 ID 순 앞의 2개)의
1회차 항목 전부와, 적격이 아닌 층 A 항목 1개(대조군)에 호환되지 않는 선택값을 심고 재생한다. 심은 적격 항목이 전부
해악이고, 해악 케이스가 정확히 그 2개이며, 대조군이 해악이 아니고, 관문이 "e2e 하지 않음"이어야 한다.
하나라도 어긋나면 본 재생을 하지 않고 종료 코드 2로 끝낸다.

    python scripts/replay_n1_null_pass.py --out docs/eval/n1-replay-null-pass.json

종료 코드: 0 = 재생 완료(관문 판정은 결과에) · 2 = 대조 불가 · 무결성 위반 · 양성 대조 실패.
LLM 호출 0건. 임베딩은 로컬. 인덱스·T3a 파일·트레이스는 읽기만 한다.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from analyze_t3_selection import (  # noqa: E402
    _TRUNCATED,
    D3_HARM_CASES,
    Uncheckable,
    first_pass_items,
    harm_metrics,
    load_doc_domains,
    read_retrieve_spans,
)

EXIT_OK = 0
EXIT_UNCHECKABLE = 2

PREREGISTRATION = "5122e97"
T3A_RUN = REPO_ROOT / "docs/eval/bench-v1.2-t3-tool-on.json"
GATE_HARM_CASES = D3_HARM_CASES  # 층 A·B 각각 해악 케이스 ≥ 2 → e2e 하지 않음 (사전 등록 §4.5)
PLANT_CASES = 2

# (search_text, k, tech_domain|None, policy) -> [doc_id, ...] (순위 순)
PolicySearcher = Callable[[str, int, "str | None", str], list[str]]


# --- 항목 재생 -----------------------------------------------------------


def replay_item(
    item: dict,
    span: dict,
    *,
    expected: set[str],
    searcher: PolicySearcher,
    meta_less: set[str],
) -> dict:
    """1회차 항목 하나 — 무필터 · strict · null-통과 재생."""
    choice = item.get("tech_domain")
    if choice is None:
        raise Uncheckable(f"도구 기록 없는 항목: {item['topic']} — T3a(도구 on) 회차가 아니다")
    value, outcome = choice.get("searched_with"), choice.get("outcome")

    search_text = (span.get("input") or {}).get("search_text") or ""
    if _TRUNCATED.search(search_text):
        raise Uncheckable(f"잘린 검색어: {item['topic']}")
    k = int(span["input"]["k"])

    recorded = [c["doc_id"] for c in item.get("candidates") or []]
    traced = [c.get("doc_id") for c in (span.get("output") or [])]
    unfiltered = searcher(search_text, k, None, "strict")
    strict = searcher(search_text, k, value, "strict") if value is not None else unfiltered
    null_pass = searcher(search_text, k, value, "null_pass") if value is not None else unfiltered

    integrity: list[str] = []
    if recorded != traced:
        integrity.append("기록 후보 ≠ 트레이스 검색 출력")
    if strict != recorded:
        integrity.append(f"strict 재생({value}) ≠ T3a 기록")
    if item.get("retrieval_error"):
        integrity.append("검색 오류 항목")

    def rank(docs: Sequence[str]) -> int | None:
        return next((i for i, d in enumerate(docs, start=1) if d in expected), None)

    eligible = bool(expected & set(unfiltered))
    kept = bool(expected & set(null_pass))
    kept_strict = bool(expected & set(strict))
    in_strict_not_null = [d for d in null_pass if d not in strict]
    return {
        "item_index": item["item_index"],
        "topic": item["topic"],
        "value": value,
        "outcome": outcome,
        # harm_metrics가 읽는 키 — "기록" 자리에 null-통과 재생을 둔다.
        "filter_survivors": None,
        "eligible": eligible,
        "harm": eligible and not kept,
        "gain": (not eligible) and kept,
        "expected_rank_unfiltered": rank(unfiltered),
        "expected_rank_recorded": rank(null_pass),
        "unfiltered": unfiltered,
        "recorded": null_pass,
        # 관찰
        "expected_rank_strict": rank(strict),
        "strict": strict,
        "strict_harm": eligible and not kept_strict,
        "strict_gain": (not eligible) and kept_strict,
        "meta_less_in_null_pass": [d for d in null_pass if d in meta_less],
        "meta_less_answers_in_null_pass": [d for d in null_pass if d in meta_less and d in expected],
        "pushed_out_by_meta_less": kept_strict and not kept,
        # 정의상 null-통과 = strict + 메타 없는 문서이므로 strict에 없던 문서는 전부 메타 없는 문서여야 한다.
        "entered_vs_strict": in_strict_not_null,
        "integrity": integrity,
    }


def replay_run(
    run: dict,
    *,
    trace_dir: Path,
    searcher: PolicySearcher,
    doc_domains: dict[str, tuple[str, ...]],
) -> dict:
    meta_less = {doc for doc, domains in doc_domains.items() if not domains}
    cases = []
    for row in run["rows"]:
        spans = read_retrieve_spans(trace_dir / f"{row['run_id']}.jsonl")
        expected = set(row["score"]["expected_doc_ids"])
        items = []
        for item in first_pass_items(row):
            span = spans.get((0, int(item["item_index"])))
            if span is None or (span.get("input") or {}).get("topic") != item["topic"]:
                raise Uncheckable(f"{row['case_id']} item {item['item_index']}: 검색 span 짝 없음")
            record = replay_item(item, span, expected=expected, searcher=searcher,
                                 meta_less=meta_less)
            record["planted"] = bool(item.get("_planted"))
            for d in record["entered_vs_strict"]:
                if d not in meta_less:
                    record["integrity"].append(f"strict에 없던 메타 있는 문서 {d}가 null-통과에 들어왔다")
            items.append(record)
        cases.append({
            "case_id": row["case_id"],
            "stratum": row["stratum"],
            "expected_doc_ids": sorted(expected),
            "expected_doc_domains": sorted({d for doc in expected for d in doc_domains.get(doc, ())}),
            "items": items,
        })
    return {"cases": cases, "meta_less_docs": sorted(meta_less)}


# --- 집계 ---------------------------------------------------------------


def strict_view(cases: Sequence[dict]) -> list[dict]:
    """같은 재생을 strict 기준으로 본 사본 — T3a 대비 관찰용."""
    out = copy.deepcopy(list(cases))
    for case in out:
        for it in case["items"]:
            it["harm"], it["gain"] = it["strict_harm"], it["strict_gain"]
            it["recorded"], it["expected_rank_recorded"] = it["strict"], it["expected_rank_strict"]
    return out


def observations(cases: Sequence[dict]) -> dict:
    out = {}
    for stratum in ("A", "B", "C"):
        group = [c for c in cases if c["stratum"] == stratum]
        items = [(c, it) for c in group for it in c["items"]]
        with_meta_less = [(c, it) for c, it in items if it["meta_less_in_null_pass"]]
        m = {
            "first_pass_items": len(items),
            "items_with_meta_less_in_top_k": len(with_meta_less),
            "meta_less_docs_in_top_k_total": sum(len(it["meta_less_in_null_pass"]) for _, it in items),
            "meta_less_answers_in_top_k_total": sum(
                len(it["meta_less_answers_in_null_pass"]) for _, it in items
            ),
        }
        m["meta_less_non_answers_in_top_k_total"] = (
            m["meta_less_docs_in_top_k_total"] - m["meta_less_answers_in_top_k_total"]
        )
        if stratum == "A":
            m["pushed_out_by_meta_less"] = [
                {
                    "case_id": c["case_id"], "item_index": it["item_index"], "value": it["value"],
                    "expected_rank_strict": it["expected_rank_strict"],
                    "expected_rank_unfiltered": it["expected_rank_unfiltered"],
                    "meta_less_above": it["meta_less_in_null_pass"],
                }
                for c, it in items if it["pushed_out_by_meta_less"]
            ]
        out[stratum] = m
    return out


def gate(harm: dict) -> dict:
    a_cases, b_cases = harm["A"]["harm_cases"]["k"], harm["B"]["harm_cases"]["k"]
    reasons = []
    if a_cases >= GATE_HARM_CASES:
        reasons.append(f"층 A 해악 케이스 {a_cases}건 ≥ {GATE_HARM_CASES}")
    if b_cases >= GATE_HARM_CASES:
        reasons.append(f"층 B 해악 케이스 {b_cases}건 ≥ {GATE_HARM_CASES}")
    return {"proceed_to_e2e": not reasons, "reasons": reasons}


def summarize(replayed: dict, *, exclude_planted_integrity: bool = False) -> dict:
    cases = replayed["cases"]
    integrity = [
        f"{c['case_id']} item {it['item_index']}: {p}"
        for c in cases for it in c["items"] for p in it["integrity"]
        if not (exclude_planted_integrity and it["planted"])
    ]
    harm = harm_metrics(cases)
    strict_harm = harm_metrics(strict_view(cases))
    t3a_gain_cases = sorted({g["case_id"] for g in strict_harm["A"]["gain_detail"]})
    null_gain_cases = sorted({g["case_id"] for g in harm["A"]["gain_detail"]})
    return {
        "integrity_problems": integrity,
        "gate": gate(harm) if not integrity else {"proceed_to_e2e": None,
                                                   "reasons": ["무결성 위반 — 관문 판정 안 함"]},
        "harm": harm,
        "strict_harm_t3a_view": strict_harm,
        "t3a_comparison": {
            "A_gain_items": {"strict": strict_harm["A"]["gain_items"], "null_pass": harm["A"]["gain_items"]},
            "A_gain_cases": {"strict": t3a_gain_cases, "null_pass": null_gain_cases,
                             "lost": sorted(set(t3a_gain_cases) - set(null_gain_cases))},
            "B_harm_items": {"strict": strict_harm["B"]["harm_items"], "null_pass": harm["B"]["harm_items"]},
        },
        "observations": observations(cases),
    }


# --- 양성 대조 (§4.4) -----------------------------------------------------


def plant_incompatible(
    run: dict,
    *,
    trace_dir: Path,
    searcher: PolicySearcher,
    doc_domains: dict[str, tuple[str, ...]],
    vocab: Sequence[str],
) -> tuple[dict, dict]:
    """층 A 적격 케이스 2개의 1회차 항목 전부 + 비적격 층 A 항목 1개(대조군)에 호환되지 않는 값을 심는다."""
    planted_run = copy.deepcopy(run)
    eligible_items: dict[str, list[int]] = {}
    non_eligible: list[tuple[str, int]] = []
    for row in sorted(planted_run["rows"], key=lambda r: r["case_id"]):
        if row["stratum"] != "A":
            continue
        spans = read_retrieve_spans(trace_dir / f"{row['run_id']}.jsonl")
        expected = set(row["score"]["expected_doc_ids"])
        for item in first_pass_items(row):
            span = spans[(0, int(item["item_index"]))]
            top = searcher(span["input"]["search_text"], int(span["input"]["k"]), None, "strict")
            if expected & set(top):
                eligible_items.setdefault(row["case_id"], []).append(int(item["item_index"]))
            else:
                non_eligible.append((row["case_id"], int(item["item_index"])))

    targets = sorted(eligible_items)[:PLANT_CASES]
    if len(targets) < PLANT_CASES:
        raise Uncheckable(f"층 A 적격 케이스가 {len(targets)}개뿐이다 — 양성 대조를 만들 수 없다")
    controls = [x for x in non_eligible if x[0] not in targets][:1]
    if not controls:
        raise Uncheckable("비적격 층 A 항목이 없다 — 대조군을 만들 수 없다")

    rows = {r["case_id"]: r for r in planted_run["rows"]}
    planted: dict = {"cases": targets, "eligible_items": [], "control": None, "values": {}}

    def incompatible(case_id: str) -> str:
        domains = {d for doc in rows[case_id]["score"]["expected_doc_ids"]
                   for d in doc_domains.get(doc, ())}
        return next(v for v in sorted(vocab) if v not in domains)

    def plant(case_id: str, item_index: int) -> None:
        value = incompatible(case_id)
        for item in rows[case_id]["items"]:
            if item.get("revision") == 0 and int(item["item_index"]) == item_index:
                item["tech_domain"] = {**item["tech_domain"], "value": value, "searched_with": value,
                                       "outcome": "chosen"}
                item["_planted"] = True
        planted["values"][f"{case_id}#{item_index}"] = value

    for case_id in targets:
        for item in first_pass_items(rows[case_id]):
            plant(case_id, int(item["item_index"]))
        planted["eligible_items"] += [f"{case_id}#{i}" for i in eligible_items[case_id]]
    plant(*controls[0])
    planted["control"] = f"{controls[0][0]}#{controls[0][1]}"
    return planted_run, planted


def check_positive_control(summary: dict, planted: dict) -> list[str]:
    failures = []
    if summary["integrity_problems"]:
        failures.append(f"심지 않은 항목의 무결성 위반: {summary['integrity_problems'][:5]}")
    detail = summary["harm"]["A"]["harm_detail"]
    found = sorted(f"{case_id}#{x['item_index']}" for case_id, xs in detail.items() for x in xs)
    if found != sorted(planted["eligible_items"]):
        failures.append(f"층 A 해악 항목 {found} ≠ 심은 적격 항목 {sorted(planted['eligible_items'])}")
    if sorted(detail) != sorted(planted["cases"]):
        failures.append(f"층 A 해악 케이스 {sorted(detail)} ≠ 심은 케이스 {planted['cases']}")
    if planted["control"] in found:
        failures.append(f"대조군 {planted['control']}이 해악으로 세어졌다")
    if summary["gate"]["proceed_to_e2e"] is not False:
        failures.append(f"관문이 'e2e 하지 않음'이 아니다: {summary['gate']}")
    return failures


# --- 실제 검색 ------------------------------------------------------------


def make_policy_searcher(retriever) -> PolicySearcher:
    cache: dict[tuple, list[str]] = {}

    def search(text: str, k: int, tech_domain: str | None, policy: str) -> list[str]:
        key = (text, k, tech_domain, policy if tech_domain is not None else None)
        if key not in cache:
            extra = {"tech_domain": tech_domain, "filter_policy": policy} if tech_domain else {}
            cache[key] = [c.doc_id for c in retriever.search(text, k=k, **extra)]
        return cache[key]

    return search


# --- 출력 ---------------------------------------------------------------


def print_report(report: dict) -> None:
    s = report["replay"]
    print(f"1단계 재생 · 입력 {report['run_label']} · 사전 등록 {PREREGISTRATION}")
    print(f"메타 없는 문서: {len(report['meta_less_docs'])}건")
    print("양성 대조:", "통과" if report["positive_control"]["passed"] else "실패",
          report["positive_control"]["planted"]["eligible_items"],
          "대조군", report["positive_control"]["planted"]["control"])
    if s is None:
        return
    for p in s["integrity_problems"][:20]:
        print("  무결성:", p)
    for st in ("A", "B"):
        h, t = s["harm"][st], s["strict_harm_t3a_view"][st]
        print(f"층 {st} null-통과 해악: 항목 {h['harm_items']['k']}/{h['harm_items']['n']} · "
              f"케이스 {h['harm_cases']['k']}/{h['harm_cases']['n']} · 이득 {h['gain_items']}"
              f"   (T3a strict: 해악 {t['harm_items']['k']}/{t['harm_items']['n']} · "
              f"케이스 {t['harm_cases']['k']} · 이득 {t['gain_items']})")
        for case_id, xs in h["harm_detail"].items():
            for x in xs:
                print(f"    {case_id} item {x['item_index']} {x['value']!r} 정답 순위 무필터 "
                      f"{x['expected_rank_unfiltered']} → null-통과 {x['expected_rank_recorded']}")
    for st, m in s["observations"].items():
        print(f"층 {st} 관찰: top-k에 메타 없는 문서가 든 항목 {m['items_with_meta_less_in_top_k']}/"
              f"{m['first_pass_items']} · 메타 없는 문서 합 {m['meta_less_docs_in_top_k_total']} "
              f"(정답 {m['meta_less_answers_in_top_k_total']} · 비정답 "
              f"{m['meta_less_non_answers_in_top_k_total']})")
        for x in m.get("pushed_out_by_meta_less", []):
            print(f"    밀어냄 {x['case_id']} item {x['item_index']} {x['value']!r} strict 순위 "
                  f"{x['expected_rank_strict']} → 밖, 위에 든 메타 없는 문서 {x['meta_less_above']}")
    print("T3a 대비 층 A 이득 케이스:", s["t3a_comparison"]["A_gain_cases"])
    g = s["gate"]
    print("관문:", {True: "통과 → 2단계(e2e)", False: "e2e 하지 않음", None: "판정 안 함"}[g["proceed_to_e2e"]],
          g["reasons"])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, default=T3A_RUN, help="T3a bench 결과 JSON")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    from src.providers.config import load_tracing_settings  # noqa: PLC0415
    from src.tools.retrieval import ChromaRetriever  # noqa: PLC0415

    trace_dir = load_tracing_settings().local_trace_dir
    retriever = ChromaRetriever()
    searcher = make_policy_searcher(retriever)
    doc_domains = load_doc_domains(retriever)
    run = json.loads(args.run.read_text(encoding="utf-8"))
    if run.get("tech_domain_tool") is not True or (run.get("filter_policy") or "strict") != "strict":
        print("대조 불가: 입력이 T3a(도구 on · strict) 회차가 아니다")
        return EXIT_UNCHECKABLE
    started = time.perf_counter()

    try:
        # 1) 양성 대조 — 본 재생보다 먼저 (§4.4)
        planted_run, planted = plant_incompatible(
            run, trace_dir=trace_dir, searcher=searcher, doc_domains=doc_domains,
            vocab=sorted(retriever.tech_domain_vocab()),
        )
        pc_summary = summarize(
            replay_run(planted_run, trace_dir=trace_dir, searcher=searcher, doc_domains=doc_domains),
            exclude_planted_integrity=True,
        )
        failures = check_positive_control(pc_summary, planted)
        report: dict = {
            "preregistration": PREREGISTRATION,
            "run_label": run.get("label"),
            "run_file": args.run.name,
            "meta_less_docs": None,
            "positive_control": {"planted": planted, "failures": failures, "passed": not failures,
                                 "gate": pc_summary["gate"],
                                 "harm_A": pc_summary["harm"]["A"]["harm_items"]},
            "replay": None,
        }
        # 2) 본 재생 — 양성 대조가 통과했을 때만
        if not failures:
            replayed = replay_run(run, trace_dir=trace_dir, searcher=searcher, doc_domains=doc_domains)
            report["meta_less_docs"] = replayed["meta_less_docs"]
            report["replay"] = summarize(replayed)
            report["cases"] = replayed["cases"]
    except Uncheckable as exc:
        print("대조 불가:", exc)
        return EXIT_UNCHECKABLE

    report["elapsed_s"] = round(time.perf_counter() - started, 1)
    if report["meta_less_docs"] is None:
        report["meta_less_docs"] = sorted(d for d, v in doc_domains.items() if not v)
    print_report(report)
    for f in failures:
        print("  양성 대조 실패:", f)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("→", args.out)
    if failures or (report["replay"] and report["replay"]["integrity_problems"]):
        return EXIT_UNCHECKABLE
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
