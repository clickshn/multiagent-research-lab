"""툴 호출 스코프 — 화이트리스트 강제 계층 (ADR-009).

`.claude/rules/security.md`의 컨벤션은 "모든 툴 호출은 화이트리스트 스코프를
벗어나면 실패해야 함"이다. session-04까지 이 규칙은 **인덱싱 경로에만** 강제돼
있었다 (`corpus.assert_allowed_source`). 검색 경로는 `sources=None`이면 인덱스
전체를 조회했고, 인덱스 내용물이 곧 스코프였다 — 즉 **정책이 데이터에 의존**했다.

이 모듈은 그 의존을 끊는다. 스코프는 호출자가 명시적으로 선언하고, 위반은 빈
결과가 아니라 예외다.

## 왜 예외인가

빈 결과를 돌려주면 "스코프 밖이라 막혔다"와 "검색 결과가 없다"가 구분되지 않는다.
후자는 정상 동작이고 전자는 보안 사건이다. 둘이 같은 신호를 내면 로그를 봐도
공격을 발견할 수 없다.

## 이중 검사 (ingress + egress)

들어가는 요청(`sources` 인자)만 검사하면, **인덱스에 이미 들어와 있는** 범위 밖
문서를 막지 못한다 — 화이트리스트가 생기기 전에 인덱싱됐거나, 다른 프로세스가
같은 디렉터리에 쓴 경우다. 그래서 결과물의 `source`도 다시 검사해서 떨어뜨린다.
정책 계층은 데이터가 이미 오염된 상태를 전제해야 한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .corpus import ALLOWED_SOURCES
from .retrieval import RetrievedChunk, Retriever


class ToolScopeError(RuntimeError):
    """툴 호출이 허용된 스코프를 벗어났다.

    `CorpusScopeError`(반입 범위 위반, ADR-004)와 구분한다 — 이쪽은 실행 시점의
    툴 호출 권한 문제이고, 저쪽은 인덱스에 무엇을 넣을지의 문제다. 원인이 다르면
    대응도 다르므로 예외 타입을 합치지 않는다.
    """


# 검색 1회에 허용하는 최대 후보 수. 상한을 두는 이유는 성능이 아니라 프롬프트다 —
# k가 커지면 그만큼 많은 외부 문서 본문이 모델 컨텍스트로 들어간다 (간접 인젝션의
# 표면적이 k에 비례한다).
DEFAULT_MAX_K = 8

# 검색어 길이 상한. 질의 자체를 프롬프트 인젝션 운반체로 쓰는 것을 제한한다.
DEFAULT_MAX_QUERY_CHARS = 512


@dataclass(frozen=True)
class ToolScope:
    """이 실행에서 툴이 할 수 있는 일의 상한.

    기본값은 "인덱싱이 허용된 공개 출처 전부"이며, 호출자가 더 좁힐 수는 있어도
    넓힐 수는 없다 (`narrow()` 참조). 권한은 위임될 때 줄기만 해야 한다.
    """

    allowed_sources: frozenset[str] = field(
        default_factory=lambda: frozenset(ALLOWED_SOURCES)
    )
    max_k: int = DEFAULT_MAX_K
    max_query_chars: int = DEFAULT_MAX_QUERY_CHARS

    def __post_init__(self) -> None:
        unknown = self.allowed_sources - set(ALLOWED_SOURCES)
        if unknown:
            # 반입조차 허용되지 않은 출처를 검색 스코프에 넣을 수는 없다.
            # 스코프가 반입 정책(ADR-004)보다 넓어지는 경로를 원천 차단한다.
            raise ToolScopeError(
                f"반입 허용 목록에 없는 출처를 스코프에 넣을 수 없습니다: "
                f"{sorted(unknown)}. 허용: {sorted(ALLOWED_SOURCES)} (ADR-004)."
            )
        if self.max_k < 1:
            raise ToolScopeError(f"max_k는 1 이상이어야 합니다: {self.max_k}")

    def narrow(self, sources: Sequence[str]) -> ToolScope:
        """스코프를 더 좁힌 새 스코프. 넓히려 하면 예외."""
        requested = frozenset(sources)
        outside = requested - self.allowed_sources
        if outside:
            raise ToolScopeError(
                f"현재 스코프를 벗어난 출처로 좁힐 수 없습니다: {sorted(outside)}. "
                f"현재 허용: {sorted(self.allowed_sources)}."
            )
        return ToolScope(
            allowed_sources=requested,
            max_k=self.max_k,
            max_query_chars=self.max_query_chars,
        )


class ScopedRetriever:
    """`Retriever`를 감싸 스코프를 강제하는 데코레이터.

    `Retriever` 프로토콜을 그대로 구현하므로 노드 코드는 바뀌지 않는다 —
    `CachingLLMProvider`가 `LLMProvider`에 대해 한 것과 같은 패턴이다 (ADR-008).
    """

    def __init__(self, inner: Retriever, scope: ToolScope | None = None) -> None:
        self.inner = inner
        self.scope = scope or ToolScope()
        # 차단 기록. 테스트와 진단이 "몇 건이 왜 막혔는지" 물을 수 있어야 한다.
        self.denied: list[str] = []

    def search(
        self, query: str, *, k: int = 4, sources: Sequence[str] | None = None
    ) -> list[RetrievedChunk]:
        # --- ingress: 요청 자체를 검사한다 ---------------------------------
        if len(query) > self.scope.max_query_chars:
            self._deny(
                f"검색어 길이 {len(query)}자가 상한 {self.scope.max_query_chars}자를 초과"
            )

        if k > self.scope.max_k:
            self._deny(f"k={k}가 상한 {self.scope.max_k}을 초과")

        if sources is None:
            # `None`(=전체)을 그대로 내려보내지 않는다. 그러면 무엇이 검색됐는지가
            # 인덱스 상태에 따라 달라져, 스코프가 선언이 아니라 우연이 된다.
            effective = sorted(self.scope.allowed_sources)
        else:
            outside = set(sources) - self.scope.allowed_sources
            if outside:
                self._deny(
                    f"스코프를 벗어난 출처 요청: {sorted(outside)}. "
                    f"허용: {sorted(self.scope.allowed_sources)}"
                )
            effective = sorted(set(sources))

        chunks = self.inner.search(query, k=k, sources=effective)

        # --- egress: 돌아온 결과도 검사한다 --------------------------------
        # 인덱스가 이미 오염돼 있을 수 있다. 여기서 조용히 떨어뜨리되 기록은 남긴다
        # (여기서 예외를 던지면 오염된 문서 1건이 전체 검색을 죽인다).
        clean: list[RetrievedChunk] = []
        for chunk in chunks:
            if chunk.source in self.scope.allowed_sources:
                clean.append(chunk)
            else:
                self.denied.append(
                    f"egress: 스코프 밖 출처 {chunk.source!r} 문서 {chunk.doc_id!r} 제외"
                )
        return clean

    def _deny(self, reason: str) -> None:
        self.denied.append(reason)
        raise ToolScopeError(f"툴 호출이 스코프를 벗어났습니다 — {reason}.")


def scoped(inner: Retriever, scope: ToolScope | None = None) -> ScopedRetriever:
    """기본 스코프를 씌운 검색 툴."""
    return ScopedRetriever(inner, scope)
