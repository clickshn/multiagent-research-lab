"""v1.2-T3 판정 — 에이전트 `tech_domain` 선택의 해악·일치·기권·e2e (사전 등록 `ce86b04`).

**왜 있나.** 사전 등록(`docs/plans/v1.2-preregistration.md`)의 지표와 판정 규칙(§3·§6)을
**본측정 전에** 코드로 고정한다. 판정은 이 스크립트의 출력으로만 한다.

**해악의 측정 (§3.4).** LLM을 다시 부르지 않는다. 대상 회차 트레이스의 `researcher_retrieve`
span에서 실제 검색어(`search_text`)를 꺼내 **무필터로 재생**하고(`ChromaRetriever.search`, 같은 k),
그 top-k에 정답 문서(`expected_doc_ids`)가 있으면 **적격**, 적격인데 **기록된(필터 후) 후보**에
정답이 없으면 **해악**이다. 무필터 top-k에 정답이 없는데 기록된 후보에 있으면 **이득**(기록만).
대상은 1회차(revision 0) 항목뿐이다.

**무결성 (판정 이전).**
- 기록된 후보(bench JSON) = 트레이스 `researcher_retrieve` 출력 (문서 ID·순서)
- 도구가 고른 값으로 검색한 항목은 **필터 검색도 재생**해 기록과 대조한다. 무필터 항목은 무필터
  재생이 곧 기록과의 대조다. 검색은 결정적이다(session-21 §5) — 어긋나면 인덱스나 기록이 바뀐 것이다.
- `cache_enabled` · `attribution_problems` · `selection_error` 누적 · 기준 회차 parallel 확인

    python scripts/analyze_t3_selection.py <대상 bench JSON> \\
        --baseline docs/eval/bench-v1.2-p1-on.json --out <결과 JSON>
    python scripts/analyze_t3_selection.py --positive-control \\
        --baseline docs/eval/bench-v1.2-p1-on.json --out <결과 JSON>

`--filter-policy {strict,null-pass}` (v1.2-N1): 필터 항목의 **재생 무결성 대조**에 쓸 정책이다. 기본 strict(T3a).
대상 회차에 기록된 `filter_policy`(없으면 strict)와 다르면 측정 무효다. **지표 정의와 임계값은 정책과 무관하게
같다** — 해악·이득은 여전히 "무필터 재생 top-k 대 기록된 후보"다. `--preregistration`은 출력에 적을 사전 등록 해시다.

`--positive-control`: 기준 회차의 **변조 사본**(층 A 해악 2케이스 · 층 B e2e −2 · 층 C 하락 1을 심음)을
메모리에서 만들어 분석하고, 심은 것이 전부 검출되는지 확인한다. 검출되지 않으면 종료 코드 2.

종료 코드: 0 = 분석 완료(판정은 결과에) · 2 = 대조 불가 · 양성 대조 실패.
LLM 호출 0건. 임베딩은 로컬. 인덱스는 읽기만 한다.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

EXIT_OK = 0
EXIT_UNCHECKABLE = 2

ORACLE_FILE = REPO_ROOT / "docs/eval/probe-retrieval-v1.2-t1-tech-domain-strict-retrieval.json"
ABSTAIN = "없음"
PREREGISTRATION = "ce86b04"
_TRUNCATED = re.compile(r"\.\.\.<\d+chars>$")

# 사전 등록 §6 — 임계값. 바꾸려면 사전 등록 Amendment가 먼저다.
D3_HARM_CASES = 2          # 층 A 해악 케이스 ≥ 2 = 해악 (1 = 관찰)
D4_B_DROP = 2              # 층 B e2e −2건 이상 = 해악 (−1 = 관찰)
D5_C_DROP = 1              # 층 C 근거 없음 유지 1건이라도 하락 = 해악
D7_A_GAINS = 2             # 층 A fail→pass ≥ 2 & 해악 아님 = 개선 관찰
SELECTION_ERROR_STOP = 3   # §4

# (search_text, k, tech_domain|None) -> [doc_id, ...] (순위 순)
Searcher = Callable[[str, int, "str | None"], list[str]]


class Uncheckable(RuntimeError):
    """대조할 수 없다 — 트레이스 없음 · 잘린 검색어 · 짝 불일치."""


# --- 입력 ---------------------------------------------------------------


def load_oracle(path: Path = ORACLE_FILE) -> dict[str, str]:
    """층 A 케이스별 오라클 값 (사전 등록 §2) — T1 프로브의 `filter.value`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        case["id"]: case["filter"]["value"]
        for case in data["cases"]
        if (case.get("filter") or {}).get("value")
    }


