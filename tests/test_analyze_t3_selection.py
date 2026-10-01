"""T3 분석 스크립트 — 사전 등록 `ce86b04` 정의·판정을 합성 데이터로 고정한다 (LLM·인덱스 없음)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import analyze_t3_selection as t3  # noqa: E402

# 무필터 순위: 정답 E가 2위. 필터 "Good"은 E를 남기고, "Bad"는 E를 지운다.
UNFILTERED = ["X", "E", "Y", "Z"]
BY_FILTER = {None: UNFILTERED, "Good": ["E", "W"], "Bad": ["X", "Y", "Z", "W"]}
DOC_DOMAINS = {"E": ("Good",)}


def searcher(text, k, tech_domain):
    return BY_FILTER[tech_domain][:k]


def _item(index, outcome, value, *, revision=0):
    searched = value if outcome == "chosen" else None
    choice = None if outcome == "tool_off" else {
        "value": value, "outcome": outcome, "source": "selected",
        "searched_with": searched, "filter_survivors": 2,
    }
    item = {"topic": f"t{index}", "revision": revision, "item_index": index,
            "retrieval_error": False,
            "candidates": [{"doc_id": d} for d in BY_FILTER[searched]]}
    if choice is not None:
        item["tech_domain"] = choice
    return item


def _row(case_id, stratum, items, *, passed=True, held=None, expected=("E",), sel_err=0):
    checks = {"negative_case_held": held} if stratum == "C" else {"expected_doc_cited": passed}
    return {"case_id": case_id, "stratum": stratum, "run_id": case_id,
            "items": items, "selection_errors": sel_err, "attribution_problems": [],
            "llm_calls": 10, "billed_tokens": 100, "wall_clock_s": 1.0,
            "score": {"passed": passed, "checks": checks,
                      "expected_doc_ids": [] if stratum == "C" else list(expected)}}


def _bench(rows, label):
    return {"label": label, "cache_enabled": False, "parallel": True, "rows": rows,
            "calls": {"total": 10 * len(rows), "by_node": {"researcher": 1},
                      "per_request": {"mean": 10}},
            "tokens": {"prompt_total": 1, "completion_total": 1, "billed_total": 1},
            "latency": {k: {"mean": 1, "p50": 1, "p95": 1} for k in ("wall", "llm", "retrieval")}}


def _write_traces(tmp_path, rows):
    for row in rows:
        lines = []
        for it in row["items"]:
            lines.append(json.dumps({
                "type": "span", "name": "researcher_retrieve",
                "input": {"topic": it["topic"], "search_text": "q " + it["topic"], "k": 4,
                          "revision": it["revision"], "item_index": it["item_index"]},
                "output": [{"doc_id": c["doc_id"]} for c in it["candidates"]],
            }))
        (tmp_path / f"{row['run_id']}.jsonl").write_text("\n".join(lines), encoding="utf-8")


def _baseline_rows():
    return [
        _row("A1", "A", [_item(0, "tool_off", None), _item(1, "tool_off", None)]),
        _row("A2", "A", [_item(0, "tool_off", None)]),
        _row("A3", "A", [_item(0, "tool_off", None)], passed=False),
        _row("A4", "A", [_item(0, "tool_off", None)], passed=False),
        _row("B1", "B", [_item(0, "tool_off", None)]),
        _row("B2", "B", [_item(0, "tool_off", None)]),
        _row("C1", "C", [_item(0, "tool_off", None)], held=True),
    ]


def _evaluate(tmp_path, run_rows, base_rows=None):
    base_rows = base_rows or _baseline_rows()
    _write_traces(tmp_path, run_rows)
    base_dir = tmp_path / "base"
    base_dir.mkdir(exist_ok=True)
    _write_traces(base_dir, base_rows)
    baseline = _bench(base_rows, "base")
    base_analysis = t3.analyze_run(baseline, trace_dir=base_dir, searcher=searcher,
                                   doc_domains=DOC_DOMAINS, oracle={"A1": "Good"})
    return t3.evaluate(_bench(run_rows, "run"), baseline, trace_dir=tmp_path, searcher=searcher,
                       doc_domains=DOC_DOMAINS, oracle={"A1": "Good"},
                       baseline_analysis=base_analysis)


def test_baseline_against_itself_is_harmless(tmp_path):
    report = _evaluate(tmp_path, _baseline_rows())
    assert report["judgment"]["verdict"] == "무해 확인"
    assert report["harm"]["A"]["harm_items"] == {"k": 0, "n": 5, "pct": 0.0}
    assert report["harm"]["B"]["eligible_items"] == 2


def test_compatible_and_abstain_are_harmless_and_counted(tmp_path):
    rows = _baseline_rows()
    rows[0]["items"] = [_item(0, "chosen", "Good"), _item(1, "abstain", "없음")]
    rows[4]["items"] = [_item(0, "abstain", "없음")]  # 층 B: 기권만 무해하다
    report = _evaluate(tmp_path, rows)
    assert report["judgment"]["verdict"] == "무해 확인", report["validity_problems"]
    a = report["selection"]["A"]
    assert a["abstain"]["k"] == 1
    assert a["exact"]["items_pooled"]["k"] == 1      # A1 오라클 Good
    assert a["compatible"]["items_pooled"]["k"] == 1  # 기권은 호환이 아니다(무해일 뿐)
    assert report["harm"]["A"]["harm_items"]["k"] == 0


def test_one_harm_case_is_observation_two_is_harm(tmp_path):
    rows = _baseline_rows()
    rows[0]["items"] = [_item(0, "chosen", "Bad"), _item(1, "chosen", "Bad")]
    report = _evaluate(tmp_path, rows)
    assert report["judgment"]["verdict"] == "무해 확인"
    assert report["harm"]["A"]["harm_items"]["k"] == 2
    assert report["harm"]["A"]["harm_cases"]["k"] == 1
    assert any("해악 케이스 1건" in o for o in report["judgment"]["observations"])

    rows[1]["items"] = [_item(0, "chosen", "Bad")]
    report = _evaluate(tmp_path, rows)
    assert report["judgment"]["verdict"] == "해악"
    assert report["judgment"]["reasons"][0].startswith("D3")
    detail = report["harm"]["A"]["harm_detail"]["A2"][0]
    assert (detail["expected_rank_unfiltered"], detail["expected_rank_recorded"]) == (2, None)


def test_selection_error_excluded_from_metrics(tmp_path):
    rows = _baseline_rows()
    rows[0]["items"] = [_item(0, "selection_error", None), _item(1, "chosen", "Good")]
    rows[0]["selection_errors"] = 1
    report = _evaluate(tmp_path, rows)
    assert report["selection"]["A"]["selection_error_items"] == 1
    assert report["selection"]["A"]["abstain"]["n"] == 4  # 5항목 중 1건 제외
    assert report["harm"]["A"]["eligible_items"] == 4
    assert report["judgment"]["verdict"] == "무해 확인"


def test_selection_error_three_invalidates(tmp_path):
    rows = _baseline_rows()
    rows[0]["selection_errors"] = 3
    assert _evaluate(tmp_path, rows)["judgment"]["verdict"] == "측정 무효"


def test_b_and_c_thresholds(tmp_path):
    rows = _baseline_rows()
    rows[4]["score"]["passed"] = False
    report = _evaluate(tmp_path, rows)
    assert report["judgment"]["verdict"] == "무해 확인"
    assert "층 B e2e −1건" in report["judgment"]["observations"]

    rows[5]["score"]["passed"] = False
    assert _evaluate(tmp_path, rows)["judgment"]["reasons"][0].startswith("D4")

    rows = _baseline_rows()
    rows[6]["score"]["passed"] = False
    rows[6]["score"]["checks"]["negative_case_held"] = False
    assert _evaluate(tmp_path, rows)["judgment"]["reasons"][0].startswith("D5")


def test_d7_improvement_observed(tmp_path):
    rows = _baseline_rows()
    rows[2]["score"]["passed"] = True
    rows[3]["score"]["passed"] = True
    report = _evaluate(tmp_path, rows)
    assert report["judgment"]["verdict"] == "무해 확인"
    assert report["judgment"]["improvement_observed"].startswith("D7")


def test_record_trace_mismatch_invalidates(tmp_path):
    rows = _baseline_rows()
    rows[0]["items"][0]["candidates"] = [{"doc_id": "X"}]  # 무필터 항목인데 재생과 다른 기록
    report = _evaluate(tmp_path, rows)
    assert report["judgment"]["verdict"] == "측정 무효"


def test_cache_on_invalidates(tmp_path):
    rows = _baseline_rows()
    _write_traces(tmp_path, rows)
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    _write_traces(base_dir, rows)
    run = _bench(rows, "run")
    run["cache_enabled"] = True
    report = t3.evaluate(run, _bench(rows, "base"), trace_dir=tmp_path, searcher=searcher,
                         doc_domains=DOC_DOMAINS, oracle={})
    assert report["judgment"]["verdict"] == "측정 무효"
