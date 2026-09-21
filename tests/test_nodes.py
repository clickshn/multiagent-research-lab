"""노드 내부 로직 검증.

가짜 프로바이더·가짜 검색 툴을 끼워 네트워크 없이 노드 동작만 본다. 실제 모델이
어떤 문장을 쓰는지가 아니라 **계약**을 고정하는 것이 목적이다: 형식이 깨졌을 때
무엇이 되는가, 근거가 없을 때 무엇이 되는가, 판정이 실패하면 어느 쪽으로 기우는가.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from src.orchestrator.nodes import (
    make_outliner_node,
    make_researcher_node,
    make_verifier_node,
    make_writer_node,
    route_after_verify,
)
from src.orchestrator.state import Citation, Finding, ResearchState, initial_state
from src.providers import ChatMessage, LLMError, LLMResponse
from src.tools.retrieval import RetrievalError, RetrievedChunk


class ScriptedProvider:
    """미리 정해둔 응답을 순서대로 돌려주는 프로바이더."""

    def __init__(self, responses: Sequence[str | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[Sequence[ChatMessage]] = []

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        stop: Sequence[str] | None = None,
    ) -> LLMResponse:
        self.calls.append(messages)
        item = self._responses.pop(0) if self._responses else ""
        if isinstance(item, Exception):
            raise item
        return LLMResponse(
            text=item,
            model="stub",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=5,
            latency_s=0.01,
        )


class StubRetriever:
    def __init__(self, chunks: Sequence[RetrievedChunk] | Exception) -> None:
        self._chunks = chunks
        self.queries: list[str] = []

    def search(
        self, query: str, *, k: int = 4, sources: Sequence[str] | None = None
    ) -> list[RetrievedChunk]:
        self.queries.append(query)
        if isinstance(self._chunks, Exception):
            raise self._chunks
        return list(self._chunks)


def _chunk(doc_id: str, text: str = "근거 본문") -> RetrievedChunk:
    return RetrievedChunk(
        doc_id=doc_id, locator="abstract", text=text, source="arxiv", title="제목", score=0.9
    )


# ---------------------------------------------------------------------------
# Outliner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        '["항목 A", "항목 B"]',
        '```json\n["항목 A", "항목 B"]\n```',
        '알겠습니다. ["항목 A", "항목 B"] 입니다.',
    ],
)
def test_outliner_parses_tolerantly(raw: str) -> None:
    """코드펜스나 머리말이 붙어도 파싱한다 — 그때마다 재호출하면 비용만 는다."""
    node = make_outliner_node(ScriptedProvider([raw]))
    result = node(initial_state("질의"))
    assert result["outline"] == ["항목 A", "항목 B"]


def test_outliner_returns_empty_outline_on_bad_format() -> None:
    """형식을 끝내 못 맞추면 예외가 아니라 빈 outline이다."""
    node = make_outliner_node(ScriptedProvider(["JSON이 아닌 응답"]))
    assert node(initial_state("질의"))["outline"] == []


def test_outliner_survives_call_failure() -> None:
    node = make_outliner_node(ScriptedProvider([LLMError("endpoint down")]))
    assert node(initial_state("질의"))["outline"] == []


# ---------------------------------------------------------------------------
# Researcher
# ---------------------------------------------------------------------------


def test_researcher_attaches_selected_citations() -> None:
    provider = ScriptedProvider(['{"supporting": [1], "note": "요약"}'])
    retriever = StubRetriever([_chunk("arXiv:1"), _chunk("arXiv:2")])
    node = make_researcher_node(provider, retriever=retriever)

    state = initial_state("질의")
    state["outline"] = ["항목 A"]
    result = node(state)

    findings = result["findings"]
    assert len(findings) == 1
    assert [c.doc_id for c in findings[0].citations] == ["arXiv:1"]
    assert findings[0].coverage == "covered"
    assert result["revision"] == 1


def test_researcher_keeps_empty_finding_when_nothing_supports() -> None:
    """뒷받침하는 후보가 없으면 항목을 조용히 누락시키지 않고 빈 Finding으로 남긴다."""
    provider = ScriptedProvider(['{"supporting": [], "note": ""}'])
    node = make_researcher_node(provider, retriever=StubRetriever([_chunk("arXiv:1")]))

    state = initial_state("질의")
    state["outline"] = ["항목 A"]
    findings = node(state)["findings"]

    assert len(findings) == 1
    assert findings[0].coverage == "uncovered"


def test_researcher_ignores_out_of_range_indices() -> None:
    """모델이 없는 후보 번호를 지어내면 버린다."""
    provider = ScriptedProvider(['{"supporting": [1, 7, 0, -2], "note": ""}'])
    node = make_researcher_node(provider, retriever=StubRetriever([_chunk("arXiv:1")]))

    state = initial_state("질의")
    state["outline"] = ["항목 A"]
    citations = node(state)["findings"][0].citations

    assert [c.doc_id for c in citations] == ["arXiv:1"]


def test_researcher_bad_format_yields_no_citations() -> None:
    """파싱 실패를 '상위 후보를 그냥 쓴다'로 처리하지 않는다.

    그렇게 하면 모델이 '뒷받침하는 문서 없음'이라고 판단한 경우와 형식을 어긴 경우를
    구분할 수 없어, 근거 없는 인용이 보고서에 실린다.
    """
    provider = ScriptedProvider(["형식을 어긴 응답"])
    node = make_researcher_node(provider, retriever=StubRetriever([_chunk("arXiv:1")]))

    state = initial_state("질의")
    state["outline"] = ["항목 A"]
    assert node(state)["findings"][0].citations == ()


def test_researcher_retry_targets_only_uncovered() -> None:
    """재검색은 아직 근거를 못 찾은 항목만 다룬다."""
    provider = ScriptedProvider(['{"supporting": [1], "note": ""}'])
    retriever = StubRetriever([_chunk("arXiv:9")])
    node = make_researcher_node(provider, retriever=retriever)

    state = initial_state("질의")
    state["outline"] = ["항목 A", "항목 B"]
    state["uncovered"] = ["항목 B"]
    state["revision"] = 1
    findings = node(state)["findings"]

    assert [f.topic for f in findings] == ["항목 B"]


def test_researcher_without_retriever_yields_uncovered() -> None:
    """검색 툴이 없으면 모델을 부르지 않고 전부 근거 없음이다."""
    provider = ScriptedProvider([])
    node = make_researcher_node(provider, retriever=None)

    state = initial_state("질의")
    state["outline"] = ["항목 A"]
    result = node(state)

    assert result["findings"][0].coverage == "uncovered"
    assert provider.calls == []


def test_researcher_survives_retrieval_error() -> None:
    node = make_researcher_node(
        ScriptedProvider([]), retriever=StubRetriever(RetrievalError("인덱스 없음"))
    )
    state = initial_state("질의")
    state["outline"] = ["항목 A"]
    assert node(state)["findings"][0].coverage == "uncovered"


# ---------------------------------------------------------------------------
# Verifier (ADR-006)
# ---------------------------------------------------------------------------


def _state_with_findings(*findings: Finding) -> ResearchState:
    state = initial_state("질의")
    state["outline"] = [f.topic for f in findings]
    state["findings"] = list(findings)
    return state


def test_verifier_skips_model_when_no_citations() -> None:
    """인용이 없는 항목은 모델을 부르지 않는다 — 셀 수 있는 것을 묻지 않는다."""
    provider = ScriptedProvider([])
    node = make_verifier_node(provider)

    result = node(_state_with_findings(Finding(topic="항목 A", citations=())))

    assert result["uncovered"] == ["항목 A"]
    assert provider.calls == []


def test_verifier_passes_when_model_says_covered() -> None:
    provider = ScriptedProvider(['{"verdict": "covered", "reason": "뒷받침함"}'])
    node = make_verifier_node(provider)
    finding = Finding(topic="항목 A", citations=(Citation("arXiv:1", "abstract", "본문"),))

    assert node(_state_with_findings(finding))["uncovered"] == []


@pytest.mark.parametrize(
    "raw",
    [
        '{"verdict": "uncovered", "reason": "주제만 비슷함"}',
        "형식을 어긴 응답",
        '{"reason": "verdict 키가 없음"}',
    ],
)
def test_verifier_is_conservative(raw: str) -> None:
    """애매하거나 형식이 깨지면 통과시키지 않는다."""
    node = make_verifier_node(ScriptedProvider([raw]))
    finding = Finding(topic="항목 A", citations=(Citation("arXiv:1", "abstract", "본문"),))

    assert node(_state_with_findings(finding))["uncovered"] == ["항목 A"]


def test_verifier_does_not_pass_on_call_failure() -> None:
    """검증 실패를 통과로 처리하면 검증 단계가 있으나 마나가 된다."""
    node = make_verifier_node(ScriptedProvider([LLMError("endpoint down")]))
    finding = Finding(topic="항목 A", citations=(Citation("arXiv:1", "abstract", "본문"),))

    assert node(_state_with_findings(finding))["uncovered"] == ["항목 A"]


def test_verifier_merges_citations_across_revisions() -> None:
    """reducer로 누적된 같은 항목의 근거를 합쳐서 판정한다."""
    provider = ScriptedProvider(['{"verdict": "covered", "reason": ""}'])
    node = make_verifier_node(provider, min_citations=2)

    state = initial_state("질의")
    state["outline"] = ["항목 A"]
    # 1회차와 2회차가 각각 1건씩 찾은 상태 — 합치면 2건이라 기계 판정을 통과한다.
    state["findings"] = [
        Finding(topic="항목 A", citations=(Citation("arXiv:1", "abstract", "본문1"),)),
        Finding(topic="항목 A", citations=(Citation("arXiv:2", "abstract", "본문2"),)),
    ]

    assert node(state)["uncovered"] == []
    assert len(provider.calls) == 1


def test_verifier_respects_min_citations() -> None:
    node = make_verifier_node(ScriptedProvider([]), min_citations=2)
    finding = Finding(topic="항목 A", citations=(Citation("arXiv:1", "abstract", "본문"),))

    assert node(_state_with_findings(finding))["uncovered"] == ["항목 A"]


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def test_writer_marks_uncovered_topics_in_prompt() -> None:
    """근거 없는 항목은 '근거 없음'으로 넘어가지, 빈칸으로 넘어가지 않는다."""
    provider = ScriptedProvider(["# 보고서"])
    node = make_writer_node(provider)

    state = initial_state("질의")
    state["outline"] = ["항목 A", "항목 B"]
    state["uncovered"] = ["항목 B"]
    state["findings"] = [
        Finding(topic="항목 A", citations=(Citation("arXiv:1", "abstract", "본문"),))
    ]
    result = node(state)

    # 본문은 모델이 쓴 그대로 앞에 남고, 출처 표만 코드가 뒤에 붙는다 (ADR-024).
    assert result["draft"].startswith("# 보고서")
    assert "## 출처" in result["draft"]
    prompt = provider.calls[0][1].content
    assert "근거 없음" in prompt
    assert "arXiv:1" in prompt


def test_writer_does_not_invent_when_outline_is_empty() -> None:
    """조사 항목이 없으면 모델을 부르지 않고 그 사실을 초안으로 남긴다."""
    provider = ScriptedProvider([])
    node = make_writer_node(provider)

    result = node(initial_state("질의"))

    assert "Outliner 단계 실패" in result["draft"]
    assert provider.calls == []


def test_writer_reports_failure_instead_of_empty_draft() -> None:
    node = make_writer_node(ScriptedProvider([LLMError("endpoint down")]))
    state = initial_state("질의")
    state["outline"] = ["항목 A"]

    assert "실패" in node(state)["draft"]


# ---------------------------------------------------------------------------
# 라우터 (ADR-006)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        ({"uncovered": [], "revision": 0}, "write"),
        ({"uncovered": ["A"], "revision": 0}, "retry"),
        ({"uncovered": ["A"], "revision": 1}, "retry"),
        ({"uncovered": ["A"], "revision": 2}, "write"),
        ({"uncovered": ["A"], "revision": 3}, "write"),
    ],
)
def test_route_after_verify_stops_at_cap(patch: dict, expected: str) -> None:
    state = initial_state("q", max_revisions=2)
    state.update(patch)
    assert route_after_verify(state) == expected
