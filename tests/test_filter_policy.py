"""`tech_domain` 필터 정책 — strict | null_pass (v1.2-N1, 사전 등록 `docs/plans/v1.2-n1-preregistration.md`).

고정하는 것:

1. **null_pass** — 메타(`tech_domains`)가 없는 문서는 통과하고, 다른 도메인 문서는 빠지며, 생존자 순서는
   무필터 순위 그대로다. 필터 후 결과 수(생존·반환·반환 중 메타 없음)가 트레이스에 남는다.
2. **strict 불변** — 정책을 명시하지 않거나 strict를 명시하면 검색 결과·span·하위 호출 인자가 이전(ADR-027)과 같다.
   (실제 인덱스 대조는 T1 프로브 동일성·p1 트레이스 재생으로 따로 한다.)
3. 알 수 없는 정책은 `FilterValueError` — 질의 전에 멈춘다.
4. 노드·스코프는 null_pass일 때만 정책을 넘기고 span에 싣는다. bench 귀속 검사는 필터 span의 정책을 대조한다.
5. bench `--filter-policy`, `compare_bench_runs` 정책 불일치 거부, 분석 스크립트 정책 인자, 1단계 재생 스크립트.

엔드포인트는 부르지 않는다. 임베딩은 결정적 가짜다.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
REPO_ROOT = TESTS.parent
for p in (REPO_ROOT, REPO_ROOT / "scripts", TESTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import analyze_t3_selection as t3  # noqa: E402
import replay_n1_null_pass as n1  # noqa: E402
from test_retrieval_filter import (  # noqa: E402, F401 — `retriever`는 픽스처
    DOCS,
    QUERY,
    VOCAB,
    RecordingTrace,
    retriever,
)
from test_tech_domain_tool import TOPICS, Provider, _spans, _state, _topic  # noqa: E402

from scripts import bench_golden, compare_bench_runs  # noqa: E402
from src.obs.tracer import LocalJsonlTracer  # noqa: E402
from src.orchestrator.nodes import make_researcher_node  # noqa: E402
from src.tools.retrieval import FILTER_POLICIES, FilterValueError, RetrievedChunk  # noqa: E402
from src.tools.scope import ScopedRetriever  # noqa: E402

META_LESS = {d for d, _, domains in DOCS if not domains}


def _ids(chunks) -> list[str]:
    return [c.doc_id for c in chunks]


# ---------------------------------------------------------------------------
# 1·2·3. 검색 계층
# ---------------------------------------------------------------------------


def test_policies_are_strict_and_null_pass() -> None:
    assert FILTER_POLICIES == ("strict", "null_pass")


@pytest.mark.parametrize("domain", sorted(VOCAB))
def test_null_pass_keeps_meta_less_drops_other_domains_and_preserves_rank(retriever, domain) -> None:
    r, _ = retriever
    unfiltered = r.search(QUERY, k=len(DOCS))
    expected = [c for c in unfiltered if domain in c.tech_domains or not c.tech_domains]
    got = r.search(QUERY, k=len(DOCS), tech_domain=domain, filter_policy="null_pass")
    assert [(c.doc_id, c.score) for c in got] == [(c.doc_id, c.score) for c in expected]
    assert META_LESS <= set(_ids(got))  # 메타 없는 문서는 어떤 값으로도 통과한다
    others = {d for d, _, domains in DOCS if domains and domain not in domains.split("|")}
    assert not others & set(_ids(got))  # 다른 도메인 문서는 빠진다
    # strict 생존자 ⊆ null_pass 생존자, 순서 유지
    strict = r.search(QUERY, k=len(DOCS), tech_domain=domain)
    assert [c for c in _ids(got) if c in set(_ids(strict))] == _ids(strict)


def test_null_pass_records_policy_and_counts_in_trace(retriever) -> None:
    r, _ = retriever
    trace = RecordingTrace()
    got = r.search(QUERY, k=4, tech_domain="Agent", trace=trace, filter_policy="null_pass")
    (span,) = trace.spans
    assert span["input"] == {"tech_domain": "Agent", "null_policy": "null_pass", "k": 4}
    agents = sum(1 for _, _, d in DOCS if "Agent" in d.split("|"))
    assert span["metadata"] == {
        "expected": len(DOCS), "fetched": len(DOCS), "survivors": agents + len(META_LESS),
        "returned": 4, "returned_without_meta": sum(d in META_LESS for d in _ids(got)),
    }


def test_strict_explicit_equals_default_including_span(retriever) -> None:
    r, spy = retriever
    t_default, t_strict = RecordingTrace(), RecordingTrace()
    default = r.search(QUERY, k=2, tech_domain="Agent", trace=t_default)
    explicit = r.search(QUERY, k=2, tech_domain="Agent", trace=t_strict, filter_policy="strict")
    assert [(c.doc_id, c.score) for c in default] == [(c.doc_id, c.score) for c in explicit]
    assert t_default.spans == t_strict.spans
    assert "returned_without_meta" not in t_strict.spans[0]["metadata"]  # strict span은 ADR-027 그대로
    assert spy.queries[0] == spy.queries[1]


def test_unknown_policy_raises_before_query(retriever) -> None:
    r, spy = retriever
    for policy in ("null-pass", "pass", ""):
        with pytest.raises(FilterValueError, match="필터 정책"):
            r.search(QUERY, k=4, tech_domain="Agent", filter_policy=policy)
    assert spy.queries == []


def test_null_pass_without_value_is_the_unfiltered_path(retriever) -> None:
    r, spy = retriever
    got = r.search(QUERY, k=3, filter_policy="null_pass")
    assert spy.queries[0]["n_results"] == 3 and spy.other_calls == []
    assert _ids(got) == _ids(r.search(QUERY, k=3))


# ---------------------------------------------------------------------------
# 4. 스코프 · 노드 · bench 귀속
# ---------------------------------------------------------------------------


class PolicyRetriever:
    """받은 인자를 기록하고, 필터가 있으면 받은 정책 그대로 `retrieval_filter` span을 남긴다."""

    def __init__(self, *, span_policy: str | None = None) -> None:
        self.calls: list[dict] = []
        self.span_policy = span_policy  # 귀속 검사용 변조 — 받은 정책 대신 이 값을 적는다
        self._lock = threading.Lock()

    def search(self, query: str, **kwargs) -> list[RetrievedChunk]:
        with self._lock:
            self.calls.append({"query": query, **kwargs})
        topic = _topic(query)
        chunks = [RetrievedChunk(doc_id=f"doc:{topic[-1]}-{i}", locator="abstract", text="본문",
                                 source="arxiv", title="제목", score=0.5) for i in (1, 2)]
        trace = kwargs.get("trace")
        if kwargs.get("tech_domain") is not None and trace is not None:
            policy = self.span_policy or kwargs.get("filter_policy", "strict")
            meta = {"expected": 10, "fetched": 10, "survivors": 5, "returned": 2}
            if policy == "null_pass":
                meta["returned_without_meta"] = 1
            trace.span("retrieval_filter", input={
                "tech_domain": kwargs["tech_domain"], "null_policy": policy, "k": kwargs.get("k"),
            }).end(output=[], metadata=meta)
        return chunks


def test_scoped_retriever_passes_policy_only_for_null_pass_with_value() -> None:
    inner = PolicyRetriever()
    scoped = ScopedRetriever(inner)
    scoped.search("항목 0", k=4, tech_domain="Agent", trace=None)                   # strict 기본
    scoped.search("항목 0", k=4, tech_domain="Agent", trace=None, filter_policy="strict")
    scoped.search("항목 0", k=4, filter_policy="null_pass")                          # 기권 = 무필터
    scoped.search("항목 0", k=4, tech_domain="Agent", trace=None, filter_policy="null_pass")
    assert "filter_policy" not in inner.calls[0] and inner.calls[0] == inner.calls[1]
    assert set(inner.calls[2]) == {"query", "k", "sources"}
    assert inner.calls[3]["filter_policy"] == "null_pass"


def _run_node(tmp_path, policy: str | None, retriever=None):
    trace = LocalJsonlTracer(tmp_path).trace("run")
    retriever = retriever or PolicyRetriever()
    extra = {} if policy is None else {"filter_policy": policy}
    node = make_researcher_node(Provider(), retriever=retriever, trace=trace, max_concurrency=4,
                                tech_domain_vocab=frozenset({"Agent", "RAG", "Reasoning"}), **extra)
    out = node(_state(TOPICS))
    return out["findings"], _spans(tmp_path), retriever


def test_node_null_pass_passes_policy_and_records_it(tmp_path) -> None:
    findings, records, retriever = _run_node(tmp_path, "null_pass")
    by_topic = {_topic(c["query"]): c for c in retriever.calls}
    assert by_topic["항목 0"]["filter_policy"] == "null_pass"
    assert "filter_policy" not in by_topic["항목 1"] and "tech_domain" not in by_topic["항목 1"]  # 기권
    spans = {s["input"]["topic"]: s["input"] for s in records if s["name"] == "researcher_retrieve"}
    assert all(s["filter_policy"] == "null_pass" for s in spans.values())

    items = bench_golden.item_records(findings, records)
    assert bench_golden.attribution_problems(items, records) == []
    chosen = [i["tech_domain"] for i in items if i["tech_domain"]["value"] is not None]
    assert all(c["filter_policy"] == "null_pass" for c in chosen)
    assert all(c["filter_returned_without_meta"] == 1 for c in chosen)


@pytest.mark.parametrize("policy", [None, "strict"])
def test_node_strict_is_unchanged(tmp_path, policy) -> None:
    findings, records, retriever = _run_node(tmp_path, policy)
    assert all("filter_policy" not in c for c in retriever.calls)
    assert all("filter_policy" not in s["input"] for s in records if s["name"] == "researcher_retrieve")
    items = bench_golden.item_records(findings, records)
    assert all("filter_policy" not in i["tech_domain"] for i in items)
    assert bench_golden.attribution_problems(items, records) == []


def test_attribution_catches_policy_mismatch_in_filter_span(tmp_path) -> None:
    findings, records, _ = _run_node(tmp_path, "null_pass", PolicyRetriever(span_policy="strict"))
    items = bench_golden.item_records(findings, records)
    assert any("정책" in p for p in bench_golden.attribution_problems(items, records))


def test_node_rejects_unknown_policy() -> None:
    with pytest.raises(FilterValueError):
        make_researcher_node(Provider(), retriever=PolicyRetriever(), filter_policy="null-pass")


def test_bench_rejects_null_pass_without_tool() -> None:
    with pytest.raises(SystemExit):
        bench_golden.main(["--label", "x", "--tech-domain-tool", "off", "--filter-policy", "null-pass"])


# ---------------------------------------------------------------------------
# 5. compare_bench_runs · 분석 스크립트
# ---------------------------------------------------------------------------


def _summary(label: str, **extra) -> dict:
    return {"label": label, **{k: "same" for k in compare_bench_runs.CONDITION_KEYS},
            "parallel": True, "rows": [], **extra}


def test_compare_refuses_policy_mismatch_unless_allowed() -> None:
    legacy, strict, null = _summary("t3a"), _summary("s", filter_policy="strict"), \
        _summary("n", filter_policy="null_pass")
    assert compare_bench_runs.compare(legacy, strict)["filter_policy"] == ["strict", "strict"]
    with pytest.raises(ValueError, match="필터 정책"):
        compare_bench_runs.compare(legacy, null)
    result = compare_bench_runs.compare(legacy, null, allow_policy_mismatch=True)
    assert result["filter_policy"] == ["strict", "null_pass"] and result["policy_mismatch_allowed"]


def test_compare_cli_flag(tmp_path) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(_summary("a")), encoding="utf-8")
    b.write_text(json.dumps(_summary("b", filter_policy="null_pass")), encoding="utf-8")
    assert compare_bench_runs.main([str(a), str(b)]) == 2
    assert compare_bench_runs.main([str(a), str(b), "--allow-policy-mismatch"]) == 0


def test_analysis_validity_checks_recorded_policy() -> None:
    analysis = {"cases": []}
    base = {"cache_enabled": False, "parallel": True}
    run = {"cache_enabled": False, "rows": [], "filter_policy": "null_pass"}
    assert t3.validity_problems(run, base, analysis, filter_policy="null_pass") == []
    assert any("정책" in p for p in t3.validity_problems(run, base, analysis))  # 기본 strict
    legacy = {"cache_enabled": False, "rows": []}  # 키 없음 = strict
    assert t3.validity_problems(legacy, base, analysis) == []
    assert any("정책" in p for p in t3.validity_problems(legacy, base, analysis, filter_policy="null_pass"))


def test_analysis_searcher_passes_policy_only_with_value() -> None:
    calls = []

    class R:
        def search(self, text, **kwargs):
            calls.append(kwargs)
            return []

    s = t3.make_searcher(R(), "null_pass")
    s("q", 4, None)
    s("q", 4, "Agent")
    assert calls == [{"k": 4, "tech_domain": None}, {"k": 4, "tech_domain": "Agent",
                                                     "filter_policy": "null_pass"}]


# ---------------------------------------------------------------------------
# 5. 1단계 재생 스크립트 (합성 — 인덱스 없음)
# ---------------------------------------------------------------------------

# 무필터: 정답 E 2위. strict "Good"은 E를 1위로, null_pass "Good"은 메타 없는 M이 E 위로 들어와 E를 밀어낸다(k=1).
UNF = ["X", "E", "M", "Y"]
RESULTS = {
    (None, "strict"): UNF,
    ("Good", "strict"): ["E", "W"],
    ("Good", "null_pass"): ["M", "E", "W"],
    ("Bad", "strict"): ["X", "Y"],
    ("Bad", "null_pass"): ["X", "M", "Y"],
}
DOMAINS = {"E": ("Good",), "X": ("Bad",), "Y": ("Bad",), "W": ("Good",), "M": ()}


def fake_searcher(text, k, value, policy):
    return RESULTS[(value, policy if value is not None else "strict")][:k]


def _n1_item(index, value, *, k_strict=None):
    searched = value
    cands = fake_searcher("q", 4, searched, "strict")
    return {"topic": f"t{index}", "revision": 0, "item_index": index, "retrieval_error": False,
            "candidates": [{"doc_id": d} for d in (k_strict or cands)],
            "tech_domain": {"value": value, "searched_with": searched,
                            "outcome": "chosen" if value else "abstain"}}


def _n1_run(tmp_path, rows):
    for row in rows:
        lines = [json.dumps({"type": "span", "name": "researcher_retrieve",
                             "input": {"topic": it["topic"], "search_text": "q", "k": 4,
                                       "revision": 0, "item_index": it["item_index"]},
                             "output": [{"doc_id": c["doc_id"]} for c in it["candidates"]]})
                 for it in row["items"]]
        (tmp_path / f"{row['run_id']}.jsonl").write_text("\n".join(lines), encoding="utf-8")
    return {"label": "t3a", "tech_domain_tool": True, "rows": rows}


def _n1_row(case_id, stratum, items, expected=("E",)):
    return {"case_id": case_id, "stratum": stratum, "run_id": case_id, "items": items,
            "score": {"expected_doc_ids": list(expected)}}


def test_replay_null_pass_harm_gain_and_meta_less_counts(tmp_path) -> None:
    rows = [_n1_row("A1", "A", [_n1_item(0, "Good"), _n1_item(1, None)]),
            _n1_row("B1", "B", [_n1_item(0, "Bad")], expected=("M",))]
    replayed = n1.replay_run(_n1_run(tmp_path, rows), trace_dir=tmp_path, searcher=fake_searcher,
                             doc_domains=DOMAINS)
    s = n1.summarize(replayed)
    assert s["integrity_problems"] == []
    # 층 A: 정답이 통과하므로 해악 없음(구조), 층 B: 메타 없는 정답 M이 통과 → strict 해악이 null_pass에서 사라진다
    assert s["harm"]["A"]["harm_items"]["k"] == 0 and s["harm"]["A"]["eligible_items"] == 2
    assert s["harm"]["B"]["harm_items"] == {"k": 0, "n": 1, "pct": 0.0}
    assert s["strict_harm_t3a_view"]["B"]["harm_items"]["k"] == 1
    a0 = replayed["cases"][0]["items"][0]
    assert a0["expected_rank_strict"] == 1 and a0["expected_rank_recorded"] == 2
    assert a0["meta_less_in_null_pass"] == ["M"]
    assert s["observations"]["B"]["meta_less_answers_in_top_k_total"] == 1
    assert s["gate"] == {"proceed_to_e2e": True, "reasons": []}


def test_replay_flags_strict_mismatch_and_skips_gate(tmp_path) -> None:
    rows = [_n1_row("A1", "A", [_n1_item(0, "Good", k_strict=["W", "E"])])]
    s = n1.summarize(n1.replay_run(_n1_run(tmp_path, rows), trace_dir=tmp_path,
                                   searcher=fake_searcher, doc_domains=DOMAINS))
    assert any("strict 재생" in p for p in s["integrity_problems"])
    assert s["gate"]["proceed_to_e2e"] is None


def test_gate_thresholds() -> None:
    def harm(a, b):
        return {"A": {"harm_cases": {"k": a}}, "B": {"harm_cases": {"k": b}}}

    assert n1.gate(harm(1, 1))["proceed_to_e2e"] is True
    assert n1.gate(harm(2, 0))["proceed_to_e2e"] is False
    assert n1.gate(harm(0, 2))["proceed_to_e2e"] is False


def test_positive_control_detects_planted_incompatible_values(tmp_path) -> None:
    rows = [_n1_row("A1", "A", [_n1_item(0, "Good"), _n1_item(1, "Good")]),
            _n1_row("A2", "A", [_n1_item(0, "Good")]),
            _n1_row("A3", "A", [_n1_item(0, "Good")]),
            _n1_row("A4", "A", [_n1_item(0, "Good")], expected=("Z",)),  # 비적격 → 대조군
            _n1_row("B1", "B", [_n1_item(0, "Bad")], expected=("M",))]
    run = _n1_run(tmp_path, rows)
    planted_run, planted = n1.plant_incompatible(run, trace_dir=tmp_path, searcher=fake_searcher,
                                                 doc_domains=DOMAINS, vocab=["Bad", "Good"])
    assert planted["cases"] == ["A1", "A2"] and planted["control"] == "A4#0"
    assert planted["values"]["A1#0"] == "Bad"
    s = n1.summarize(n1.replay_run(planted_run, trace_dir=tmp_path, searcher=fake_searcher,
                                   doc_domains=DOMAINS), exclude_planted_integrity=True)
    assert n1.check_positive_control(s, planted) == []
    # 원본 입력은 건드리지 않는다
    assert run["rows"][0]["items"][0]["tech_domain"]["value"] == "Good"


def test_positive_control_fails_when_detector_is_blind(tmp_path) -> None:
    rows = [_n1_row("A1", "A", [_n1_item(0, "Good")]), _n1_row("A2", "A", [_n1_item(0, "Good")]),
            _n1_row("A3", "A", [_n1_item(0, "Good")], expected=("Z",))]
    run = _n1_run(tmp_path, rows)
    planted_run, planted = n1.plant_incompatible(run, trace_dir=tmp_path, searcher=fake_searcher,
                                                 doc_domains=DOMAINS, vocab=["Bad", "Good"])

    def blind(text, k, value, policy):  # 필터를 무시하는 고장 난 검색기
        return UNF[:k]

    s = n1.summarize(n1.replay_run(planted_run, trace_dir=tmp_path, searcher=blind,
                                   doc_domains=DOMAINS), exclude_planted_integrity=True)
    assert n1.check_positive_control(s, planted)
