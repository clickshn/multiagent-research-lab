"""`ChromaRetriever.search(tech_domain=...)` — 검색 계층 필터 (ADR-027).

고정하는 것:

1. **`tech_domain=None`이면 기존 경로와 같다** — `collection.query`에 가는 인자가 같고
   (`n_results=k`), 전수 조회·`count()`·`get()`을 부르지 않는다. 결과도 필터 인자를 모르는
   직접 질의와 같다.
2. **필터 경로는 항상 전수를 받는다** — k가 얼마든 `n_results`는 컬렉션 문서 수다.
   `sources`가 있으면 그 출처의 문서 수다.
3. **strict** — 온톨로지 메타가 없는 문서는 탈락하고, 생존자의 순서는 무필터 순위 그대로다
   (v1.1 프로브 후처리와 같은 정의, ADR-023).
4. 통제어휘 밖 값은 `FilterValueError`(= `RetrievalError`가 아니다 — Researcher가 삼키지 않게).
5. 전수가 오지 않으면 `RetrievalError` — 조용히 넘기지 않는다 (ADR-005 Amendment 3).
6. 트레이스에 생존자 수가 남는다.

임베딩 모델은 띄우지 않는다 — `test_index_guard.py`와 같은 결정적 가짜 임베딩이다.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.providers.config import VectorStoreSettings  # noqa: E402
from src.tools.corpus import CorpusDoc  # noqa: E402
from src.tools.retrieval import (  # noqa: E402
    ChromaRetriever,
    FilterValueError,
    RetrievalError,
    _to_chunks,
)

_DIM = 8
VOCAB = frozenset({"Agent", "LLM", "Reasoning", "Eval/Governance"})


class FakeEmbeddings:
    model_name = "fake/test-embeddings"

    @property
    def dimension(self) -> int:
        return _DIM

    def _vector(self, text: str) -> list[float]:
        seed = sum(ord(c) for c in text) or 1
        return [((seed * (i + 1)) % 97) / 97.0 + 0.01 for i in range(_DIM)]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


# (doc_id, source, tech_domains) — 빈 문자열은 온톨로지 메타 없음(스냅샷 문서)
DOCS = [
    ("news:a1", "news", "Agent|LLM"),
    ("news:a2", "news", "Reasoning"),
    ("news:a3", "news", "Eval/Governance|Agent"),
    ("arXiv:0001v1", "arxiv", "Reasoning|LLM"),
    ("arXiv:0002v1", "arxiv", ""),
    ("arXiv:0003v1", "arxiv", "Agent"),
    ("arXiv:0004v1", "arxiv", ""),
]


def _corpus() -> list[CorpusDoc]:
    docs = []
    for n, (doc_id, source, domains) in enumerate(DOCS):
        extra = {"tech_domains": domains, "export_id": "exp-1"} if domains else {}
        docs.append(
            CorpusDoc(
                doc_id=doc_id,
                source=source,
                title=f"문서 {n} 에이전트 추론",
                text=f"본문 {n} " * (n + 1),
                extra=extra,
            )
        )
    return docs


class QuerySpy:
    """컬렉션 호출을 기록한다. 결과는 진짜 컬렉션이 낸다."""

    def __init__(self, collection) -> None:
        self._collection = collection
        self.queries: list[dict] = []
        self.other_calls: list[str] = []
        self.drop_last = False

    def query(self, **kwargs):
        self.queries.append(kwargs)
        raw = self._collection.query(**kwargs)
        if self.drop_last:  # HNSW 유실 흉내 — 마지막 1건을 뺀다
            raw = {key: ([v[0][:-1]] if v else v) for key, v in raw.items()
                   if isinstance(v, list)}
        return raw

    def count(self):
        self.other_calls.append("count")
        return self._collection.count()

    def get(self, **kwargs):
        self.other_calls.append("get")
        return self._collection.get(**kwargs)

    def __getattr__(self, name):
        return getattr(self._collection, name)


class RecordingTrace:
    def __init__(self) -> None:
        self.spans: list[dict[str, Any]] = []

    @property
    def run_id(self) -> str:
        return "test"

    def span(self, name, *, kind="span", input=None):
        record: dict[str, Any] = {"name": name, "kind": kind, "input": input}
        self.spans.append(record)

        class _S:
            def end(self, **kwargs):
                record.update(kwargs)

        return _S()

    def end(self, **_):
        return None


@pytest.fixture
def retriever(tmp_path):
    settings = VectorStoreSettings(persist_dir=tmp_path / "chroma", collection="filter_test")
    built = ChromaRetriever(embeddings=FakeEmbeddings(), settings=settings)
    built.index(_corpus())
    fresh = ChromaRetriever(
        embeddings=FakeEmbeddings(), settings=settings, tech_domain_vocab=VOCAB
    )
    spy = QuerySpy(fresh._get_collection(create=False))
    fresh._collection = spy
    return fresh, spy


QUERY = "에이전트 추론 벤치마크"


def _ids(chunks) -> list[str]:
    return [c.doc_id for c in chunks]


def test_none_path_sends_same_query_as_before(retriever) -> None:
    r, spy = retriever
    got = r.search(QUERY, k=3)
    assert len(spy.queries) == 1
    call = spy.queries[0]
    assert call["n_results"] == 3 and call["where"] is None
    assert call["include"] == ["documents", "metadatas", "distances"]
    assert spy.other_calls == []  # 전수 조회용 count/get을 부르지 않는다

    # 필터 인자를 모르는 직접 질의와 결과가 같다 (ID·순위·점수)
    direct = _to_chunks(spy._collection.query(
        query_embeddings=[FakeEmbeddings().embed_query(QUERY)], n_results=3, where=None,
        include=["documents", "metadatas", "distances"]))
    assert [(c.doc_id, c.score) for c in got] == [(c.doc_id, c.score) for c in direct]


def test_none_path_with_sources_unchanged(retriever) -> None:
    r, spy = retriever
    r.search(QUERY, k=2, sources=["arxiv"])
    assert spy.queries[0]["n_results"] == 2
    assert spy.queries[0]["where"] == {"source": {"$in": ["arxiv"]}}
    assert spy.other_calls == []


def test_none_path_ignores_trace(retriever) -> None:
    r, _ = retriever
    trace = RecordingTrace()
    r.search(QUERY, k=2, trace=trace)
    assert trace.spans == []


@pytest.mark.parametrize("k", [1, 2, 4, 100])
def test_filter_always_fetches_whole_collection(retriever, k) -> None:
    r, spy = retriever
    r.search(QUERY, k=k, tech_domain="Agent")
    assert spy.queries[-1]["n_results"] == len(DOCS)
    assert spy.queries[-1]["where"] is None


def test_filter_fetches_whole_source_when_sources_given(retriever) -> None:
    r, spy = retriever
    got = r.search(QUERY, k=4, sources=["arxiv"], tech_domain="Agent")
    assert spy.queries[-1]["n_results"] == sum(1 for d in DOCS if d[1] == "arxiv")
    assert _ids(got) == ["arXiv:0003v1"]


@pytest.mark.parametrize("domain", sorted(VOCAB))
def test_filter_is_strict_and_rank_preserving(retriever, domain) -> None:
    r, _ = retriever
    unfiltered = r.search(QUERY, k=len(DOCS))
    expected = [c for c in unfiltered if domain in c.tech_domains]
    got = r.search(QUERY, k=len(DOCS), tech_domain=domain)
    assert [(c.doc_id, c.score) for c in got] == [(c.doc_id, c.score) for c in expected]
    # 메타 없는 문서는 어떤 값으로도 살아남지 않는다 (strict)
    assert not {"arXiv:0002v1", "arXiv:0004v1"} & set(_ids(got))


def test_filter_returns_top_k_of_survivors(retriever) -> None:
    r, _ = retriever
    all_agents = r.search(QUERY, k=len(DOCS), tech_domain="Agent")
    assert len(all_agents) == 3
    assert _ids(r.search(QUERY, k=2, tech_domain="Agent")) == _ids(all_agents)[:2]


def test_out_of_vocab_value_raises_not_retrieval_error(retriever) -> None:
    r, spy = retriever
    with pytest.raises(FilterValueError) as info:
        r.search(QUERY, k=4, tech_domain="reasoning")  # 대소문자 오타
    assert not isinstance(info.value, RetrievalError)
    assert spy.queries == []  # 질의 전에 멈춘다


def test_incomplete_fetch_raises_and_is_traced(retriever) -> None:
    r, spy = retriever
    spy.drop_last = True
    trace = RecordingTrace()
    with pytest.raises(RetrievalError, match="전수"):
        r.search(QUERY, k=4, tech_domain="Agent", trace=trace)
    (span,) = trace.spans
    assert span["metadata"]["expected"] == len(DOCS)
    assert span["metadata"]["fetched"] == len(DOCS) - 1
    assert "error" in span["metadata"]


def test_filter_records_survivor_count_in_trace(retriever) -> None:
    r, _ = retriever
    trace = RecordingTrace()
    got = r.search(QUERY, k=2, tech_domain="Agent", trace=trace)
    (span,) = trace.spans
    assert span["name"] == "retrieval_filter"
    assert span["input"] == {"tech_domain": "Agent", "null_policy": "strict", "k": 2}
    assert span["metadata"] == {
        "expected": len(DOCS), "fetched": len(DOCS), "survivors": 3, "returned": 2,
    }
    assert [o["doc_id"] for o in span["output"]] == _ids(got)


def test_default_vocab_comes_from_manifests() -> None:
    """주입하지 않으면 실제 export manifest에서 읽는다 (계약 §5 — 어휘를 복제하지 않는다)."""
    vocab = ChromaRetriever(embeddings=FakeEmbeddings()).tech_domain_vocab()
    assert {"Reasoning", "Eval/Governance", "Data/Synthetic"} <= vocab
