"""에이전트가 쓰는 툴 계층.

`.claude/rules/security.md`: 모든 툴 호출은 화이트리스트 스코프를 벗어나면 실패해야 한다.
이 패키지에서 그 화이트리스트는 두 겹이다.

1. **코퍼스 반입 경계** (`corpus.ALLOWED_SOURCES`) — 무엇을 인덱싱할 수 있는가.
   공개 출처만 허용한다 (ADR-004).
2. **검색 스코프 경계** (`scope.ScopedRetriever`) — 무엇을 검색할 수 있는가.
   인덱싱된 것 중에서도 호출자가 선언한 스코프로 다시 좁히고, 벗어나면 예외를
   던진다. 요청(ingress)과 결과(egress)를 모두 검사한다 (ADR-009).
3. **본문 신뢰 경계** (`sanitize.wrap_untrusted`) — 검색된 문서 본문이 프롬프트에
   들어갈 때 "자료이지 지시가 아니다"라는 경계로 감싼다 (ADR-009).

1·2가 *무엇에 접근하는가*의 경계라면, 3은 *접근한 내용을 어떻게 취급하는가*의
경계다. 셋 다 필요하다 — 스코프 안의 문서도 악의적일 수 있다.
"""

from .corpus import (
    ALLOWED_SOURCES,
    CorpusDoc,
    CorpusScopeError,
    assert_allowed_source,
    load_corpus,
)
from .retrieval import ChromaRetriever, RetrievedChunk, Retriever, get_retriever
from .sanitize import (
    SanitizedText,
    detect_injection,
    sanitize_document_text,
    wrap_untrusted,
)
from .scope import ScopedRetriever, ToolScope, ToolScopeError, scoped

__all__ = [
    "ALLOWED_SOURCES",
    "assert_allowed_source",
    "ChromaRetriever",
    "CorpusDoc",
    "CorpusScopeError",
    "get_retriever",
    "load_corpus",
    "RetrievedChunk",
    "Retriever",
    "SanitizedText",
    "detect_injection",
    "sanitize_document_text",
    "wrap_untrusted",
    "ScopedRetriever",
    "ToolScope",
    "ToolScopeError",
    "scoped",
]
