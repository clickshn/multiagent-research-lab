"""Researcher `tech_domain` 선택 도구 (v1.2-T2, ADR-028 · 사전 등록 `docs/plans/v1.2-preregistration.md`).

고정하는 계약:

1. **off = v1.2-P1.** 도구가 꺼져 있으면 선택 호출이 없고, 검색 인자·span 입력·LLM 입력 해시가
   그대로다. (실제 p1-on 트레이스 대조는 `scripts/check_tool_off_parity.py`.)
2. **on.** 1회차 항목마다 선택 호출 1회(json_schema) → 고른 값으로 검색 → 기존 Researcher 호출.
   "없음"이면 무필터. 재시도는 다시 고르지 않고 1회차 값을 쓴다(D1).
3. **동시 실행.** 선택 호출도 겹친다. 검색은 여전히 한 번에 하나다.
4. **정지 규칙.** 스키마 위반 → `TechDomainSelectionError`, 어휘 밖 값 → `FilterValueError`.
   삼키지 않는다. 엔드포인트 오류는 `selection_error`로 세고 무필터로 진행한다.
5. **span 귀속.** 선택값·원문 응답·필터 후 결과 수가 span에 남고, bench 귀속 검사가 그것까지 덮는다.

엔드포인트는 부르지 않는다.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import bench_golden  # noqa: E402
from src.obs.tracer import LocalJsonlTracer  # noqa: E402
from src.orchestrator import compile_graph, initial_state, nodes, prompts  # noqa: E402
from src.orchestrator.nodes import (  # noqa: E402
    TECH_DOMAIN_ABSTAIN,
    TechDomainSelectionError,
    make_researcher_node,
)
from src.orchestrator.state import ResearchState  # noqa: E402
from src.providers import ConfigError, LLMError, LLMResponse  # noqa: E402
from src.providers.config import ToolSettings, load_tool_settings  # noqa: E402
from src.tools.retrieval import FilterValueError, RetrievedChunk  # noqa: E402
from src.tools.scope import ScopedRetriever  # noqa: E402

VOCAB = frozenset({"Agent", "RAG", "Reasoning"})
TOPICS = ["항목 0", "항목 1", "항목 2", "항목 3"]
# 항목별 선택 — 항목 1은 기권
CHOICE = {"항목 0": "Agent", "항목 1": TECH_DOMAIN_ABSTAIN, "항목 2": "RAG", "항목 3": "Agent"}


class Gauge:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current = 0
        self.peak = 0

    def __enter__(self) -> None:
        with self._lock:
            self.current += 1
            self.peak = max(self.peak, self.current)

    def __exit__(self, *_: object) -> None:
        with self._lock:
            self.current -= 1


def _topic(text: str) -> str | None:
    return next((t for t in TOPICS if t in text), None)


class Provider:
    """시스템 프롬프트로 노드를 가리고, user의 항목 이름으로 응답을 정한다."""

    def __init__(self, *, choice: dict | None = None, raw: dict | None = None,
                 fail: set | None = None, delay: float = 0.0,
                 verdicts: dict | None = None) -> None:
        self.choice = CHOICE if choice is None else choice
        self.raw = raw or {}
        self.fail = fail or set()
        self.delay = delay
        self.verdicts = verdicts or {}
        self.calls: list[dict] = []
        self.select_gauge = Gauge()
        self._lock = threading.Lock()

    def complete(self, messages, **kwargs) -> LLMResponse:
        system, user = messages[0].content, messages[1].content
        topic = _topic(user)
        is_select = system.startswith(prompts.GROUNDING_RULE) and "검색 필터 선택" in system
        with self._lock:
            self.calls.append({"select": is_select, "topic": topic, "messages": messages, **kwargs})
        if is_select:
            assert kwargs.get("response_format") is not None
            with self.select_gauge:
                time.sleep(self.delay)
            if topic in self.fail:
                raise LLMError("429 Too Many Requests")
            text = self.raw.get(topic) or json.dumps(
                {"tech_domain": self.choice[topic]}, ensure_ascii=False)
        else:
            assert "response_format" not in kwargs  # 기존 노드 호출은 스키마 없이 그대로
            if system == prompts.OUTLINER_SYSTEM:
                text = json.dumps(TOPICS, ensure_ascii=False)
            elif system == prompts.RESEARCHER_SYSTEM:
                text = '{"supporting": [1]}'
            elif system == prompts.VERIFIER_SYSTEM:
                text = json.dumps({"verdict": self.verdicts.get(topic, "covered")})
            else:
                text = "초안"
        return LLMResponse(text=text, model="fake", finish_reason="stop",
                           prompt_tokens=10, completion_tokens=5, latency_s=0.01)


class Retriever:
    """필터가 있으면 검색 계층처럼 `retrieval_filter` span을 남긴다. 받은 인자를 기록한다."""

    def __init__(self, *, invalid: set | None = None) -> None:
        self.calls: list[dict] = []
        self.invalid = invalid or set()
        self.gauge = Gauge()
        self._lock = threading.Lock()

    def search(self, query: str, **kwargs) -> list[RetrievedChunk]:
        with self._lock:
            self.calls.append({"query": query, **kwargs})
        with self.gauge:
            time.sleep(0.005)
        topic = _topic(query)
        tech_domain = kwargs.get("tech_domain")
        if tech_domain is not None and tech_domain not in VOCAB | self.invalid:
            raise FilterValueError(f"어휘 밖 {tech_domain!r}")
        if tech_domain in self.invalid:
            raise FilterValueError(f"어휘 밖 {tech_domain!r}")
        n = 2 if tech_domain else 4
        chunks = [
            RetrievedChunk(doc_id=f"doc:{topic[-1]}-{i}", locator="abstract",
                           text=f"{topic} 본문 {i}", source="arxiv", title="제목", score=0.5)
            for i in range(1, n + 1)
        ]
        trace = kwargs.get("trace")
        if tech_domain is not None and trace is not None:
            trace.span("retrieval_filter", input={
                "tech_domain": tech_domain, "null_policy": "strict", "k": kwargs.get("k"),
            }).end(output=[], metadata={"expected": 10, "fetched": 10, "survivors": 2,
                                        "returned": len(chunks)})
        return chunks


def _state(topics: Sequence[str], **extra) -> ResearchState:
    return ResearchState(query="질의", outline=list(topics), revision=0, **extra)


def _spans(trace_dir: Path, name: str | None = None) -> list[dict]:
    (path,) = trace_dir.glob("*.jsonl")
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [r for r in records if name is None or r.get("name") == name]


# ---------------------------------------------------------------------------
# 1. off = v1.2-P1
# ---------------------------------------------------------------------------


def test_off_makes_no_selection_call_and_search_args_are_unchanged(tmp_path) -> None:
    provider, retriever = Provider(), Retriever()
    trace = LocalJsonlTracer(tmp_path).trace("run")
    out = make_researcher_node(provider, retriever=retriever, trace=trace,
                               max_concurrency=4)(_state(TOPICS))

    assert not any(c["select"] for c in provider.calls)
    assert all(set(c) == {"query", "k", "sources"} for c in retriever.calls)
    assert all(f.tech_domain is None and f.tech_domain_outcome is None for f in out["findings"])
    spans = _spans(tmp_path, "researcher_retrieve")
    assert all(set(s["input"]) == {"topic", "search_text", "k", "revision", "item_index"}
               for s in spans)
    assert not _spans(tmp_path, "researcher_select") and not _spans(tmp_path, "retrieval_filter")
    items = bench_golden.item_records(out["findings"], _spans(tmp_path))
    assert all("tech_domain" not in item for item in items)
    assert bench_golden.attribution_problems(items, _spans(tmp_path)) == []


def test_off_and_on_send_the_same_downstream_inputs_when_every_item_abstains() -> None:
    """전 항목 기권이면 필터가 없다 → 선택 호출을 빼면 나머지 입력 해시가 off와 같다."""
    def run(vocab):
        provider = Provider(choice={t: TECH_DOMAIN_ABSTAIN for t in TOPICS})
        app = compile_graph(provider, retriever=Retriever(), max_concurrency=4,
                            tech_domain_vocab=vocab)
        app.invoke(initial_state("질의", max_revisions=2), config={"recursion_limit": 50})
        return sorted(
            nodes._input_hash(c["messages"], temperature=0.0, max_tokens=c.get("max_tokens"))
            for c in provider.calls if not c["select"]
        ), sum(c["select"] for c in provider.calls)

    off, off_selects = run(None)
    on, on_selects = run(VOCAB)
    assert off_selects == 0 and on_selects == len(TOPICS)
    assert off == on


def test_scoped_retriever_passes_filter_only_when_set() -> None:
    inner = Retriever()
    scoped = ScopedRetriever(inner)
    scoped.search("항목 0", k=4)
    scoped.search("항목 0", k=4, tech_domain="Agent", trace=None)
    assert set(inner.calls[0]) == {"query", "k", "sources"}
    assert inner.calls[1]["tech_domain"] == "Agent"


# ---------------------------------------------------------------------------
# 2. on
# ---------------------------------------------------------------------------


def test_on_selects_once_per_item_then_searches_with_choice() -> None:
    provider, retriever = Provider(), Retriever()
    out = make_researcher_node(provider, retriever=retriever, max_concurrency=1,
                               tech_domain_vocab=VOCAB)(_state(TOPICS))

    selects = [c for c in provider.calls if c["select"]]
    assert [c["topic"] for c in selects] == TOPICS
    schema = selects[0]["response_format"]
    assert schema == nodes.tech_domain_schema(VOCAB)
    assert schema["json_schema"]["schema"]["properties"]["tech_domain"]["enum"][-1] == "없음"
    # 순차 경로: 선택 → 검색 → Researcher 순서로 항목마다
    order = [("select" if c["select"] else "call", c["topic"]) for c in provider.calls]
    assert order == [(kind, t) for t in TOPICS for kind in ("select", "call")]

    by_topic = {_topic(c["query"]): c for c in retriever.calls}
    assert by_topic["항목 0"]["tech_domain"] == "Agent"
    assert "tech_domain" not in by_topic["항목 1"]  # 기권 = 무필터, 인자 자체가 없다
    assert by_topic["항목 2"]["tech_domain"] == "RAG"
    assert [(f.tech_domain, f.tech_domain_outcome) for f in out["findings"]] == [
        ("Agent", "chosen"), (None, "abstain"), ("RAG", "chosen"), ("Agent", "chosen")]
    nodes_ = [r.node for r in out["trace"]]
    assert nodes_.count("researcher_select") == 4 and nodes_.count("researcher") == 4


def test_retry_reuses_first_pass_choice_without_new_selection() -> None:
    provider = Provider(verdicts={"항목 0": "uncovered", "항목 1": "uncovered"})
    retriever = Retriever()
    app = compile_graph(provider, retriever=retriever, max_concurrency=4, tech_domain_vocab=VOCAB)
    state = app.invoke(initial_state("질의", max_revisions=2), config={"recursion_limit": 50})

    assert sum(c["select"] for c in provider.calls) == len(TOPICS)  # 1회차만
    retry = [f for f in state["findings"] if f.revision == 1]
    assert {f.topic: (f.tech_domain, f.tech_domain_outcome) for f in retry} == {
        "항목 0": ("Agent", "chosen"), "항목 1": (None, "abstain")}
    retry_searches = retriever.calls[len(TOPICS):]
    assert {_topic(c["query"]): c.get("tech_domain") for c in retry_searches} == {
        "항목 0": "Agent", "항목 1": None}


def test_selection_calls_run_concurrently_while_search_stays_serial() -> None:
    provider, retriever = Provider(delay=0.05), Retriever()
    make_researcher_node(provider, retriever=retriever, max_concurrency=4,
                         tech_domain_vocab=VOCAB)(_state(TOPICS))
    assert provider.select_gauge.peak > 1
    assert retriever.gauge.peak == 1


def test_selection_prompt_is_versioned_and_has_no_golden_text() -> None:
    golden = json.loads((REPO_ROOT / "docs/eval/golden-set.json").read_text(encoding="utf-8"))
    prompt = prompts.TECH_DOMAIN_SELECT_SYSTEM + prompts.TECH_DOMAIN_SELECT_USER
    for case in golden["cases"]:
        assert case["query"] not in prompt
    assert prompts.PROMPT_VERSION == "2026-10-01.1"


# ---------------------------------------------------------------------------
# 4. 정지 규칙
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [
    "Agent",                                  # JSON 아님 (강제 꺼짐)
    '```json\n{"tech_domain": "Agent"}\n```',  # 코드펜스 — 관대하게 흡수하지 않는다
    '{"tech_domain": "Agent", "why": "x"}',   # 키 추가
    '{"tech_domain": "Vision"}',              # enum 밖
])
def test_schema_violation_stops_the_run(tmp_path, raw) -> None:
    trace = LocalJsonlTracer(tmp_path).trace("run")
    node = make_researcher_node(Provider(raw={"항목 2": raw}), retriever=Retriever(),
                                trace=trace, max_concurrency=4, tech_domain_vocab=VOCAB)
    with pytest.raises(TechDomainSelectionError):
        node(_state(TOPICS))
    (bad,) = [s for s in _spans(tmp_path, "researcher_select")
              if s["metadata"]["topic"] == "항목 2"]
    assert bad["metadata"]["selection_outcome"] == "violation"
    assert bad["output"] == raw


def test_filter_value_error_is_not_swallowed(tmp_path) -> None:
    trace = LocalJsonlTracer(tmp_path).trace("run")
    node = make_researcher_node(Provider(), retriever=Retriever(invalid={"RAG"}), trace=trace,
                                max_concurrency=1, tech_domain_vocab=VOCAB)
    with pytest.raises(FilterValueError):
        node(_state(TOPICS))
    errored = [s for s in _spans(tmp_path, "researcher_retrieve")
               if (s.get("metadata") or {}).get("error")]
    assert errored and errored[0]["metadata"]["error"].startswith("FilterValueError")


def test_endpoint_error_is_selection_error_and_falls_back_to_unfiltered(tmp_path) -> None:
    trace = LocalJsonlTracer(tmp_path).trace("run")
    provider, retriever = Provider(fail={"항목 0"}), Retriever()
    out = make_researcher_node(provider, retriever=retriever, trace=trace, max_concurrency=4,
                               tech_domain_vocab=VOCAB)(_state(TOPICS))
    first = out["findings"][0]
    assert (first.tech_domain, first.tech_domain_outcome) == (None, "selection_error")
    assert first.citations  # 무필터로 진행해 Researcher까지 갔다
    assert "tech_domain" not in next(c for c in retriever.calls if _topic(c["query"]) == "항목 0")
    assert bench_golden.selection_errors(out["findings"]) == 1
    items = bench_golden.item_records(out["findings"], _spans(tmp_path))
    assert bench_golden.attribution_problems(items, _spans(tmp_path)) == []


# ---------------------------------------------------------------------------
# 5. span 기록 · 귀속
# ---------------------------------------------------------------------------


def _traced_run(tmp_path):
    trace = LocalJsonlTracer(tmp_path).trace("run")
    node = make_researcher_node(Provider(delay=0.01), retriever=Retriever(), trace=trace,
                                max_concurrency=4, tech_domain_vocab=VOCAB)
    first = node(_state(TOPICS))
    retry = node(ResearchState(query="질의", outline=TOPICS, revision=1,
                               uncovered=["항목 2", "항목 1"], findings=first["findings"]))
    return first["findings"] + retry["findings"], _spans(tmp_path)


def test_spans_record_choice_raw_response_and_filter_counts(tmp_path) -> None:
    findings, records = _traced_run(tmp_path)
    items = bench_golden.item_records(findings, records)
    assert items is not None
    assert bench_golden.attribution_problems(items, records) == []

    by_key = {(i["topic"], i["revision"]): i["tech_domain"] for i in items}
    assert by_key[("항목 0", 0)] == {
        "value": "Agent", "outcome": "chosen", "source": "selected", "searched_with": "Agent",
        "selected": "Agent", "raw_response": '{"tech_domain": "Agent"}',
        "select_input_hash": by_key[("항목 0", 0)]["select_input_hash"],
        "filter_survivors": 2, "filter_returned": 2, "filter_expected": 10,
    }
    assert by_key[("항목 0", 0)]["select_input_hash"]
    assert by_key[("항목 1", 0)]["selected"] == "없음"
    assert by_key[("항목 1", 0)]["filter_survivors"] is None  # 기권은 필터 span이 없다
    assert by_key[("항목 2", 1)]["source"] == "reused"
    assert by_key[("항목 2", 1)]["selected"] is None  # 재시도에는 선택 호출이 없다
    assert by_key[("항목 2", 1)]["filter_returned"] == 2
    # 선택 span에 항목 키와 입력 해시가 있다
    for span in [r for r in records if r["name"] == "researcher_select"]:
        assert span["metadata"]["revision"] == 0 and span["metadata"]["input_hash"]
    summary = bench_golden.tech_domain_selection_summary([{"stratum": "A", "items": items}])
    assert summary == {"A": {"abstain": 1, "chosen": 3, "first_pass_items": 4, "reused": 2}}


@pytest.mark.parametrize("tamper", ["select_index", "select_value", "filter_index", "reuse"])
def test_attribution_catches_misattributed_selection_spans(tmp_path, tamper) -> None:
    findings, records = _traced_run(tmp_path)
    if tamper == "select_index":
        span = next(r for r in records if r["name"] == "researcher_select"
                    and r["metadata"]["topic"] == "항목 0")
        span["metadata"]["item_index"] = 3  # 다른 항목의 키 → 중복
    elif tamper == "select_value":
        span = next(r for r in records if r["name"] == "researcher_select"
                    and r["metadata"]["topic"] == "항목 2")
        span["metadata"]["selected"] = "Agent"
    elif tamper == "filter_index":
        span = next(r for r in records if r["name"] == "retrieval_filter")
        span["input"]["item_index"] = 1  # 기권 항목(1)으로 옮긴다
    else:
        span = next(r for r in records if r["name"] == "researcher_retrieve"
                    and r["input"]["revision"] == 1 and r["input"]["topic"] == "항목 2")
        span["input"]["tech_domain"] = "Agent"
    items = bench_golden.item_records(findings, records)
    assert bench_golden.attribution_problems(items, records)


def test_attribution_flags_selection_spans_on_an_off_row(tmp_path) -> None:
    findings, records = _traced_run(tmp_path)
    off_items = [{k: v for k, v in i.items() if k != "tech_domain"}
                 for i in bench_golden.item_records(findings, records)]
    assert any("도구 off" in p for p in bench_golden.attribution_problems(off_items, records))


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------


def test_tool_settings_default_off_and_reject_typos(monkeypatch) -> None:
    monkeypatch.delenv("RESEARCH_TECH_DOMAIN_TOOL", raising=False)
    assert load_tool_settings(env_file=None) == ToolSettings(tech_domain_tool=False)
    monkeypatch.setenv("RESEARCH_TECH_DOMAIN_TOOL", "on")
    assert load_tool_settings(env_file=None).tech_domain_tool is True
    monkeypatch.setenv("RESEARCH_TECH_DOMAIN_TOOL", "onn")
    with pytest.raises(ConfigError):
        load_tool_settings(env_file=None)


# ---------------------------------------------------------------------------
# 1'. off = p1-on — 실제 기준 회차 트레이스 대조 (로컬 트레이스가 있을 때만)
# ---------------------------------------------------------------------------


def _p1_on_traces_present() -> bool:
    from src.providers.config import load_tracing_settings

    bench = json.loads((REPO_ROOT / "docs/eval/bench-v1.2-p1-on.json").read_text(encoding="utf-8"))
    trace_dir = load_tracing_settings().local_trace_dir
    return all((trace_dir / f"{row['run_id']}.jsonl").exists() for row in bench["rows"])


@pytest.mark.skipif(not _p1_on_traces_present(), reason="p1-on 로컬 트레이스 없음(var/traces는 커밋되지 않는다)")
def test_tool_off_replays_p1_on_with_identical_input_hashes(monkeypatch) -> None:
    """off 경로를 p1-on 응답으로 재생 → 30/30 케이스, 297/297 호출 입력 해시 동일 (LLM 호출 0)."""
    from scripts import check_tool_off_parity

    monkeypatch.setattr(sys, "argv", ["x", str(REPO_ROOT / "docs/eval/bench-v1.2-p1-on.json")])
    assert check_tool_off_parity.main() == 0
