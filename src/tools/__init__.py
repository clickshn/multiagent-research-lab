"""에이전트가 쓰는 툴 계층.

`.claude/rules/security.md`: 모든 툴 호출은 화이트리스트 스코프를 벗어나면 실패해야 한다.
이 패키지에서 그 화이트리스트는 두 겹이다.

1. **코퍼스 반입 경계** (`corpus.ALLOWED_SOURCES`) — 무엇을 인덱싱할 수 있는가.
   공개 출처만 허용한다 (ADR-004).
2. **검색 스코프 경계** (`retrieval.ChromaRetriever.search`) — 무엇을 검색할 수 있는가.
   인덱싱된 것 중에서도 호출자가 지정한 출처로 다시 좁힌다.
"""

from .corpus import (
    ALLOWED_SOURCES,
    CorpusDoc,
    CorpusScopeError,
    assert_allowed_source,
    load_corpus,
)
from .retrieval import ChromaRetriever, RetrievedChunk, Retriever, get_retriever

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
]
