"""LangGraph 그래프 배선.

검증 루프를 조건부 엣지(사이클)로 표현한다 — 루프 제어가 프레임워크 바깥으로
새지 않고 그래프 정의 안에 남게 하기 위함이다 (ADR-002 Rationale 2).

    START -> outliner -> researcher -> verifier --(uncovered 남음)--> researcher
                                          |
                                          +--(근거 충족 / 상한 도달)--> writer -> END
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.providers import LLMProvider, get_provider

from .nodes import (
    make_outliner_node,
    make_researcher_node,
    make_verifier_node,
    make_writer_node,
    route_after_verify,
)
from .state import ResearchState

OUTLINER = "outliner"
RESEARCHER = "researcher"
VERIFIER = "verifier"
WRITER = "writer"


def build_graph(provider: LLMProvider | None = None) -> StateGraph:
    """그래프를 구성한다 (컴파일 전).

    프로바이더를 인자로 받는 이유: 노드가 모듈 로드 시점에 엔드포인트를 붙잡지
    않게 하려는 것이다. 테스트는 가짜 프로바이더로 구조만 검증할 수 있다.
    """
    llm = provider or get_provider()

    graph = StateGraph(ResearchState)

    graph.add_node(OUTLINER, make_outliner_node(llm))
    graph.add_node(RESEARCHER, make_researcher_node(llm))
    graph.add_node(VERIFIER, make_verifier_node(llm))
    graph.add_node(WRITER, make_writer_node(llm))

    graph.add_edge(START, OUTLINER)
    graph.add_edge(OUTLINER, RESEARCHER)
    graph.add_edge(RESEARCHER, VERIFIER)

    # 검증 루프. "retry"는 RESEARCHER로 되돌아가는 사이클이다.
    graph.add_conditional_edges(
        VERIFIER,
        route_after_verify,
        {"retry": RESEARCHER, "write": WRITER},
    )

    graph.add_edge(WRITER, END)

    return graph


def compile_graph(provider: LLMProvider | None = None, *, checkpointer=None):
    """실행 가능한 그래프.

    `checkpointer`는 배치성 장시간 실행의 중단·재개용이다. 백엔드 선택은
    아직 열려 있어(ADR-002 Implementation) 인자로만 받아둔다.
    """
    return build_graph(provider).compile(checkpointer=checkpointer)