def read_retrieve_spans(trace_path: Path) -> dict[tuple[int, int], dict]:
    """(revision, item_index) → `researcher_retrieve` span. 키 중복은 대조 불가."""
    if not trace_path.exists():
        raise Uncheckable(f"트레이스 없음: {trace_path.name}")
    spans: dict[tuple[int, int], dict] = {}
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") != "span" or event.get("name") != "researcher_retrieve":
            continue
        fields = event.get("input") or {}
        key = (int(fields["revision"]), int(fields["item_index"]))
        if key in spans:
            raise Uncheckable(f"{trace_path.name}: 검색 span 키 중복 {key}")
        spans[key] = event
    return spans


def first_pass_items(row: dict) -> list[dict]:
    items = row.get("items")
    if items is None:
        raise Uncheckable(f"{row['case_id']}: 항목 기록 없음")
    return sorted((it for it in items if it.get("revision") == 0), key=lambda it: it["item_index"])


# --- 항목 분석 -----------------------------------------------------------


def analyze_item(
    item: dict,
    span: dict,
    *,
    expected: set[str],
    searcher: Searcher,
) -> dict:
    """1회차 항목 하나 — 선택값, 무필터 재생, 해악/이득, 무결성."""
    choice = item.get("tech_domain")  # 도구 off 회차(p1-on)에는 키가 없다
    if choice is None:
        value, outcome, searched_with = None, "tool_off", None
    else:
        value, outcome, searched_with = (
            choice.get("value"), choice.get("outcome"), choice.get("searched_with")
        )

    search_text = (span.get("input") or {}).get("search_text") or ""
    if _TRUNCATED.search(search_text):
        raise Uncheckable(f"잘린 검색어: {item['topic']}")
    k = int(span["input"]["k"])

    recorded = [c["doc_id"] for c in item.get("candidates") or []]
    traced = [c.get("doc_id") for c in (span.get("output") or [])]
    integrity: list[str] = []
    if recorded != traced:
        integrity.append("기록 후보 ≠ 트레이스 검색 출력")

    unfiltered = searcher(search_text, k, None)
    if searched_with is None:
        if unfiltered != recorded:
            integrity.append("무필터 항목인데 무필터 재생 ≠ 기록")
    else:
        filtered = searcher(search_text, k, searched_with)
        if filtered != recorded:
            integrity.append(f"필터({searched_with}) 재생 ≠ 기록")
    if item.get("retrieval_error"):
        integrity.append("검색 오류 항목")

    eligible = bool(expected & set(unfiltered))
    kept = bool(expected & set(recorded))

    def rank(docs: Sequence[str]) -> int | None:
        return next((i for i, d in enumerate(docs, start=1) if d in expected), None)

    return {
        "item_index": item["item_index"],
        "topic": item["topic"],
        "value": value,
        "outcome": outcome,
        "searched_with": searched_with,
        "filter_survivors": (choice or {}).get("filter_survivors"),
        "eligible": eligible,
        "harm": eligible and not kept,
        "gain": (not eligible) and kept,
        "expected_rank_unfiltered": rank(unfiltered),
        "expected_rank_recorded": rank(recorded),
        "unfiltered": unfiltered,
        "recorded": recorded,
        "integrity": integrity,
    }


def analyze_run(
    run: dict,
    *,
    trace_dir: Path,
    searcher: Searcher,
    doc_domains: dict[str, tuple[str, ...]],
    oracle: dict[str, str],
) -> dict:
    """회차 하나의 케이스별·항목별 기록."""
    cases = []
    for row in run["rows"]:
        spans = read_retrieve_spans(trace_dir / f"{row['run_id']}.jsonl")
        expected = set(row["score"]["expected_doc_ids"])
        domains = sorted({d for doc in expected for d in doc_domains.get(doc, ())})
        items = []
        for item in first_pass_items(row):
            span = spans.get((0, int(item["item_index"])))
            if span is None or (span.get("input") or {}).get("topic") != item["topic"]:
                raise Uncheckable(f"{row['case_id']} item {item['item_index']}: 검색 span 짝 없음")
            record = analyze_item(item, span, expected=expected, searcher=searcher)
            record["exact"] = (
                record["value"] == oracle[row["case_id"]] if row["case_id"] in oracle else None
            )
            record["compatible"] = (
                record["value"] in domains if row["stratum"] == "A" and domains else None
            )
            items.append(record)
        cases.append({
            "case_id": row["case_id"],
            "stratum": row["stratum"],
            "passed": row["score"]["passed"],
            "negative_case_held": row["score"]["checks"].get("negative_case_held"),
            "oracle": oracle.get(row["case_id"]),
            "expected_doc_ids": sorted(expected),
            "expected_doc_domains": domains,
            "topics": [it["topic"] for it in items],
            "selection_errors": int(row.get("selection_errors") or 0),
            "items": items,
        })
    return {"label": run.get("label"), "cases": cases}


