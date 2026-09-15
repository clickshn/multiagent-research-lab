"""노드 함수 골격.

컨벤션(`.claude/rules/orchestrator.md`): 노드는 State를 받아 State를 반환하는
순수 함수다. 모델 호출 같은 부작용은 노드가 직접 만들지 않고, 팩토리가 주입한
프로바이더를 통해서만 일어난다 — 그래서 각 노드는 `make_*_node(provider)`로
만들어지고, 반환된 함수 자체는 (provider를 고정한 상태에서) State -> State다.

프로바이더를 임포트 시점이 아니라 팩토리 인자로 받는 이유: 테스트에서 가짜
프로바이더를 끼워 넣어 그래프 구조만 따로 검증할 수 있게 하기 위함이다.

현재 상태: 시그니처와 그래프 배선만 확정하고 내부 로직은 비어 있다 (session-03 구현 예정).
각 노드는 아직 빈 부분 갱신을 반환하므로 그래프는 끝까지 traverse 되지만
의미 있는 산출물은 만들지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable

from src.providers import LLMProvider

from .state import ResearchState

# 노드의 계약: State를 받아 "이번에 바뀐 키만" 담은 부분 갱신을 돌려준다.
NodeFn = Callable[[ResearchState], ResearchState]

# 라우터의 계약: State를 읽기만 하고 다음 노드 이름을 돌려준다.
RouterFn = Callable[[ResearchState], str]


def make_outliner_node(provider: LLMProvider) -> NodeFn:
    """질의를 조사 항목 목록으로 분해한다.

    반환 키: `outline`, `trace`
    """

    def outliner(state: ResearchState) -> ResearchState:
        # TODO(session-03): provider.complete()로 query -> 조사 항목 목록 분해.
        #   출력은 파싱 가능한 형태로 강제하고, 실패 시 재시도가 아니라
        #   빈 outline을 반환해 라우터가 판단하게 한다.
        return ResearchState()

    return outliner


def make_researcher_node(provider: LLMProvider) -> NodeFn:
    """조사 항목별로 근거 문서를 검색·추출한다.

    재검색 루프에서 여러 번 호출되며, 매번 `uncovered`에 남은 항목만 다시 다룬다.
    근거를 찾지 못한 항목은 citations가 빈 Finding으로 남긴다 — 조용히 누락시키지
    않아야 검증 단계가 그 사실을 볼 수 있다.

    반환 키: `findings`(누적), `revision`, `trace`
    """

    def researcher(state: ResearchState) -> ResearchState:
        # TODO(session-03): VectorDB 검색 툴 호출 + provider.complete()로 근거 추출.
        #   툴 스코프는 화이트리스트로 제한한다 (.claude/rules/security.md).
        return ResearchState(revision=state.get("revision", 0) + 1)

    return researcher


def make_verifier_node(provider: LLMProvider) -> NodeFn:
    """각 조사 항목에 실제로 근거가 붙었는지 판정한다.

    Writer 앞에 두는 이유: 근거 없는 항목을 Writer에게 넘기면 모델이 그 빈칸을
    메우려 든다. 근거 유무 판정을 쓰기 전에 끝내야 "근거 없으면 문장을 만들지
    않는다"는 원칙이 구조적으로 지켜진다.

    반환 키: `uncovered`, `trace`
    """

    def verifier(state: ResearchState) -> ResearchState:
        # TODO(session-03): findings의 coverage 집계 + 근거-주장 정합성 판정.
        #   1차는 citations 유무만 보고, 정합성 판정(근거가 실제로 그 주장을
        #   뒷받침하는가)은 골든셋 확보 후 추가한다.
        return ResearchState(uncovered=[])

    return verifier


def make_writer_node(provider: LLMProvider) -> NodeFn:
    """근거에 붙은 초안을 작성한다.

    Researcher가 반환한 근거 범위 밖의 주장은 생성하지 않는다. 근거가 없는 항목은
    본문에 "근거 없음"으로 표시한다 (docs/architecture.md 설계 원칙).

    반환 키: `draft`, `trace`
    """

    def writer(state: ResearchState) -> ResearchState:
        # TODO(session-03): provider.complete()로 findings 기반 초안 생성.
        #   각 주장 문장에 Citation을 붙이고, uncovered 항목은 명시적으로 표기한다.
        return ResearchState()

    return writer


def route_after_verify(state: ResearchState) -> str:
    """검증 후 분기 — 재검색할지 초안 작성으로 넘어갈지.

    라우터는 State를 읽기만 하는 순수 함수이며 모델을 호출하지 않는다.
    루프 종료 조건을 여기 한 곳에 모아두어야 "왜 루프가 멈췄는가"를
    그래프 정의만 보고 답할 수 있다.
    """
    uncovered = state.get("uncovered") or []
    if not uncovered:
        return "write"

    if state.get("revision", 0) >= state.get("max_revisions", 0):
        # 상한 도달: 남은 항목은 "근거 없음"으로 표시된 채 초안으로 넘어간다.
        return "write"

    return "retry"
