"""노드 안 동시 호출 (v1.2-P1, ADR-026).

고정하는 계약 네 가지:

1. **off = S0c 경로.** 상한 1이면 스레드 풀을 만들지 않고, 검색 → 호출이 항목 순서대로
   번갈아 일어난다(v1.2-S0c의 `for` 루프와 같은 순서). on과 **같은 입력**을 보낸다.
2. **결과 순서 보존.** 완료 순서가 뒤집혀도 findings·trace·uncovered는 항목 순서다.
3. **검색 직렬화.** LLM 호출은 겹치지만 검색은 한 번에 하나다. 기다린 시간은 span에 남는다.
4. **span-항목 귀속.** 동시 실행에서 span이 완료 순서로 쓰여도, 각 span은 자기 항목의
   입력·출력을 담고, bench가 `(revision, item_index)`로 되짝지은 후보·supporting이 항목과 맞는다.

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
from src.orchestrator.nodes import make_researcher_node, make_verifier_node  # noqa: E402
from src.orchestrator.state import Citation, Finding, ResearchState  # noqa: E402
from src.providers import ChatMessage, ConfigError, LLMResponse  # noqa: E402
from src.providers.config import ConcurrencySettings, load_concurrency_settings  # noqa: E402
from src.tools.retrieval import RetrievedChunk  # noqa: E402

TOPICS = ["항목 0", "항목 1", "항목 2", "항목 3", "항목 4"]


def _doc(topic: str, n: int) -> str:
    return f"doc:{topic[-1]}-{n}"


class Gauge:
    """동시에 안에 들어와 있는 스레드 수의 최댓값."""

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


class TopicProvider:
    """user 메시지의 항목 이름으로 응답을 정한다 — 스레드 순서와 무관하게 결정적이다.

    `delays`로 항목별 지연을 줘 완료 순서를 뒤집는다. 호출 순서(`events`)와 동시성(`gauge`)을 남긴다.
    """

    def __init__(self, *, delays: dict[str, float] | None = None, events: list | None = None,
                 verdicts: dict[str, str] | None = None) -> None:
        self.delays = delays or {}
        self.events = events if events is not None else []
        self.verdicts = verdicts or {}
        self.gauge = Gauge()
        self.calls: list[tuple[str, Sequence[ChatMessage], int | None]] = []
        self._lock = threading.Lock()

    @staticmethod
    def topic_of(user: str) -> str | None:
        for topic in TOPICS:
            if f"조사 항목: {topic}" in user or f"항목: {topic}" in user or topic in user:
                return topic
        return None

    def complete(self, messages: Sequence[ChatMessage], *, temperature: float = 0.0,
                 max_tokens: int | None = None, stop: Sequence[str] | None = None) -> LLMResponse:
        system, user = messages[0].content, messages[1].content
        topic = self.topic_of(user)
        with self._lock:
            self.calls.append((topic, messages, max_tokens))
            self.events.append(("call", topic))
        with self.gauge:
            time.sleep(self.delays.get(topic, 0.0))
        if system == prompts.OUTLINER_SYSTEM:
            text = json.dumps(TOPICS, ensure_ascii=False)
        elif system == prompts.RESEARCHER_SYSTEM:
            text = '{"supporting": [2]}'  # 항목마다 두 번째 후보를 고른다
        elif system == prompts.VERIFIER_SYSTEM:
            text = json.dumps({"verdict": self.verdicts.get(topic, "covered")})
        else:
            text = "초안"
        return LLMResponse(text=text, model="fake", finish_reason="stop",
                           prompt_tokens=10, completion_tokens=5, latency_s=0.01)


class TopicRetriever:
    """검색어에 든 항목별로 다른 후보를 돌려준다. 동시 진입 수를 잰다."""

    def __init__(self, *, events: list | None = None, hold_s: float = 0.0) -> None:
        self.events = events if events is not None else []
        self.hold_s = hold_s
        self.gauge = Gauge()
        self._lock = threading.Lock()

    def search(self, query: str, *, k: int = 4,
               sources: Sequence[str] | None = None) -> list[RetrievedChunk]:
        topic = TopicProvider.topic_of(query)
        with self._lock:
            self.events.append(("retrieve", topic))
        with self.gauge:
            time.sleep(self.hold_s)
        return [
            RetrievedChunk(doc_id=_doc(topic, n), locator="abstract", text=f"{topic} 본문 {n}",
                           source="arxiv", title="제목", score=0.5)
            for n in range(1, 4)
        ]


def _state(topics: Sequence[str], **extra) -> ResearchState:
    return ResearchState(query="질의", outline=list(topics), revision=0, **extra)


# ---------------------------------------------------------------------------
# 1. off = S0c 경로
# ---------------------------------------------------------------------------


def test_off_uses_no_pool_and_interleaves_retrieve_then_call_in_order(monkeypatch) -> None:
    def forbidden(*_a, **_k):
        raise AssertionError("상한 1인데 스레드 풀을 만들었다")

    monkeypatch.setattr(nodes, "ThreadPoolExecutor", forbidden)
    events: list = []
    node = make_researcher_node(TopicProvider(events=events),
                                retriever=TopicRetriever(events=events), max_concurrency=1)
    out = node(_state(TOPICS))

    expected = []
    for topic in TOPICS:
        expected += [("retrieve", topic), ("call", topic)]
    assert events == expected
    assert [f.topic for f in out["findings"]] == TOPICS

    verifier = make_verifier_node(TopicProvider(), max_concurrency=1)
    verifier(_state(TOPICS, findings=out["findings"]))  # 풀을 만들면 위 forbidden이 터진다


def test_node_factory_default_is_sequential_but_run_default_is_on() -> None:
    """팩토리 인자는 1(순차), 실행 기본값은 켜짐·상한 4 (ADR-026 Accepted, session-20)."""
    assert nodes.DEFAULT_MAX_CONCURRENCY == 1
    assert ConcurrencySettings().effective == 4
    assert ConcurrencySettings(parallel=False).effective == 1


def test_off_and_on_send_identical_inputs() -> None:
    off, on = TopicProvider(), TopicProvider(delays={TOPICS[0]: 0.05})
    for provider, cap in ((off, 1), (on, 4)):
        app = compile_graph(provider, retriever=TopicRetriever(), max_concurrency=cap)
        app.invoke(initial_state("질의", max_revisions=1), config={"recursion_limit": 50})

    def signature(provider: TopicProvider) -> list:
        return sorted(
            nodes._input_hash(m, temperature=0.0, max_tokens=mt) for _, m, mt in provider.calls
        )

    # Outliner 1 + Researcher 5 + Verifier 5 + Writer 1
    assert len(off.calls) == len(on.calls) == 12
    assert signature(off) == signature(on)


# ---------------------------------------------------------------------------
# 2. 결과 순서 보존
# ---------------------------------------------------------------------------


def test_results_keep_item_order_when_completion_order_is_reversed() -> None:
    # 앞 항목일수록 늦게 끝난다 → 완료 순서는 4,3,2,1,0
    delays = {t: 0.02 * (len(TOPICS) - i) for i, t in enumerate(TOPICS)}
    provider = TopicProvider(delays=delays, verdicts={TOPICS[1]: "uncovered",
                                                     TOPICS[3]: "uncovered"})
    researcher = make_researcher_node(provider, retriever=TopicRetriever(), max_concurrency=4)
    out = researcher(_state(TOPICS))

    assert provider.gauge.peak > 1  # 실제로 겹쳤다
    assert [f.topic for f in out["findings"]] == TOPICS
    assert [f.citations[0].doc_id for f in out["findings"]] == [_doc(t, 2) for t in TOPICS]
    assert len(out["trace"]) == len(TOPICS)

    verifier = make_verifier_node(provider, max_concurrency=4)
    verdict = verifier(_state(TOPICS, findings=out["findings"]))
    assert verdict["uncovered"] == [TOPICS[1], TOPICS[3]]
    assert [r.node for r in verdict["trace"]] == ["verifier"] * len(TOPICS)


def test_concurrency_respects_cap() -> None:
    provider = TopicProvider(delays={t: 0.03 for t in TOPICS})
    make_researcher_node(provider, retriever=TopicRetriever(), max_concurrency=2)(_state(TOPICS))
    assert provider.gauge.peak == 2


def test_verifier_skips_uncited_items_and_keeps_order_in_parallel() -> None:
    findings = [
        Finding(topic=t, citations=(Citation(doc_id=_doc(t, 1), locator="a", snippet="s"),)
                if i % 2 == 0 else (), revision=0)
        for i, t in enumerate(TOPICS)
    ]
    provider = TopicProvider(delays={TOPICS[0]: 0.05})
    out = make_verifier_node(provider, max_concurrency=4)(_state(TOPICS, findings=findings))
    assert out["uncovered"] == [TOPICS[1], TOPICS[3]]
    assert sorted(t for t, _, _ in provider.calls) == [TOPICS[0], TOPICS[2], TOPICS[4]]


# ---------------------------------------------------------------------------
# 3. 검색 직렬화
# ---------------------------------------------------------------------------


def test_retrieval_is_serialized_while_llm_calls_overlap(tmp_path) -> None:
    retriever = TopicRetriever(hold_s=0.02)
    provider = TopicProvider(delays={t: 0.05 for t in TOPICS})
    trace = LocalJsonlTracer(tmp_path).trace("run")
    make_researcher_node(provider, retriever=retriever, trace=trace,
                         max_concurrency=4)(_state(TOPICS))

    assert retriever.gauge.peak == 1  # 검색은 한 번에 하나
    assert provider.gauge.peak > 1  # LLM 호출은 겹친다
    spans = _spans(tmp_path, "researcher_retrieve")
    waits = [s["metadata"]["lock_wait_s"] for s in spans]
    assert len(waits) == len(TOPICS)
    assert max(waits) > 0.0  # 누군가는 락을 기다렸다
    # span 지연은 검색 자체만 잰다 (락 대기 제외)
    assert all(s["latency_s"] < 0.02 + 0.015 for s in spans)


def test_retrieval_lock_is_module_wide() -> None:
    """노드 인스턴스가 달라도 같은 락이다 — 막으려는 것이 인덱스 파일 경합이다."""
    retriever = TopicRetriever(hold_s=0.02)
    nodes_ = [make_researcher_node(TopicProvider(), retriever=retriever, max_concurrency=4)
              for _ in range(2)]
    threads = [threading.Thread(target=n, args=(_state(TOPICS),)) for n in nodes_]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert retriever.gauge.peak == 1


# ---------------------------------------------------------------------------
# 4. span-항목 귀속
# ---------------------------------------------------------------------------


def _spans(trace_dir: Path, name: str | None = None) -> list[dict]:
    (path,) = trace_dir.glob("*.jsonl")
    lines = path.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]  # 줄이 섞였으면 여기서 터진다
    return [r for r in records if name is None or r.get("name") == name]


def test_spans_are_attributed_to_their_item_and_bench_repairs_order(tmp_path) -> None:
    delays = {t: 0.02 * (len(TOPICS) - i) for i, t in enumerate(TOPICS)}
    provider = TopicProvider(delays=delays)
    trace = LocalJsonlTracer(tmp_path).trace("run")
    out = make_researcher_node(provider, retriever=TopicRetriever(), trace=trace,
                               max_concurrency=4)(_state(TOPICS))

    calls = _spans(tmp_path, "researcher_call")
    # 완료 순서로 쓰였다 — 이 테스트가 순서 뒤집힘을 실제로 만든다
    assert [c["metadata"]["topic"] for c in calls] != TOPICS
    expected_hash = {t: nodes._input_hash(m, temperature=0.0, max_tokens=mt)
                     for t, m, mt in provider.calls}
    for span in calls:
        topic = span["metadata"]["topic"]
        assert span["metadata"]["item_index"] == TOPICS.index(topic)
        assert f"{topic} 본문" in span["input"]["user"]
        assert span["metadata"]["input_hash"] == expected_hash[topic]

    items = bench_golden.item_records(out["findings"], _spans(tmp_path))
    assert items is not None
    for topic, item in zip(TOPICS, items, strict=True):
        assert item["topic"] == topic
        assert [c["doc_id"] for c in item["candidates"]] == [_doc(topic, n) for n in (1, 2, 3)]
        assert item["supporting"] == [_doc(topic, 2)]
        assert item["input_hash"] == expected_hash[topic]


def test_bench_pairs_retry_items_by_revision_and_index(tmp_path) -> None:
    trace = LocalJsonlTracer(tmp_path).trace("run")
    provider = TopicProvider(delays={TOPICS[0]: 0.04})
    node = make_researcher_node(provider, retriever=TopicRetriever(), trace=trace,
                                max_concurrency=4)
    first = node(_state(TOPICS[:3]))
    retry = node(ResearchState(query="질의", outline=TOPICS[:3], revision=1,
                               uncovered=[TOPICS[2], TOPICS[0]]))
    findings = first["findings"] + retry["findings"]
    items = bench_golden.item_records(findings, _spans(tmp_path))
    assert [(i["topic"], i["revision"], i["item_index"]) for i in items] == [
        (TOPICS[0], 0, 0), (TOPICS[1], 0, 1), (TOPICS[2], 0, 2),
        (TOPICS[2], 1, 0), (TOPICS[0], 1, 1),
    ]
    assert all(i["candidates"][0]["doc_id"] == _doc(i["topic"], 1) for i in items)


def test_jsonl_lines_do_not_interleave_under_threads(tmp_path) -> None:
    trace = LocalJsonlTracer(tmp_path).trace("run")
    big = "가" * 3000

    def write(n: int) -> None:
        for i in range(50):
            trace.span(f"s{n}", input={"blob": big, "i": i}).end(output=big)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(_spans(tmp_path)) == 1 + 8 * 50


def test_failed_call_span_keeps_item_and_hash(tmp_path) -> None:
    from src.providers import LLMError

    class Failing(TopicProvider):
        def complete(self, messages, **kwargs):
            if TOPICS[1] in messages[1].content:
                raise LLMError("429 Too Many Requests")
            return super().complete(messages, **kwargs)

    trace = LocalJsonlTracer(tmp_path).trace("run")
    out = make_researcher_node(Failing(), retriever=TopicRetriever(), trace=trace,
                               max_concurrency=4)(_state(TOPICS[:3]))
    assert out["findings"][1].citations == ()
    (failed,) = [s for s in _spans(tmp_path, "researcher_call") if s["metadata"].get("error")]
    assert failed["metadata"]["topic"] == TOPICS[1] and failed["metadata"]["input_hash"]
    assert bench_golden.endpoint_errors(_spans(tmp_path)) == {
        "total": 1, "rate_limit": 1, "timeout": 0, "other": 0}


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------


def test_concurrency_settings_from_env(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_PARALLEL", "on")
    monkeypatch.setenv("RESEARCH_MAX_CONCURRENCY", "3")
    settings = load_concurrency_settings(env_file=None)
    assert (settings.parallel, settings.max_concurrency, settings.effective) == (True, 3, 3)

    # 비워두면 켜짐(상한 4)
    monkeypatch.setenv("RESEARCH_PARALLEL", "")
    monkeypatch.delenv("RESEARCH_MAX_CONCURRENCY")
    settings = load_concurrency_settings(env_file=None)
    assert (settings.parallel, settings.max_concurrency, settings.effective) == (True, 4, 4)

    for raw in ("off", "0", "false", "NO"):
        monkeypatch.setenv("RESEARCH_PARALLEL", raw)
        settings = load_concurrency_settings(env_file=None)
        assert (settings.parallel, settings.effective) == (False, 1)


def test_parallel_flag_typo_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_PARALLEL", "of")
    with pytest.raises(ConfigError):
        load_concurrency_settings(env_file=None)


@pytest.mark.parametrize("raw", ["0", "-1", "four"])
def test_concurrency_cap_is_validated(monkeypatch, raw: str) -> None:
    monkeypatch.setenv("RESEARCH_MAX_CONCURRENCY", raw)
    with pytest.raises(ConfigError):
        load_concurrency_settings(env_file=None)
