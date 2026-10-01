"""Chroma 기반 검색 툴 (ADR-005).

Researcher 노드가 보는 인터페이스는 `Retriever` 프로토콜 하나다. Chroma를 세션 6에서
Qdrant로 옮기더라도 노드 코드는 바뀌지 않는다 — 프로바이더 계층과 같은 원칙이다.

검색 결과에는 **항상** `doc_id`와 `locator`가 실린다. 출처 없는 결과를 반환할 수
있는 설계였다면 Writer가 근거 없는 문장을 쓸 여지가 생긴다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from src.providers.config import VectorStoreSettings, load_vectorstore_settings
from src.providers.embeddings import EmbeddingProvider, get_embedding_provider

from .corpus import DEFAULT_CORPUS_DIR, CorpusDoc, CorpusScopeError, assert_allowed_source

if TYPE_CHECKING:
    from src.obs import RunTrace

# 인덱스를 만든 임베딩 모델을 컬렉션 메타데이터에 남겨둘 키.
# 모델이 바뀌면 벡터 공간이 달라져 검색 결과가 조용히 엉뚱해진다. 에러로 잡는다.
_META_EMBEDDING_MODEL = "embedding_model"


class RetrievalError(RuntimeError):
    """검색·인덱싱 실패."""


class FilterValueError(ValueError):
    """필터 값이 통제어휘 밖이다 (ADR-027).

    `RetrievalError`와 따로 둔다. Researcher는 `RetrievalError`를 "근거 없음"으로 삼키는데,
    오타 난 필터 값이 그렇게 삼켜지면 strict 필터의 "생존자 0"과 구별되지 않는다.
    알 수 없는 필터 정책(`filter_policy`)도 같은 이유로 이 예외다.
    """


# `tech_domain` 필터 정책 (v1.2-N1). 메타(`tech_domains`)가 **없는** 문서를 어떻게 다루는가.
#   strict    — 탈락시킨다 (ADR-027, 기본값).
#   null_pass — 통과시킨다. v1.1 프로브 `--null-policy pass`와 같은 정의다 (ADR-023).
# 메타가 있는 문서는 두 정책 모두 `값 ∈ tech_domains`로 판정하고, 생존자 순서는 무필터 순위 그대로다.
FILTER_POLICY_STRICT = "strict"
FILTER_POLICY_NULL_PASS = "null_pass"
FILTER_POLICIES: tuple[str, ...] = (FILTER_POLICY_STRICT, FILTER_POLICY_NULL_PASS)


@dataclass(frozen=True)
class RetrievedChunk:
    """검색 결과 1건. 출처 정보가 항상 함께 온다.

    **온톨로지 메타(session-16).** `release_type`·`tech_domains`·`published`를 함께
    싣는다. 인용에 "어떤 종류의 문서인가"를 붙이려면 검색 결과가 그것을 들고 와야
    하기 때문이다 (ADR-024).

    ⚠️ **`derived_*` 키는 여기로 옮기지 않는다** — `derived_summary`·
    `derived_impact_rationale`는 모델이 쓴 패러프레이즈이고 계약 §6이 색인·인용을
    금지한다. 옮기는 순간 Writer가 원문 대신 패러프레이즈를 인용할 수 있게 된다
    (`contract_import.py` 모듈 docstring).

    ⚠️ **추가는 additive다.** 질의·`where` 절·`k`·정렬·점수 계산은 한 줄도 바뀌지
    않았다 — 메타를 더 실어올 뿐 무엇이 몇 위로 오는지는 그대로다. 그 불변을
    `docs/eval/citation-metadata-parity-session-16.md`가 30케이스 전건 대조로 확인한다.
    """

    doc_id: str
    locator: str
    text: str
    source: str
    title: str = ""
    url: str = ""
    score: float = 0.0  # 1.0에 가까울수록 유사 (코사인 유사도)
    # --- 온톨로지 메타 (session-16, ADR-024) ---
    published: str = ""
    release_type: str = ""
    tech_domains: tuple[str, ...] = ()
    has_ontology: bool = False
    """이 문서가 **온톨로지 export를 거쳤는가**.

    `release_type`이 비어 있는 이유가 두 가지이기 때문에 따로 들고 다닌다:
    export를 거쳤는데 값이 없는 것(= 해당 없음)과, export 자체를 거치지 않은 것
    (= 확인 안 됨, ADR-022 층 B의 스냅샷 arXiv 14건)은 다른 사실이다.
    인용에서 이 둘을 같은 빈칸으로 보여주면 독자가 구별할 수 없다.
    """


@runtime_checkable
class Retriever(Protocol):
    """Researcher 노드가 의존하는 유일한 검색 인터페이스."""

    def search(
        self,
        query: str,
        *,
        k: int = 4,
        sources: Sequence[str] | None = None,
        tech_domain: str | None = None,
        trace: RunTrace | None = None,
        filter_policy: str = FILTER_POLICY_STRICT,
    ) -> list[RetrievedChunk]: ...


class ChromaRetriever:
    """Chroma 임베디드(persistent) 컬렉션 위의 검색 툴.

    임베딩은 우리가 직접 계산해서 넣는다 — Chroma의 기본 임베딩 함수를 쓰면
    임베딩 백엔드 선택이 벡터 DB 선택에 묶여버리기 때문이다. 두 결정은 따로
    되돌릴 수 있어야 한다 (ADR-005).
    """

    def __init__(
        self,
        embeddings: EmbeddingProvider | None = None,
        settings: VectorStoreSettings | None = None,
        tech_domain_vocab: frozenset[str] | None = None,
    ) -> None:
        self.settings = settings or load_vectorstore_settings()
        self.embeddings = embeddings or get_embedding_provider()
        self._client = None
        self._collection = None
        # 통제어휘. None이면 필터 검색을 처음 할 때 manifest에서 읽는다 (계약 §5 — 복제하지 않는다).
        self._tech_domain_vocab = tech_domain_vocab

    # --- 내부 ---------------------------------------------------------------

    def _get_collection(self, *, create: bool = True):
        if self._collection is not None:
            return self._collection
        try:
            import chromadb
        except ImportError as exc:  # pragma: no cover
            raise RetrievalError(
                "chromadb가 설치되어 있지 않습니다. `pip install -r requirements.txt`."
            ) from exc

        self.settings.persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self.settings.persist_dir))

        metadata = {
            _META_EMBEDDING_MODEL: self.embeddings.model_name,
            # 코사인 유사도. 임베딩을 정규화해서 넣으므로 의미가 명확하다.
            "hnsw:space": "cosine",
        }
        if create:
            self._collection = self._client.get_or_create_collection(
                name=self.settings.collection, metadata=metadata
            )
        else:
            try:
                self._collection = self._client.get_collection(name=self.settings.collection)
            except Exception as exc:  # noqa: BLE001
                raise RetrievalError(
                    f"인덱스가 없습니다 (collection={self.settings.collection!r}). "
                    "`python scripts/build_index.py`를 먼저 실행하세요."
                ) from exc

        self._assert_embedding_model_matches(self._collection)
        return self._collection

    def _assert_embedding_model_matches(self, collection) -> None:
        """인덱스를 만든 임베딩 모델과 현재 모델이 같은지 확인한다.

        다르면 검색이 에러 없이 이상한 결과를 낸다 — 조용한 실패라 특히 위험하다.
        """
        indexed_model = (collection.metadata or {}).get(_META_EMBEDDING_MODEL)
        current = self.embeddings.model_name
        if indexed_model and indexed_model != current:
            raise RetrievalError(
                f"임베딩 모델 불일치: 인덱스는 {indexed_model!r}로 만들어졌고 "
                f"현재 설정은 {current!r}입니다. 인덱스를 다시 만드세요 "
                "(`python scripts/build_index.py --reset`)."
            )

    # --- 인덱싱 -------------------------------------------------------------

    def reset(self) -> None:
        """컬렉션을 비운다. 임베딩 모델을 바꿨을 때 쓴다."""
        self._get_collection(create=True)
        assert self._client is not None
        try:
            self._client.delete_collection(name=self.settings.collection)
        except Exception:  # noqa: BLE001 - 없으면 지울 것도 없다
            pass
        self._collection = None

    def index(self, docs: Sequence[CorpusDoc], *, batch_size: int = 32) -> int:
        """문서를 임베딩해 컬렉션에 넣는다. 출처 화이트리스트를 강제한다."""
        if not docs:
            return 0
        for doc in docs:
            assert_allowed_source(doc.source)

        collection = self._get_collection(create=True)
        total = 0
        for start in range(0, len(docs), batch_size):
            batch = docs[start : start + batch_size]
            # 제목을 본문 앞에 붙여 임베딩한다 — 초록만으로는 주제어가 약한 경우가 있다.
            texts = [f"{d.title}\n\n{d.text}".strip() for d in batch]
            vectors = self.embeddings.embed_documents(texts)
            collection.upsert(
                ids=[f"{d.source}:{d.doc_id}:{d.locator}" for d in batch],
                documents=[d.text for d in batch],
                embeddings=vectors,
                metadatas=[d.as_metadata() for d in batch],
            )
            total += len(batch)
        return total

    def count(self) -> int:
        return int(self._get_collection(create=True).count())

    # --- 검색 ---------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        k: int = 4,
        sources: Sequence[str] | None = None,
        tech_domain: str | None = None,
        trace: RunTrace | None = None,
        filter_policy: str = FILTER_POLICY_STRICT,
    ) -> list[RetrievedChunk]:
        """질의와 유사한 청크를 반환한다.

        `sources`를 주면 그 출처로만 좁힌다. 허용 목록 밖의 출처를 요구하면
        빈 결과가 아니라 예외다 (`.claude/rules/security.md`).

        `tech_domain`을 주면 그 도메인을 가진 문서만 남긴다 (ADR-027, strict — 온톨로지
        메타가 없는 문서는 탈락). **None이면 아래 무필터 경로를 그대로 탄다** — 필터 경로는
        별도 메서드이고 `trace`도 거기서만 쓴다.

        `filter_policy`는 메타가 없는 문서의 처리다(`FILTER_POLICIES`, v1.2-N1). 기본값 strict는
        ADR-027 경로 그대로다. `tech_domain`이 None이면 정책은 쓰이지 않는다(무필터).
        """
        if not query.strip():
            return []

        where = None
        if sources:
            for source in sources:
                assert_allowed_source(source)
            where = {"source": {"$in": list(sources)}}

        if filter_policy not in FILTER_POLICIES:
            raise FilterValueError(
                f"알 수 없는 필터 정책: {filter_policy!r} (허용: {', '.join(FILTER_POLICIES)})"
            )
        if tech_domain is not None:
            return self._search_tech_domain(
                query, k=k, where=where, tech_domain=tech_domain, trace=trace,
                filter_policy=filter_policy,
            )

        collection = self._get_collection(create=False)
        vector = self.embeddings.embed_query(query)
        try:
            raw = collection.query(
                query_embeddings=[vector],
                n_results=k,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:  # noqa: BLE001
            raise RetrievalError(f"검색 실패: {exc}") from exc

        return list(_to_chunks(raw))

    # --- 필터 검색 (ADR-027) ------------------------------------------------

    def tech_domain_vocab(self) -> frozenset[str]:
        """`tech_domain` 통제어휘 — export manifest들의 합집합."""
        if self._tech_domain_vocab is None:
            from .contract_import import discover_manifests  # noqa: PLC0415 (순환 참조 회피)

            vocab: set[str] = set()
            for manifest in discover_manifests(DEFAULT_CORPUS_DIR):
                vocab |= manifest.tech_domain
            self._tech_domain_vocab = frozenset(vocab)
        return self._tech_domain_vocab

    def _search_tech_domain(
        self,
        query: str,
        *,
        k: int,
        where: dict | None,
        tech_domain: str,
        trace: RunTrace | None,
        filter_policy: str = FILTER_POLICY_STRICT,
    ) -> list[RetrievedChunk]:
        """전수를 순위로 받아 `tech_domain`으로 거른 뒤 상위 `k`건을 돌려준다.

        v1.1 프로브(ADR-023 `--arm tech_domain --null-policy strict`)의 후처리를 검색 계층으로
        옮긴 것이다. `tech_domains`는 `|` 연결 문자열이라 Chroma `where`로 거를 수 없다
        (ADR-020) — 그래서 DB에서 자르지 않고 여기서 거른다.

        ⚠️ **받아오는 건수는 항상 전수다** (`sources`가 없으면 `collection.count()`).
        k<N으로 받아 거르면 필터 뒤 상위 문서가 조용히 빠질 수 있다. 그 경로는 만들지 않는다.
        ⚠️ **전수가 오지 않으면 예외다.** HNSW는 근사 검색이라 N건을 물어도 N-1건만 오는
        실행이 있다 (ADR-005 Amendment 3). 빠진 문서가 필터 생존자일 수 있으므로 조용히
        넘기지 않는다.

        `filter_policy=null_pass`면 메타(`tech_domains`)가 없는 문서도 생존자로 남긴다(v1.2-N1).
        메타가 있는 문서의 판정과 생존자 순서는 strict와 같다 — 생존 집합이 메타 없는 문서만큼
        늘어날 뿐이다.
        """
        if tech_domain not in self.tech_domain_vocab():
            raise FilterValueError(
                f"tech_domain 값이 통제어휘 밖입니다: {tech_domain!r} "
                f"(어휘: {', '.join(sorted(self.tech_domain_vocab()))})"
            )

        collection = self._get_collection(create=False)
        if where is None:
            expected = int(collection.count())
        else:
            expected = len(collection.get(where=where, include=[])["ids"])

        span = None
        if trace is not None:
            span = trace.span(
                "retrieval_filter",
                kind="span",
                input={"tech_domain": tech_domain, "null_policy": filter_policy, "k": k},
            )

        vector = self.embeddings.embed_query(query)
        raw: dict = {}
        if expected:
            try:
                raw = collection.query(
                    query_embeddings=[vector],
                    n_results=expected,
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )
            except Exception as exc:  # noqa: BLE001
                if span is not None:
                    span.end(output=None, metadata={"error": str(exc)})
                raise RetrievalError(f"검색 실패: {exc}") from exc

        fetched = len((raw.get("ids") or [[]])[0])
        if fetched < expected:
            message = (
                f"필터 검색이 전수를 받지 못했다: {expected}건을 물었는데 {fetched}건 "
                "(HNSW 근사 검색 유실, ADR-005 Amendment 3)"
            )
            if span is not None:
                span.end(
                    output=None,
                    metadata={"error": message, "expected": expected, "fetched": fetched},
                )
            raise RetrievalError(message)

        null_pass = filter_policy == FILTER_POLICY_NULL_PASS
        survivors = [
            c for c in _to_chunks(raw)
            if tech_domain in c.tech_domains or (null_pass and not c.tech_domains)
        ]
        result = survivors[:k]
        if span is not None:
            span.end(
                output=[{"doc_id": c.doc_id, "score": c.score} for c in result],
                metadata={
                    "expected": expected,
                    "fetched": fetched,
                    "survivors": len(survivors),
                    "returned": len(result),
                    # null_pass일 때만 싣는다 — strict span은 ADR-027 그대로 둔다.
                    **({"returned_without_meta": sum(not c.tech_domains for c in result)}
                       if null_pass else {}),
                },
            )
        return result


def _to_chunks(raw: dict) -> list[RetrievedChunk]:
    """Chroma 응답을 우리 타입으로 좁힌다. 스키마 변화를 여기서만 흡수한다."""
    documents = (raw.get("documents") or [[]])[0]
    metadatas = (raw.get("metadatas") or [[]])[0]
    distances = (raw.get("distances") or [[]])[0]

    chunks: list[RetrievedChunk] = []
    for text, meta, distance in zip(documents, metadatas, distances, strict=False):
        meta = meta or {}
        doc_id = str(meta.get("doc_id", "")).strip()
        if not doc_id:
            # 출처를 모르는 결과는 쓰지 않는다 — 인용할 수 없는 근거는 근거가 아니다.
            continue
        chunks.append(
            RetrievedChunk(
                doc_id=doc_id,
                locator=str(meta.get("locator", "abstract")),
                text=str(text or ""),
                source=str(meta.get("source", "")),
                title=str(meta.get("title", "")),
                url=str(meta.get("url", "")),
                # cosine distance -> similarity
                score=round(1.0 - float(distance), 4),
                published=str(meta.get("published", "")),
                release_type=str(meta.get("release_type", "")),
                tech_domains=_split_list(meta.get("tech_domains")),
                # `export_id`는 `ontology_metadata()`가 항상 싣는 키다. 있으면 그 문서는
                # 계약 export를 거쳤다는 뜻이고, 없으면 스냅샷만으로 들어온 문서다.
                has_ontology=bool(str(meta.get("export_id", "")).strip()),
            )
        )
    return chunks


def _split_list(value: object) -> tuple[str, ...]:
    """Chroma에 `|`로 이어 붙여 저장한 리스트를 되돌린다.

    저장 형태는 `contract_import._LIST_JOIN`이 정한다. 스칼라만 받는 메타데이터에
    리스트를 넣으려고 이어 붙인 것이라, 읽는 쪽에서 되돌리는 자리가 필요하다.
    """
    if not value:
        return ()
    return tuple(part for part in str(value).split("|") if part.strip())


def get_retriever(
    embeddings: EmbeddingProvider | None = None,
    settings: VectorStoreSettings | None = None,
) -> Retriever:
    """기본 검색 툴. 노드는 이 함수만 알면 된다."""
    return ChromaRetriever(embeddings, settings)
