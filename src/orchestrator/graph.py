"""LangGraph 그래프 배선.

검증 루프를 조건부 엣지(사이클)로 표현한다 — 루프 제어가 프레임워크 바깥으로
새지 않고 그래프 정의 안에 남게 하기 위함이다 (ADR-002 Rationale 2).

    START -> outliner -> researcher -> verifier --(uncovered 남음)--> researcher
                                          |
                                          +--(근거 충족 / 상한 도달)--> writer -> END
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from langgraph.graph import END, START, StateGraph

from src.obs import RunTrace
from src.providers import LLMProvider, get_provider
from src.tools.retrieval import Retriever
from src.tools.scope import ScopedRetriever, ToolScope

from .nodes import (
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_MIN_CITATIONS,
    DEFAULT_TOP_K,
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


def build_graph(
    provider: LLMProvider | None = None,
    *,
    retriever: Retriever | None = None,
    trace: RunTrace | None = None,
    top_k: int = DEFAULT_TOP_K,
    min_citations: int = DEFAULT_MIN_CITATIONS,
    sources: Sequence[str] | None = None,
    scope: ToolScope | None = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    tech_domain_vocab: Iterable[str] | None = None,
) -> StateGraph:
    """그래프를 구성한다 (컴파일 전).

    프로바이더·검색 툴·tracer를 인자로 받는 이유: 노드가 모듈 로드 시점에
    엔드포인트나 인덱스를 붙잡지 않게 하려는 것이다. 테스트는 가짜 프로바이더로
    구조만 검증할 수 있고, 실행 스크립트는 실행 1건에 대응하는 trace를 주입한다.

    `retriever`가 None이면 검색 없이 동작한다 — 모든 항목이 "근거 없음"이 되므로
    구조 검증용이다. 실제 실행은 `scripts/run_research.py`가 검색 툴을 주입한다.

    **툴 스코프는 여기서 강제된다 (ADR-009).** 주입된 검색 툴이 무엇이든
    `ScopedRetriever`로 감싸므로, 호출자가 스코프를 빠뜨려서 넓은 권한이 새는
    경로가 없다. 스코프를 노드나 스크립트에 맡기면 새 호출 지점이 생길 때마다
    빠뜨릴 수 있다 — 그래서 그래프 배선이라는 **단일 길목**에 둔다.

    `max_concurrency`는 Researcher·Verifier **노드 안** 동시 호출 상한이다(1 = 순차).
    그래프 구조는 이 값과 무관하게 같다 — 팬아웃(`Send`)을 쓰지 않는다 (ADR-026).
    환경변수로 켜는 값은 호출자가 `load_concurrency_settings().effective`로 넘긴다.

    `tech_domain_vocab`을 주면 Researcher의 `tech_domain` 선택 도구가 켜진다(v1.2-T2, ADR-028).
    None이면 꺼져 있고 그래프는 v1.2-P1과 같은 호출을 만든다. 켜는 것은 호출자가
    `load_tool_settings()`를 읽어 정한다 — 노드가 환경변수를 직접 읽지 않는다.
    """
    llm = provider or get_provider()

    effective_scope = scope or ToolScope()
    if sources:
        # 호출자가 출처를 지정하면 스코프를 그만큼 좁힌다. 넓히려 하면 예외다.
        effective_scope = effective_scope.narrow(sources)
    if retriever is not None and not isinstance(retriever, ScopedRetriever):
        retriever = ScopedRetriever(retriever, effective_scope)

    graph = StateGraph(ResearchState)

    graph.add_node(OUTLINER, make_outliner_node(llm, trace=trace))
    graph.add_node(
        RESEARCHER,
        make_researcher_node(
            llm, retriever=retriever, trace=trace, top_k=top_k, sources=sources,
            max_concurrency=max_concurrency, tech_domain_vocab=tech_domain_vocab,
        ),
    )
    graph.add_node(
        VERIFIER,
        make_verifier_node(
            llm, trace=trace, min_citations=min_citations, max_concurrency=max_concurrency
        ),
    )
    graph.add_node(WRITER, make_writer_node(llm, trace=trace))

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


def compile_graph(
    provider: LLMProvider | None = None,
    *,
    retriever: Retriever | None = None,
    trace: RunTrace | None = None,
    top_k: int = DEFAULT_TOP_K,
    min_citations: int = DEFAULT_MIN_CITATIONS,
    sources: Sequence[str] | None = None,
    scope: ToolScope | None = None,
    checkpointer=None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    tech_domain_vocab: Iterable[str] | None = None,
):
    """실행 가능한 그래프.

    `checkpointer`는 배치성 장시간 실행의 중단·재개용이다. 백엔드 선택은
    아직 열려 있어(ADR-002 Implementation) 인자로만 받아둔다.
    """
    return build_graph(
        provider,
        retriever=retriever,
        trace=trace,
        top_k=top_k,
        min_citations=min_citations,
        sources=sources,
        scope=scope,
        max_concurrency=max_concurrency,
        tech_domain_vocab=tech_domain_vocab,
    ).compile(checkpointer=checkpointer)