# --- 지표 (사전 등록 §3) ---------------------------------------------------


def _ratio(k: int, n: int) -> dict:
    return {"k": k, "n": n, "pct": round(100 * k / n, 1) if n else None}


def selection_metrics(cases: Sequence[dict]) -> dict:
    """층별 선택 지표. `selection_error` 항목은 분자·분모에서 뺀다(§3.3)."""
    out: dict[str, dict] = {}
    by_stratum: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        by_stratum[case["stratum"]].append(case)
    for stratum, group in sorted(by_stratum.items()):
        items = [(c, it) for c in group for it in c["items"]]
        valid = [(c, it) for c, it in items if it["outcome"] != "selection_error"]
        tool_on = any(it["outcome"] != "tool_off" for _, it in items)
        m: dict = {
            "cases": len(group),
            "first_pass_items": len(items),
            "selection_error_items": len(items) - len(valid),
            "selection_error_cases": sorted(
                {c["case_id"] for c, it in items if it["outcome"] == "selection_error"}
            ),
            "tool_on": tool_on,
        }
        if tool_on:
            m["abstain"] = _ratio(sum(it["outcome"] == "abstain" for _, it in valid), len(valid))
            m["value_counts"] = dict(Counter(it["value"] or ABSTAIN for _, it in valid).most_common())
        if stratum == "A":
            for key in ("exact", "compatible"):
                scored = [(c, it) for c, it in valid if it[key] is not None]
                per_case: dict[str, list[bool]] = defaultdict(list)
                for c, it in scored:
                    per_case[c["case_id"]].append(bool(it[key]))
                majority = sum(sum(v) * 2 > len(v) for v in per_case.values())
                m[key] = {
                    "items_pooled": _ratio(sum(bool(it[key]) for _, it in scored), len(scored)),
                    "case_majority_observation": _ratio(majority, len(per_case)),
                }
            # 호환이 아닌데 기권도 아닌 항목 — 해악이 날 수 있는 자리(§3.2)
            m["incompatible_non_abstain"] = [
                {"case_id": c["case_id"], "item_index": it["item_index"], "value": it["value"],
                 "expected_doc_domains": c["expected_doc_domains"]}
                for c, it in valid
                if it["compatible"] is False and it["outcome"] == "chosen"
            ]
        out[stratum] = m
    return out


def harm_metrics(cases: Sequence[dict]) -> dict:
    """층 A·B 해악·이득 (§3.4). `selection_error` 항목은 제외."""
    out = {}
    for stratum in ("A", "B"):
        group = [c for c in cases if c["stratum"] == stratum]
        eligible_items = harm_items = gain_items = 0
        eligible_cases: set[str] = set()
        harm_cases: dict[str, list[dict]] = defaultdict(list)
        gains = []
        for case in group:
            for it in case["items"]:
                if it["outcome"] == "selection_error":
                    continue
                if it["eligible"]:
                    eligible_items += 1
                    eligible_cases.add(case["case_id"])
                if it["harm"]:
                    harm_items += 1
                    harm_cases[case["case_id"]].append({
                        "item_index": it["item_index"], "topic": it["topic"],
                        "value": it["value"], "outcome": it["outcome"],
                        "filter_survivors": it["filter_survivors"],
                        "expected_rank_unfiltered": it["expected_rank_unfiltered"],
                        "expected_rank_recorded": it["expected_rank_recorded"],
                        "expected_doc_domains": case["expected_doc_domains"],
                        "unfiltered": it["unfiltered"], "recorded": it["recorded"],
                    })
                if it["gain"]:
                    gain_items += 1
                    gains.append({"case_id": case["case_id"], "item_index": it["item_index"],
                                  "value": it["value"],
                                  "expected_rank_recorded": it["expected_rank_recorded"]})
        out[stratum] = {
            "harm_items": _ratio(harm_items, eligible_items),
            "harm_cases": _ratio(len(harm_cases), len(eligible_cases)),
            "eligible_items": eligible_items,
            "eligible_cases": sorted(eligible_cases),
            "harm_detail": dict(sorted(harm_cases.items())),
            "gain_items": gain_items,
            "gain_detail": gains,
        }
    return out


