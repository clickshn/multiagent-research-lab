"""두 bench 회차를 **케이스별로** 대조해 자연 변동을 잰다 (v1.2-S0b, session-17 §2 #6).

같은 코드·같은 인덱스·캐시 끔으로 두 번 돌린 결과의 차이가 **자연 변동**이다.
temperature 0이어도 vLLM greedy는 비트 단위 결정론을 보장하지 않는다(session-17 §4).
이 폭을 모르면 이후의 어떤 전/후 차이도 효과로 읽을 수 없다 (ADR-025 판정 기준).

`compare_probe_runs.py`와 달리 바이트 동일성을 보지 않는다 — 생성 결과는 원래 흔들린다.
대신 **무엇을 비교하는지를 여기 고정한다.** 비교할 때마다 손으로 고르면 "이번엔 이것은
봐주자"가 늘어난다.

- pass/fail 뒤집힘 (층별 건수 + 케이스 목록)
- 인용 집합(`cited_doc_ids`) 차이
- T(항목 수) · revision · 토큰 · 호출 수 편차
- 위험군 조건부 선택률 (ADR-025) run1 / run2 / 차이

지연은 비교하지 않는다 — 엔드포인트 부하에 따라 흔들리는 값이라 자연 변동이 아니다.

    python scripts/compare_bench_runs.py <run1.json> <run2.json> [--out <compare.json>]

종료 코드: 0 = 대조 완료 · 2 = 파일을 읽을 수 없음 / 케이스 구성이 다름 / 출력 파일이 이미 있음

LLM 호출 0건. 외부 API 호출 0건. 읽기 전용이다(`--out`만 쓴다).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# 두 회차가 같은 조건이어야 비교가 성립한다. 하나라도 다르면 대조하지 않는다.
CONDITION_KEYS: tuple[str, ...] = (
    "golden_set_version",
    "prompt_version",
    "model",
    "embedding_model",
    "corpus_docs",
    "cache_enabled",
    "candidate_score_exposed",
    "max_revisions",
    "top_k",
)


def _verdict(row: dict) -> str:
    passed = row["score"]["passed"]
    return "pass" if passed is True else ("fail" if passed is False else "unscored")


def compare(run1: dict, run2: dict) -> dict:
    mismatched = {
        k: [run1.get(k), run2.get(k)] for k in CONDITION_KEYS if run1.get(k) != run2.get(k)
    }
    if mismatched:
        raise ValueError(f"측정 조건이 다른 두 회차는 대조하지 않는다: {mismatched}")

    rows1 = {r["case_id"]: r for r in run1["rows"]}
    rows2 = {r["case_id"]: r for r in run2["rows"]}
    if set(rows1) != set(rows2):
        raise ValueError(
            f"케이스 구성이 다르다: run1만 {sorted(set(rows1) - set(rows2))}, "
            f"run2만 {sorted(set(rows2) - set(rows1))}"
        )

    flips: list[dict] = []
    flips_by_stratum: Counter = Counter()
    n_by_stratum: Counter = Counter()
    citation_diffs: list[dict] = []
    per_case: list[dict] = []

    for case_id in sorted(rows1):
        a, b = rows1[case_id], rows2[case_id]
        stratum = a.get("stratum") or "?"
        n_by_stratum[stratum] += 1
        va, vb = _verdict(a), _verdict(b)
        if va != vb:
            flips.append({"case_id": case_id, "stratum": stratum, "run1": va, "run2": vb})
            flips_by_stratum[stratum] += 1

        cited_a, cited_b = set(a["score"]["cited_doc_ids"]), set(b["score"]["cited_doc_ids"])
        if cited_a != cited_b:
            citation_diffs.append(
                {
                    "case_id": case_id,
                    "stratum": stratum,
                    "only_run1": sorted(cited_a - cited_b),
                    "only_run2": sorted(cited_b - cited_a),
                }
            )

        tok_a = a["prompt_tokens"] + a["completion_tokens"]
        tok_b = b["prompt_tokens"] + b["completion_tokens"]
        per_case.append(
            {
                "case_id": case_id,
                "stratum": stratum,
                "verdict": [va, vb],
                "topic_count": [a["topic_count"], b["topic_count"]],
                "revisions": [a["revisions"], b["revisions"]],
                "llm_calls": [a["llm_calls"], b["llm_calls"]],
                "tokens": [tok_a, tok_b],
                "completion_tokens": [a["completion_tokens"], b["completion_tokens"]],
                "citations_same": cited_a == cited_b,
            }
        )

    def changed(key: str) -> list[str]:
        return [c["case_id"] for c in per_case if c[key][0] != c[key][1]]

    token_deltas = [abs(c["tokens"][1] - c["tokens"][0]) for c in per_case]
    token_rel = [
        abs(c["tokens"][1] - c["tokens"][0]) / c["tokens"][0] for c in per_case if c["tokens"][0]
    ]

    risk1, risk2 = run1.get("risk_selection") or {}, run2.get("risk_selection") or {}

    def rate_delta(key: str) -> float | None:
        if risk1.get(key) is None or risk2.get(key) is None:
            return None
        return round(risk2[key] - risk1[key], 4)

    return {
        "run1": run1.get("label"),
        "run2": run2.get("label"),
        "condition": {k: run1.get(k) for k in CONDITION_KEYS},
        "n_cases": len(per_case),
        "verdict_flips": {
            "total": len(flips),
            "by_stratum": {
                s: {"flips": flips_by_stratum[s], "n": n_by_stratum[s]}
                for s in sorted(n_by_stratum)
            },
            "cases": flips,
        },
        "citation_set_diffs": {"total": len(citation_diffs), "cases": citation_diffs},
        "topic_count_changed": changed("topic_count"),
        "revisions_changed": changed("revisions"),
        "llm_calls_changed": changed("llm_calls"),
        "tokens_changed": changed("tokens"),
        "token_abs_delta": {
            "max": max(token_deltas, default=0),
            "mean": round(sum(token_deltas) / len(token_deltas), 1) if token_deltas else 0.0,
            "max_relative": round(max(token_rel, default=0.0), 4),
        },
        "risk_selection": {
            "run1": {k: risk1.get(k) for k in ("selected", "eligible", "rate",
                                                "first_pass_selected", "first_pass_eligible",
                                                "first_pass_rate", "missing_items")},
            "run2": {k: risk2.get(k) for k in ("selected", "eligible", "rate",
                                                "first_pass_selected", "first_pass_eligible",
                                                "first_pass_rate", "missing_items")},
            "rate_delta": rate_delta("rate"),
            "first_pass_rate_delta": rate_delta("first_pass_rate"),
        },
        "per_case": per_case,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run1", type=Path)
    parser.add_argument("run2", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        run1 = json.loads(args.run1.read_text(encoding="utf-8"))
        run2 = json.loads(args.run2.read_text(encoding="utf-8"))
        result = compare(run1, run2)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"대조할 수 없습니다: {exc}")
        return 2

    flips = result["verdict_flips"]
    print(f"run1 : {args.run1.name}\nrun2 : {args.run2.name}\n케이스: {result['n_cases']}건")
    print("\npass/fail 뒤집힘 (층별):")
    for stratum, b in flips["by_stratum"].items():
        print(f"  층 {stratum}: {b['flips']}건 (n={b['n']})")
    for f in flips["cases"]:
        print(f"    {f['case_id']} (층{f['stratum']}) {f['run1']} -> {f['run2']}")
    print(f"\n인용 집합이 다른 케이스: {result['citation_set_diffs']['total']}건")
    for c in result["citation_set_diffs"]["cases"]:
        print(f"    {c['case_id']} run1만 {c['only_run1']} / run2만 {c['only_run2']}")
    print(f"T 변화: {result['topic_count_changed']}")
    print(f"revision 변화: {result['revisions_changed']}")
    print(f"호출 수 변화: {result['llm_calls_changed']}")
    print(f"토큰 변화: {len(result['tokens_changed'])}건, |Δ| {result['token_abs_delta']}")
    risk = result["risk_selection"]
    print(
        f"\n위험군 조건부 선택률 (ADR-025): run1 {risk['run1']['selected']}/{risk['run1']['eligible']}"
        f" · run2 {risk['run2']['selected']}/{risk['run2']['eligible']} · Δ {risk['rate_delta']}"
        f"\n  1회차만: run1 {risk['run1']['first_pass_selected']}/{risk['run1']['first_pass_eligible']}"
        f" · run2 {risk['run2']['first_pass_selected']}/{risk['run2']['first_pass_eligible']}"
        f" · Δ {risk['first_pass_rate_delta']}"
    )

    if args.out is not None:
        if args.out.exists():
            print(f"\n출력 파일이 이미 있습니다: {args.out}. 덮어쓰지 않습니다.")
            return 2
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n저장: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
