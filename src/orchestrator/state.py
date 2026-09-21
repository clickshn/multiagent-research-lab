"""LangGraph State 스키마.

컨벤션(`.claude/rules/orchestrator.md`): State는 TypedDict + Annotated reducer로
정의하고, 노드는 State를 받아 State(부분 갱신)를 반환하는 순수 함수로 유지한다.

reducer를 쓰는 이유: 노드가 기존 값을 읽어 덧붙이는 대신 "이번에 새로 만든 것"만
반환하게 하려는 것이다. 누적 책임이 State 정의 한 곳에 모이면 재검색 루프가
여러 번 돌아도 노드가 누적 로직을 중복해서 갖지 않는다.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Annotated, Literal, TypedDict

# 근거를 찾지 못한 항목의 처리 상태. "근거 없으면 문장을 만들지 않는다"는
# 원칙(docs/problem-statement.md §3)을 State에서 표현하기 위한 타입.
Coverage = Literal["covered", "uncovered"]


@dataclass(frozen=True)
class Citation:
    """근거 1건의 출처. 검색 결과에는 항상 문서 ID와 위치가 함께 온다.

    **온톨로지 메타(session-16, ADR-024).** 문서의 종류(`release_type`)·기술 영역
    (`tech_domains`)·발행일을 함께 들고 다닌다. 값은 검색 결과(`RetrievedChunk`)에서
    그대로 옮겨오며, **모델을 거치지 않는다.**

    ⚠️ **이 값들은 Writer의 LLM 입력에 들어가지 않는다.** 출처 표를 코드가 렌더링할 때만
    쓴다 — 모델이 옮겨 적으면 `Community`를 `Paper`로 잘못 쓸 수 있고, 그러면 감사
    가능성이 모델 정확도에 걸린다 (ADR-024, ADR-006 "셀 수 있는 것은 모델에게 묻지 않는다").
    """

    doc_id: str
    locator: str  # 페이지/섹션/청크 등 문서 내 위치
    snippet: str
    published: str = ""
    release_type: str = ""
    tech_domains: tuple[str, ...] = ()
    # 온톨로지 export를 거친 문서인가. "값이 없음"과 "확인 안 됨"을 가르는 유일한 근거다.
    has_ontology: bool = False


@dataclass(frozen=True)
class Finding:
    """조사 항목 1건에 대해 Researcher가 모은 근거."""

    topic: str
    citations: tuple[Citation, ...] = ()
    # 이 근거를 만든 재검색 회차 (0 = 최초 조사). 재검색 루프가 **새 근거를
    # 실제로 찾았는지**를 사후에 판정하려면, 근거마다 언제 들어왔는지가 남아야
    # 한다. session-03에서 "재검색 2회를 돌았지만 새 근거 0건"이라는 관찰이
    # 나왔는데, 그때는 이 값이 없어 로그를 손으로 대조해야 했다.
    revision: int = 0

    @property
    def coverage(self) -> Coverage:
        return "covered" if self.citations else "uncovered"


@dataclass(frozen=True)
class LLMCallRecord:
    """감사 로그 1건.

    CLAUDE.md의 "모든 에이전트 호출과 툴 호출은 감사 로그로 남을 수 있어야 한다"를
    State 안에서 충족한다. Langfuse 연동 전에도 그래프 실행 결과만으로 추적 가능하다.
    """

    node: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    revision: int
    # 캐시 적중으로 돌려받은 호출인지 (ADR-008). 적중 호출은 엔드포인트에
    # 토큰이 청구되지 않으므로, 비용 집계가 이 플래그로 둘을 나눠 센다.
    cached: bool = False


class ResearchState(TypedDict, total=False):
    """그래프 전체가 공유하는 상태.

    total=False — 각 노드는 자기가 바꾼 키만 담은 부분 갱신을 반환한다.
    """

    # --- 입력 ---
    query: str

    # --- Outliner 산출물 ---
    # 재검색 루프에서 개요를 다시 쓰지 않으므로 누적이 아니라 덮어쓴다.
    outline: list[str]

    # --- Researcher 산출물 (루프마다 누적) ---
    findings: Annotated[list[Finding], operator.add]

    # --- 검증 결과 ---
    # 근거를 아직 찾지 못한 조사 항목. 비면 Writer로 진행한다.
    uncovered: list[str]

    # --- Writer 산출물 ---
    draft: str

    # --- 루프 제어 ---
    revision: int
    max_revisions: int

    # --- 계측 / 감사 로그 (전 노드 누적) ---
    trace: Annotated[list[LLMCallRecord], operator.add]


def initial_state(query: str, *, max_revisions: int = 2) -> ResearchState:
    """그래프 진입 상태.

    `max_revisions`는 재검색 루프의 상한이다. 근거를 못 찾는 항목이 남아도
    무한히 돌지 않고 "근거 없음"으로 표시한 채 종료하기 위한 안전장치다.
    """
    return ResearchState(
        query=query,
        outline=[],
        findings=[],
        uncovered=[],
        draft="",
        revision=0,
        max_revisions=max_revisions,
        trace=[],
    )