def e2e_metrics(cases: Sequence[dict], baseline_cases: Sequence[dict]) -> dict:
    """층별 e2e pass/fail 건수와 뒤집힘 (§3.5). 층 B는 절대 차이."""
    base = {c["case_id"]: c for c in baseline_cases}
    out = {}
    for stratum in ("A", "B", "C"):
        group = [c for c in cases if c["stratum"] == stratum]
        missing = [c["case_id"] for c in group if c["case_id"] not in base]
        if missing:
            raise Uncheckable(f"기준 회차에 없는 케이스: {missing}")
        passed = sum(bool(c["passed"]) for c in group)
        base_passed = sum(bool(base[c["case_id"]]["passed"]) for c in group)
        m = {
            "n": len(group), "passed": passed, "baseline_passed": base_passed,
            "delta": passed - base_passed,
            "pass_to_fail": sorted(c["case_id"] for c in group
                                   if base[c["case_id"]]["passed"] and not c["passed"]),
            "fail_to_pass": sorted(c["case_id"] for c in group
                                   if not base[c["case_id"]]["passed"] and c["passed"]),
        }
        if stratum == "C":
            held = sum(bool(c["negative_case_held"]) for c in group)
            base_held = sum(bool(base[c["case_id"]]["negative_case_held"]) for c in group)
            m["negative_held"] = {"held": held, "baseline_held": base_held,
                                  "drop": base_held - held}
        out[stratum] = m
    return out


def outline_drift(cases: Sequence[dict], baseline_cases: Sequence[dict]) -> list[str]:
    """1회차 Outliner 항목이 기준과 다른 케이스 (§4 — 기록·표시만, 계산은 막지 않는다)."""
    base = {c["case_id"]: c["topics"] for c in baseline_cases}
    return sorted(c["case_id"] for c in cases if base.get(c["case_id"]) != c["topics"])


def cost_deltas(run: dict, baseline: dict) -> dict:
    """호출·토큰·지연 증가분 — 기록만 한다(§6.1, 판정에 쓰지 않음)."""
    def pick(bench: dict) -> dict:
        return {
            "calls_total": bench["calls"]["total"],
            "calls_by_node": bench["calls"]["by_node"],
            "calls_per_request_mean": bench["calls"]["per_request"]["mean"],
            "prompt_tokens": bench["tokens"]["prompt_total"],
            "completion_tokens": bench["tokens"]["completion_total"],
            "billed_tokens": bench["tokens"]["billed_total"],
            "wall_mean_s": bench["latency"]["wall"]["mean"],
            "wall_p50_s": bench["latency"]["wall"]["p50"],
            "wall_p95_s": bench["latency"]["wall"]["p95"],
            "llm_mean_s": bench["latency"]["llm"]["mean"],
            "retrieval_mean_s": bench["latency"]["retrieval"]["mean"],
            "bench_wall_clock_s": bench.get("bench_wall_clock_s"),
            "revision_distribution": bench.get("revision_distribution"),
        }
    now, base = pick(run), pick(baseline)
    delta = {
        key: round(now[key] - base[key], 3)
        for key in now
        if isinstance(now[key], (int, float)) and isinstance(base[key], (int, float))
    }
    nodes = set(now["calls_by_node"]) | set(base["calls_by_node"])
    delta["calls_by_node"] = {
        n: now["calls_by_node"].get(n, 0) - base["calls_by_node"].get(n, 0) for n in sorted(nodes)
    }

    # 같은 케이스끼리의 행 합계 — 대상이 부분 집합(소표본)이어도 비교가 성립한다.
    wanted = {r["case_id"] for r in run["rows"]}
    def rows_sum(bench: dict) -> dict:
        rows = [r for r in bench["rows"] if r["case_id"] in wanted]
        return {
            "cases": len(rows),
            "llm_calls": sum(r["llm_calls"] for r in rows),
            "billed_tokens": sum(r["billed_tokens"] for r in rows),
            "wall_clock_s": round(sum(r["wall_clock_s"] for r in rows), 3),
        }
    same_now, same_base = rows_sum(run), rows_sum(baseline)
    same = {"run": same_now, "baseline": same_base,
            "delta": {k: round(same_now[k] - same_base[k], 3) for k in same_now if k != "cases"}}
    return {"run": now, "baseline": base, "delta": delta, "same_cases": same}


