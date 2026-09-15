"""코퍼스 반입·검색 스코프 화이트리스트 검증 (ADR-004).

`.claude/rules/security.md`: 모든 툴 호출은 화이트리스트 스코프를 벗어나면 실패해야 한다.

**방어 전 / 방어 후.** 화이트리스트가 없을 때 `load_corpus()`는 디렉터리 이름을 그대로
출처로 받아들이므로, `data/corpus/internal-hr/`처럼 사내 문서를 담은 디렉터리를 만들면
아무 저항 없이 인덱싱된다 — 실패가 아니라 정상 동작으로 보이므로 리뷰에서도 놓치기 쉽다.
방어 후에는 `assert_allowed_source()`가 `CorpusScopeError`를 던져 인덱싱 자체가 중단된다.
아래 테스트가 그 경계를 고정한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tools.corpus import (
    ALLOWED_SOURCES,
    CorpusDoc,
    CorpusScopeError,
    assert_allowed_source,
    load_corpus,
)
from src.tools.retrieval import ChromaRetriever


def test_allowed_sources_are_public_only() -> None:
    """허용 목록이 공개 출처만 담고 있는지.

    이 목록이 조용히 늘어나는 것을 막는 회귀 테스트다. 출처를 추가하려면
    ADR-004를 먼저 갱신해야 하고, 그러면 이 테스트도 함께 고치게 된다.
    """
    assert ALLOWED_SOURCES == ("arxiv", "news")


@pytest.mark.parametrize(
    "source",
    ["internal-hr", "confidential", "사내문서", "../arxiv", "arxiv/../internal", ""],
)
def test_disallowed_source_raises(source: str) -> None:
    """허용 목록 밖의 출처는 빈 결과가 아니라 예외다."""
    with pytest.raises(CorpusScopeError):
        assert_allowed_source(source)


@pytest.mark.parametrize("source", ALLOWED_SOURCES)
def test_allowed_source_passes(source: str) -> None:
    assert assert_allowed_source(source) == source


def test_load_corpus_rejects_disallowed_directory(tmp_path: Path) -> None:
    """사내 문서를 담은 디렉터리는 인덱싱 경로에 진입하지 못한다."""
    internal = tmp_path / "internal-hr"
    internal.mkdir()
    (internal / "docs.json").write_text(
        json.dumps(
            {"documents": [{"doc_id": "HR-001", "title": "인사 규정", "text": "기밀"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(CorpusScopeError, match="internal-hr"):
        load_corpus(tmp_path)


def test_load_corpus_accepts_allowed_directory(tmp_path: Path) -> None:
    arxiv = tmp_path / "arxiv"
    arxiv.mkdir()
    (arxiv / "sample.json").write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "doc_id": "arXiv:1234.5678v1",
                        "title": "A Paper",
                        "text": "초록 본문",
                        "locator": "abstract",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    docs = load_corpus(tmp_path)
    assert len(docs) == 1
    assert docs[0].source == "arxiv"
    # 출처 제시 요구: doc_id와 locator가 항상 함께 실린다.
    assert docs[0].doc_id == "arXiv:1234.5678v1"
    assert docs[0].locator == "abstract"


def test_index_rejects_disallowed_source() -> None:
    """인덱싱 직전에도 한 번 더 막는다.

    `load_corpus()`를 우회해 CorpusDoc을 직접 만들어 넣는 경로가 있을 수 있으므로,
    경계를 한 겹만 두지 않는다. 임베딩·Chroma에 닿기 전에 예외가 나야 한다.
    """
    retriever = ChromaRetriever()
    doc = CorpusDoc(
        doc_id="HR-001", source="internal-hr", title="인사 규정", text="기밀 내용"
    )
    with pytest.raises(CorpusScopeError):
        retriever.index([doc])


def test_search_rejects_disallowed_source_filter() -> None:
    """검색 스코프도 화이트리스트를 벗어나면 실패한다."""
    retriever = ChromaRetriever()
    with pytest.raises(CorpusScopeError):
        retriever.search("질의", sources=["internal-hr"])
