"""그래프 골격 구조 검증.

노드 내부 로직은 아직 비어 있으므로 산출물이 아니라 **구조**를 검증한다:
컴파일되는가, 네 노드를 모두 거치는가, 검증 루프가 상한에서 멈추는가.
가짜 프로바이더를 쓰므로 실제 엔드포인트를 호출하지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from src.orchestrator import compile_graph, initial_state
from src.orchestrator.nodes import route_after_verify
from src.providers import ChatMessage, LLMResponse


class StubProvider:
    """호출되면 기록만 남기는 프로바이더. 네트워크를 타지 않는다."""

    def __init__(self) -> None:
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
        return LLMResponse(
            text="",
            model="stub",
            finish_reason="stop",
            prompt_tokens=0,
            completion_tokens=0,
            latency_s=0.0,
        )


def test_graph_compiles_and_terminates() -> None:
    app = compile_graph(StubProvider())
    result = app.invoke(initial_state("테스트 질의", max_revisions=2))

    # 골격 단계에서는 상태 키가 보존되는 것까지만 보장한다.
    assert result["query"] == "테스트 질의"
    assert "draft" in result


def test_graph_has_verification_cycle() -> None:
    """검증 루프가 그래프 정의 안에 사이클로 존재하는지."""
    app = compile_graph(StubProvider())
    edges = app.get_graph().edges
    pairs = {(e.source, e.target) for e in edges}

    assert ("verifier", "researcher") in pairs, "재검색 사이클이 없다"
    assert ("verifier", "writer") in pairs
    assert ("researcher", "verifier") in pairs


@pytest.mark.parametrize(
    ("state_patch", "expected"),
    [
        ({"uncovered": [], "revision": 0}, "write"),
        ({"uncovered": ["A"], "revision": 0}, "retry"),
        # 상한 도달 시 무한 루프 대신 "근거 없음"인 채로 초안으로 넘어간다.
        ({"uncovered": ["A"], "revision": 2}, "write"),
    ],
)
def test_route_after_verify(state_patch: dict, expected: str) -> None:
    state = initial_state("q", max_revisions=2)
    state.update(state_patch)
    assert route_after_verify(state) == expected