# --- 판정 (사전 등록 §4 · §6.1) -------------------------------------------


def validity_problems(
    run: dict, baseline: dict, analysis: dict, *, filter_policy: str = "strict"
) -> list[str]:
    problems = []
    recorded_policy = run.get("filter_policy") or "strict"  # 키가 없으면(T3a까지) strict
    if recorded_policy != filter_policy:
        problems.append(f"필터 정책 불일치: 기록 {recorded_policy!r} ≠ 분석 {filter_policy!r}")
    if run.get("cache_enabled") is not False:
        problems.append(f"캐시 켜짐 또는 미기록 (cache_enabled={run.get('cache_enabled')!r})")
    if baseline.get("cache_enabled") is not False:
        problems.append("기준 회차 캐시 켜짐")
    if baseline.get("parallel") is not True:
        problems.append(f"기준 회차 parallel={baseline.get('parallel')!r} (on이어야 한다)")
    for row in run["rows"]:
        for p in row.get("attribution_problems") or []:
            problems.append(f"{row['case_id']} 귀속: {p}")
    errors = sum(c["selection_errors"] for c in analysis["cases"])
    if errors >= SELECTION_ERROR_STOP:
        problems.append(f"selection_error 누적 {errors}건 ≥ {SELECTION_ERROR_STOP}")
    for case in analysis["cases"]:
        for it in case["items"]:
            for p in it["integrity"]:
                problems.append(f"{case['case_id']} item {it['item_index']}: {p}")
    return problems


def verdict(harm: dict, e2e: dict, problems: Sequence[str]) -> dict:
    if problems:
        return {"verdict": "측정 무효", "reasons": list(problems)}

    a_harm_cases = harm["A"]["harm_cases"]["k"]
    b_delta = e2e["B"]["delta"]
    c_drop = e2e["C"]["negative_held"]["drop"]
    harmful = []
    if a_harm_cases >= D3_HARM_CASES:
        harmful.append(f"D3: 층 A 해악 케이스 {a_harm_cases}건 ≥ {D3_HARM_CASES}")
    if b_delta <= -D4_B_DROP:
        harmful.append(f"D4: 층 B e2e {e2e['B']['baseline_passed']}/{e2e['B']['n']} → "
                       f"{e2e['B']['passed']}/{e2e['B']['n']} (Δ {b_delta:+d}건)")
    if c_drop >= D5_C_DROP:
        harmful.append(f"D5: 층 C 근거 없음 유지 {c_drop}건 하락")
    if harmful:
        return {"verdict": "해악", "reasons": harmful}

    observations = []
    if a_harm_cases == 1:
        observations.append(f"층 A 해악 케이스 1건 ({', '.join(harm['A']['harm_detail'])})")
    if b_delta == -1:
        observations.append("층 B e2e −1건")
    if len(e2e["A"]["pass_to_fail"]) == 1:
        observations.append(f"층 A e2e pass→fail 1건 ({e2e['A']['pass_to_fail'][0]})")
    result = {"verdict": "무해 확인", "observations": observations}
    if len(e2e["A"]["fail_to_pass"]) >= D7_A_GAINS:
        result["improvement_observed"] = (
            f"D7: 층 A fail→pass {len(e2e['A']['fail_to_pass'])}건 "
            f"({', '.join(e2e['A']['fail_to_pass'])}) — 효과 입증 아님"
        )
    if len(e2e["A"]["pass_to_fail"]) >= 2:
        # 판정 조건은 D3뿐이다(§6.1). 건수와 케이스는 적는다.
        result["observations"].append(
            f"층 A e2e pass→fail {len(e2e['A']['pass_to_fail'])}건 "
            f"({', '.join(e2e['A']['pass_to_fail'])}) — 판정 조건 아님(D3만 쓴다)"
        )
    return result


