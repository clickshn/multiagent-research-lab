"""v1.2-P1 판정 — 노드 안 동시 호출 off/on 동시간 대조 (ADR-026).

**측정 전에 커밋한다.** 무엇을 결함으로, 무엇을 변동으로 셀지를 결과를 보기 전에 고정한다.

두 층으로 나눈다.

1. **로직** — Outliner 출력(1회차 항목 목록)이 off와 같은 케이스에 한해, 1회차 Researcher
   호출의 입력 해시(`items[].input_hash`, revision 0)가 **전부 같아야** 한다. 하나라도 다르면
   **구현 결함**이다(종료 코드 1). 후보 문서 목록도 같이 본다 — 입력 해시가 후보를 담으므로
   후보만 다르고 해시가 같을 수는 없다.
   Outliner 출력이 다른 케이스는 **시간 간 변동**으로 따로 센다(로직 판정 대상 아님).
2. **출력** — 입력이 같은 케이스에서 인용 집합·pass/fail·1회차 supporting이 다르면
   **배칭 비결정성**으로 분류한다. 동시 요청은 서버 배칭을 바꿔 greedy 출력이 달라질 수 있다.
   비교 기준으로 off ↔ S0c(시간 간, 같은 순차 코드)를 같은 지표로 나란히 둔다.

지연은 **off/on 두 회차로만** 비교한다(같은 세션, 연달아 실행). S0c와는 시각이 달라 서버
부하 조건이 다르다. 워밍업은 bench가 이미 제외한다.

    python scripts/compare_p1_concurrency.py <off.json> <on.json> --s0c <s0c.json> [--out <p1.json>]

종료 코드: 0 = 로직 동일 · 1 = 로직 결함(입력 해시 불일치) · 2 = 입력 오류 / 조건 불일치 / 출력 파일 있음

LLM 호출 0건. 외부 API 호출 0건. 읽기 전용이다(`--out`만 쓴다).
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.compare_bench_runs import CONDITION_KEYS, compare  # noqa: E402


def _first_pass(row: dict) -> list[dict]:
    items = row.get("items")
    if items is None:
        raise ValueError(f"{row['case_id']}: items 없음 (--require-trace로 잰 결과가 아니다)")
    return [i for i in items if i["revision"] == 0]


def _outline(row: dict) -> list[str]:
    return [i["topic"] for i in _first_pass(row)]


def _nearest_rank(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def _latency(run: dict) -> dict:
    walls = [r["wall_clock_s"] for r in run["rows"]]
    return {
        "n": len(walls),
        "p50_s": round(_nearest_rank(walls, 50), 3),
        "p95_s": round(_nearest_rank(walls, 95), 3),
        "mean_s": round(statistics.fmean(walls), 3),
        "max_s": round(max(walls), 3),
        "bench_wall_clock_s": run.get("bench_wall_clock_s"),
    }


def _calls(run: dict) -> dict:
    rows = run["rows"]
    topics = sum(r["topic_count"] for r in rows)
    calls = sum(r["llm_calls"] for r in rows)
    return {
        "total": calls,
        "by_node": run["calls"]["by_node"],
        "per_item_mean": round(calls / topics, 3) if topics else None,
        "per_item_p50": run["calls"]["per_item"].get("p50"),
        "per_item_p95": run["calls"]["per_item"].get("p95"),
        "billed_tokens": run["tokens"]["billed_total"],
        "prompt_tokens": run["tokens"]["prompt_total"],
        "completion_tokens": run["tokens"]["completion_total"],
    }


def logic_layer(off: dict, on: dict) -> dict:
    rows_off = {r["case_id"]: r for r in off["rows"]}
    rows_on = {r["case_id"]: r for r in on["rows"]}
    same_outline: list[str] = []
    outline_changed: list[dict] = []
    defects: list[dict] = []
    compared_calls = 0

    for case_id in sorted(rows_off):
        a, b = rows_off[case_id], rows_on[case_id]
        if _outline(a) != _outline(b):
            outline_changed.append({"case_id": case_id, "off": _outline(a), "on": _outline(b)})
            continue
        same_outline.append(case_id)
        for ia, ib in zip(_first_pass(a), _first_pass(b), strict=True):
            cand_a = [c["doc_id"] for c in ia["candidates"]]
            cand_b = [c["doc_id"] for c in ib["candidates"]]
            if ia.get("input_hash") is not None or ib.get("input_hash") is not None:
                compared_calls += 1
            if ia.get("input_hash") != ib.get("input_hash") or cand_a != cand_b:
                defects.append({
                    "case_id": case_id, "topic": ia["topic"],
                    "hash_off": ia.get("input_hash"), "hash_on": ib.get("input_hash"),
                    "candidates_same": cand_a == cand_b,
                })

    missing = [
        case_id for case_id in same_outline
        for i in _first_pass(rows_off[case_id]) + _first_pass(rows_on[case_id])
        if i["candidates"] and not i.get("input_hash")
    ]
    if missing:
        raise ValueError(f"입력 해시가 없는 1회차 호출이 있다 (ADR-026 이전 결과?): {sorted(set(missing))}")

    return {
        "cases_same_outline": len(same_outline),
        "first_pass_calls_compared": compared_calls,
        "defects": defects,
        "logic_identical": not defects,
        "time_variation_outline_changed": {"total": len(outline_changed), "cases": outline_changed},
    }


def output_layer(base: dict, after: dict, *, only: set[str] | None = None) -> dict:
    """인용 집합 · pass/fail · 1회차 supporting 차이. `only`가 있으면 그 케이스만 센다."""
    result = compare(base, after)
    keep = (lambda c: c in only) if only is not None else (lambda c: True)
    flips = [f for f in result["verdict_flips"]["cases"] if keep(f["case_id"])]
    cites = [c for c in result["citation_set_diffs"]["cases"] if keep(c["case_id"])]

    rows_a = {r["case_id"]: r for r in base["rows"]}
    rows_b = {r["case_id"]: r for r in after["rows"]}
    supporting_diffs: list[dict] = []
    compared = 0
    for case_id in sorted(rows_a):
        if not keep(case_id) or _outline(rows_a[case_id]) != _outline(rows_b[case_id]):
            continue
        for ia, ib in zip(_first_pass(rows_a[case_id]), _first_pass(rows_b[case_id]), strict=True):
            if [c["doc_id"] for c in ia["candidates"]] != [c["doc_id"] for c in ib["candidates"]]:
                continue  # 입력이 다르면 출력 차이를 비결정성으로 셀 수 없다
            compared += 1
            if ia["supporting"] != ib["supporting"]:
                supporting_diffs.append({"case_id": case_id, "topic": ia["topic"],
                                         "base": ia["supporting"], "after": ib["supporting"]})

    changed_cases = sorted({f["case_id"] for f in flips} | {c["case_id"] for c in cites}
                           | {d["case_id"] for d in supporting_diffs})
    return {
        "base": base.get("label"),
        "after": after.get("label"),
        "n_cases": len(only) if only is not None else result["n_cases"],
        "verdict_flips": {"total": len(flips), "cases": flips},
        "citation_set_diffs": {"total": len(cites), "cases": cites},
        "first_pass_supporting_diffs": {"total": len(supporting_diffs),
                                        "compared_items": compared, "items": supporting_diffs},
        "cases_with_any_output_diff": changed_cases,
        "revisions_changed": [c for c in result["revisions_changed"] if keep(c)],
        "llm_calls_changed": [c for c in result["llm_calls_changed"] if keep(c)],
    }


def judge(off: dict, on: dict, s0c: dict | None) -> dict:
    for label, run, expect in (("off", off, False), ("on", on, True)):
        if run.get("parallel") is not expect:
            raise ValueError(f"{label} 회차의 parallel={run.get('parallel')!r} (기대 {expect})")
    logic = logic_layer(off, on)
    same_input = {
        c for c in (r["case_id"] for r in off["rows"])
        if c not in {x["case_id"] for x in logic["time_variation_outline_changed"]["cases"]}
        and c not in {d["case_id"] for d in logic["defects"]}
    }
    batching = output_layer(off, on, only=same_input)
    return {
        "condition": {k: off.get(k) for k in CONDITION_KEYS},
        "concurrency": {
            "off": {k: off.get(k) for k in ("parallel", "max_concurrency", "effective_concurrency")},
            "on": {k: on.get(k) for k in ("parallel", "max_concurrency", "effective_concurrency")},
        },
        "run_order": [
            {"label": off.get("label"), "generated_at": off.get("generated_at")},
            {"label": on.get("label"), "generated_at": on.get("generated_at")},
        ],
        "logic": logic,
        "batching_nondeterminism": batching,
        "time_variation_off_vs_s0c": output_layer(s0c, off) if s0c is not None else None,
        "latency": {"off": _latency(off), "on": _latency(on),
                    "note": "wall_clock_s (케이스 1건 전체), 워밍업 제외. S0c와는 비교하지 않는다."},
        "paired_speedup": _paired_speedup(off, on),
        "calls": {"off": _calls(off), "on": _calls(on),
                  "s0c": _calls(s0c) if s0c is not None else None},
        "endpoint_errors": {"off": off.get("endpoint_errors"), "on": on.get("endpoint_errors")},
    }


def _paired_speedup(off: dict, on: dict) -> dict:
    walls_off = {r["case_id"]: r["wall_clock_s"] for r in off["rows"]}
    ratios = sorted(
        r["wall_clock_s"] / walls_off[r["case_id"]] for r in on["rows"] if walls_off[r["case_id"]]
    )
    return {
        "on_over_off_p50": round(_nearest_rank(ratios, 50), 3),
        "on_over_off_min": round(ratios[0], 3),
        "on_over_off_max": round(ratios[-1], 3),
        "n": len(ratios),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("off", type=Path)
    parser.add_argument("on", type=Path)
    parser.add_argument("--s0c", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.out is not None and args.out.exists():
        print(f"[거부] 출력 파일이 이미 있다: {args.out}")
        return 2
    try:
        load = lambda p: json.loads(p.read_text(encoding="utf-8"))  # noqa: E731
        result = judge(load(args.off), load(args.on), load(args.s0c) if args.s0c else None)
    except (OSError, ValueError, KeyError) as exc:
        print(f"[거부] {exc}")
        return 2

    logic = result["logic"]
    print(f"로직: 같은 Outliner {logic['cases_same_outline']}건 · 1회차 호출 "
          f"{logic['first_pass_calls_compared']}건 대조 · 결함 {len(logic['defects'])}건 · "
          f"Outliner 변동 {logic['time_variation_outline_changed']['total']}건")
    b = result["batching_nondeterminism"]
    print(f"배칭 비결정성(off↔on, 입력 동일 {b['n_cases']}건): 케이스 "
          f"{len(b['cases_with_any_output_diff'])} · 인용 {b['citation_set_diffs']['total']} · "
          f"pass/fail {b['verdict_flips']['total']} · 1회차 supporting "
          f"{b['first_pass_supporting_diffs']['total']}/{b['first_pass_supporting_diffs']['compared_items']}")
    if result["time_variation_off_vs_s0c"]:
        t = result["time_variation_off_vs_s0c"]
        print(f"시간 간(S0c↔off): 케이스 {len(t['cases_with_any_output_diff'])} · 인용 "
              f"{t['citation_set_diffs']['total']} · pass/fail {t['verdict_flips']['total']} · "
              f"1회차 supporting {t['first_pass_supporting_diffs']['total']}/"
              f"{t['first_pass_supporting_diffs']['compared_items']}")
    lat = result["latency"]
    print(f"지연 p50/p95: off {lat['off']['p50_s']}/{lat['off']['p95_s']}s · "
          f"on {lat['on']['p50_s']}/{lat['on']['p95_s']}s (n={lat['on']['n']})")
    print(f"오류: off {result['endpoint_errors']['off']} · on {result['endpoint_errors']['on']}")

    if args.out is not None:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print(f"저장: {args.out}")
    return 0 if logic["logic_identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