def evaluate(
    run: dict,
    baseline: dict,
    *,
    trace_dir: Path,
    searcher: Searcher,
    doc_domains: dict[str, tuple[str, ...]],
    oracle: dict[str, str],
    baseline_analysis: dict | None = None,
    filter_policy: str = "strict",
    preregistration: str = PREREGISTRATION,
) -> dict:
    analysis = analyze_run(run, trace_dir=trace_dir, searcher=searcher,
                           doc_domains=doc_domains, oracle=oracle)
    if baseline_analysis is None:
        baseline_analysis = analyze_run(baseline, trace_dir=trace_dir, searcher=searcher,
                                        doc_domains=doc_domains, oracle=oracle)
    harm = harm_metrics(analysis["cases"])
    e2e = e2e_metrics(analysis["cases"], baseline_analysis["cases"])
    problems = validity_problems(run, baseline, analysis, filter_policy=filter_policy)
    base_eligible = {
        s: harm_metrics(baseline_analysis["cases"])[s]["eligible_items"] for s in ("A", "B")
    }
    return {
        "preregistration": preregistration,
        "filter_policy": filter_policy,
        "run_label": run.get("label"),
        "baseline_label": baseline.get("label"),
        "run_conditions": {k: run.get(k) for k in (
            "tech_domain_tool", "parallel", "max_concurrency", "effective_concurrency",
            "cache_enabled", "prompt_version", "model", "corpus_docs", "index_check",
            "endpoint_errors", "n_cases")},
        "baseline_conditions": {k: baseline.get(k) for k in (
            "tech_domain_tool", "parallel", "max_concurrency", "cache_enabled", "prompt_version")},
        "validity_problems": problems,
        "judgment": verdict(harm, e2e, problems),
        "harm": harm,
        "baseline_eligible_items": base_eligible,
        "selection": selection_metrics(analysis["cases"]),
        "selection_error_total": sum(c["selection_errors"] for c in analysis["cases"]),
        "e2e": e2e,
        "outline_drift_cases": outline_drift(analysis["cases"], baseline_analysis["cases"]),
        "cost": cost_deltas(run, baseline),
        "cases": analysis["cases"],
    }


# --- 양성 대조 -----------------------------------------------------------


def plant_harm(baseline: dict, baseline_analysis: dict) -> tuple[dict, dict]:
    """기준 회차의 변조 사본 — 층 A 해악 2케이스 · 층 B e2e −2 · 층 C 하락 1.

    해악은 **적격 항목의 기록 후보에서 정답 문서를 빼는** 방식으로 심는다(트레이스는 그대로).
    그래서 무결성 검사(기록 ≠ 트레이스)도 그 항목을 잡아야 한다 — 두 검출기를 함께 시험한다.
    """
    tampered = copy.deepcopy(baseline)
    tampered["label"] = f"{baseline.get('label')}-TAMPERED"
    eligible_a = [
        (c["case_id"], it["item_index"])
        for c in baseline_analysis["cases"] if c["stratum"] == "A"
        for it in c["items"] if it["eligible"]
    ]
    planted_cases: list[str] = []
    planted_items: list[tuple[str, int]] = []
    for case_id, index in eligible_a:
        if case_id in planted_cases:
            continue
        planted_cases.append(case_id)
        planted_items.append((case_id, index))
        if len(planted_cases) == 2:
            break
    rows = {r["case_id"]: r for r in tampered["rows"]}
    for case_id, index in planted_items:
        row = rows[case_id]
        expected = set(row["score"]["expected_doc_ids"])
        item = next(it for it in row["items"] if it["revision"] == 0 and it["item_index"] == index)
        item["candidates"] = [c for c in item["candidates"] if c["doc_id"] not in expected]
        item["tech_domain"] = {"value": "Hardware/Chip", "outcome": "chosen", "source": "selected",
                               "searched_with": None, "filter_survivors": None}
    b_flipped = [r["case_id"] for r in tampered["rows"]
                 if r["stratum"] == "B" and r["score"]["passed"]][:2]
    for case_id in b_flipped:
        rows[case_id]["score"]["passed"] = False
    c_flipped = [r["case_id"] for r in tampered["rows"] if r["stratum"] == "C"][:1]
    for case_id in c_flipped:
        rows[case_id]["score"]["passed"] = False
        rows[case_id]["score"]["checks"]["negative_case_held"] = False
    planted = {"A_harm_items": planted_items, "B_pass_to_fail": b_flipped,
               "C_negative_dropped": c_flipped}
    return tampered, planted


def check_positive_control(report: dict, planted: dict) -> list[str]:
    failures = []
    detected = {(cid, h["item_index"]) for cid, hs in report["harm"]["A"]["harm_detail"].items()
                for h in hs}
    for case_id, index in planted["A_harm_items"]:
        if (case_id, index) not in detected:
            failures.append(f"심은 층 A 해악 미검출: {case_id} item {index}")
    if detected - {tuple(x) for x in planted["A_harm_items"]}:
        failures.append(f"심지 않은 해악 검출: {sorted(detected)}")
    if report["e2e"]["B"]["pass_to_fail"] != sorted(planted["B_pass_to_fail"]):
        failures.append(f"층 B 뒤집힘 불일치: {report['e2e']['B']['pass_to_fail']}")
    if report["e2e"]["C"]["negative_held"]["drop"] != len(planted["C_negative_dropped"]):
        failures.append("층 C 하락 미검출")
    flagged = {(p.split(" item ")[0], int(p.split(" item ")[1].split(":")[0]))
               for p in report["validity_problems"] if " item " in p}
    for case_id, index in planted["A_harm_items"]:
        if (case_id, index) not in flagged:
            failures.append(f"무결성 검사가 변조 항목을 못 잡음: {case_id} item {index}")
    # 무결성 문제를 빼고 판정만 다시 내면 세 기준이 모두 걸려야 한다.
    reasons = verdict(report["harm"], report["e2e"], [])
    if reasons["verdict"] != "해악" or len(reasons["reasons"]) != 3:
        failures.append(f"판정이 D3·D4·D5 해악을 모두 내지 않음: {reasons}")
    if report["judgment"]["verdict"] != "측정 무효":
        failures.append("변조 사본이 측정 무효로 판정되지 않음")
    return failures


# --- 실제 검색·메타 --------------------------------------------------------


def make_searcher(retriever, filter_policy: str = "strict") -> Searcher:
    """재생용 검색. `filter_policy`는 필터 값이 있을 때만 쓰인다(무필터 재생은 정책과 무관)."""
    cache: dict[tuple[str, int, str | None], list[str]] = {}

    def search(text: str, k: int, tech_domain: str | None) -> list[str]:
        key = (text, k, tech_domain)
        if key not in cache:
            extra = {"filter_policy": filter_policy} if tech_domain is not None else {}
            cache[key] = [c.doc_id for c in retriever.search(text, k=k, tech_domain=tech_domain,
                                                             **extra)]
        return cache[key]

    return search


def load_doc_domains(retriever) -> dict[str, tuple[str, ...]]:
    from src.tools.retrieval import _split_list  # noqa: PLC0415

    raw = retriever._get_collection(create=False).get(include=["metadatas"])
    domains: dict[str, set[str]] = defaultdict(set)
    for meta in raw.get("metadatas") or []:
        meta = meta or {}
        domains[str(meta.get("doc_id", ""))] |= set(_split_list(meta.get("tech_domains")))
    return {doc: tuple(sorted(v)) for doc, v in domains.items()}


# --- 출력 ---------------------------------------------------------------


def print_report(report: dict) -> None:
    j = report["judgment"]
    print(f"대상 {report['run_label']} · 기준 {report['baseline_label']} · "
          f"사전 등록 {report['preregistration']} · 필터 정책 {report['filter_policy']}")
    print(f"판정: {j['verdict']}")
    for r in j.get("reasons", [])[:20]:
        print("  -", r)
    for o in j.get("observations", []):
        print("  관찰:", o)
    if j.get("improvement_observed"):
        print("  개선 관찰:", j["improvement_observed"])
    for s in ("A", "B"):
        h = report["harm"][s]
        print(f"층 {s} 해악: 항목 {h['harm_items']['k']}/{h['harm_items']['n']} · "
              f"케이스 {h['harm_cases']['k']}/{h['harm_cases']['n']} · 이득 {h['gain_items']} "
              f"(기준 적격 {report['baseline_eligible_items'][s]})")
        for case_id, details in h["harm_detail"].items():
            for x in details:
                print(f"    {case_id} item {x['item_index']} {x['value']!r} "
                      f"생존 {x['filter_survivors']} · 정답 순위 무필터 "
                      f"{x['expected_rank_unfiltered']} → 기록 {x['expected_rank_recorded']}")
    for s in ("A", "B", "C"):
        e = report["e2e"][s]
        line = (f"층 {s} e2e: {e['baseline_passed']}/{e['n']} → {e['passed']}/{e['n']} "
                f"(Δ {e['delta']:+d}) p→f {e['pass_to_fail']} f→p {e['fail_to_pass']}")
        print(line)
    for s, m in report["selection"].items():
        if not m["tool_on"]:
            print(f"층 {s} 선택: 도구 off ({m['first_pass_items']}항목)")
            continue
        extra = ""
        if s == "A":
            extra = (f" · 정확 {m['exact']['items_pooled']['k']}/{m['exact']['items_pooled']['n']}"
                     f" · 호환 {m['compatible']['items_pooled']['k']}/"
                     f"{m['compatible']['items_pooled']['n']}")
        print(f"층 {s} 선택: 기권 {m['abstain']['k']}/{m['abstain']['n']}{extra} · "
              f"selection_error {m['selection_error_items']}")
    print("Outliner 1회차 항목이 기준과 다른 케이스:", report["outline_drift_cases"] or "없음")
    d = report["cost"]["delta"]
    same = report["cost"]["same_cases"]
    print(f"증가분(회차 전체): 호출 {d['calls_total']:+} · 토큰 {d['billed_tokens']:+} · "
          f"wall mean {d['wall_mean_s']:+}s · p95 {d['wall_p95_s']:+}s")
    print(f"증가분(같은 {same['run']['cases']}케이스): 호출 {same['delta']['llm_calls']:+} · "
          f"토큰 {same['delta']['billed_tokens']:+} · wall 합 {same['delta']['wall_clock_s']:+}s")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path, nargs="?", help="대상 bench 결과 JSON")
    parser.add_argument("--baseline", type=Path,
                        default=REPO_ROOT / "docs/eval/bench-v1.2-p1-on.json")
    parser.add_argument("--positive-control", action="store_true",
                        help="기준 회차의 변조 사본으로 검출기를 시험한다")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--filter-policy", choices=("strict", "null-pass"), default="strict",
                        help="필터 재생 무결성 대조에 쓸 정책 (v1.2-N1). 지표 정의는 바뀌지 않는다")
    parser.add_argument("--preregistration", default=PREREGISTRATION,
                        help=f"출력에 적을 사전 등록 커밋 (기본 {PREREGISTRATION} = T3a)")
    args = parser.parse_args(argv)
    filter_policy = args.filter_policy.replace("-", "_")
    if not args.positive_control and args.run is None:
        parser.error("대상 bench JSON 또는 --positive-control")

    from src.providers.config import load_tracing_settings  # noqa: PLC0415
    from src.tools.retrieval import ChromaRetriever  # noqa: PLC0415

    trace_dir = load_tracing_settings().local_trace_dir
    retriever = ChromaRetriever()
    searcher = make_searcher(retriever, filter_policy)
    doc_domains = load_doc_domains(retriever)
    oracle = load_oracle()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    started = time.perf_counter()

    try:
        baseline_analysis = analyze_run(baseline, trace_dir=trace_dir, searcher=searcher,
                                        doc_domains=doc_domains, oracle=oracle)
        if args.positive_control:
            run, planted = plant_harm(baseline, baseline_analysis)
        else:
            run = json.loads(args.run.read_text(encoding="utf-8"))
        report = evaluate(run, baseline, trace_dir=trace_dir, searcher=searcher,
                          doc_domains=doc_domains, oracle=oracle,
                          baseline_analysis=baseline_analysis,
                          filter_policy=filter_policy, preregistration=args.preregistration)
    except Uncheckable as exc:
        print("대조 불가:", exc)
        return EXIT_UNCHECKABLE

    report["run_file"] = args.run.name if args.run else None
    report["baseline_file"] = args.baseline.name
    report["elapsed_s"] = round(time.perf_counter() - started, 1)
    exit_code = EXIT_OK
    if args.positive_control:
        failures = check_positive_control(report, planted)
        report["positive_control"] = {"planted": planted, "failures": failures,
                                      "passed": not failures}
        print("양성 대조:", "통과" if not failures else "실패", planted)
        for f in failures:
            print("  -", f)
        if failures:
            exit_code = EXIT_UNCHECKABLE

    print_report(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print("→", args.out)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
