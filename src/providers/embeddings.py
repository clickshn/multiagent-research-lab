"""임베딩 프로바이더.

LLM 프로바이더(`llm.py`)와 같은 원칙이다: 상위 계층은 `EmbeddingProvider`
프로토콜만 보고, 어떤 모델이 어디서 도는지는 모른다. 임베딩 백엔드를 바꿔도
검색 툴과 노드 코드는 바뀌지 않는다.

**왜 로컬 모델인가.** 서빙 엔드포인트(`VLLM_BASE`)는 `/v1/embeddings`를 제공하지
않는다 — 실측 404 (session-03, `scripts/probe_embeddings.py`로 재현 가능).
그래서 임베딩만 로컬 sentence-transformers로 내린다. 이것은 "외부 LLM 벤더 비의존"
제약을 깨지 않는다: 모델 가중치가 로컬에 있고 문서 내용이 어디로도 나가지 않는다.
상세는 ADR-005.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .config import EmbeddingSettings, load_embedding_settings


class EmbeddingError(RuntimeError):
    """임베딩 생성 실패."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """검색 툴이 의존하는 유일한 임베딩 인터페이스."""

    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class SentenceTransformerEmbeddings:
    """로컬 sentence-transformers 임베딩.

    모델 로딩이 수십 초 걸리므로 첫 사용 시점까지 미룬다(lazy). 인덱싱과 검색이
    같은 프로세스 안에서 같은 인스턴스를 재사용하도록 호출부가 이 객체를 들고 다닌다.
    """

    def __init__(self, settings: EmbeddingSettings | None = None) -> None:
        self.settings = settings or load_embedding_settings()
        self._model = None

    @property
    def model_name(self) -> str:
        return self.settings.model_name

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover - 환경 문제
                raise EmbeddingError(
                    "sentence-transformers가 설치되어 있지 않습니다. "
                    "`pip install -r requirements.txt`를 실행하세요."
                ) from exc
            try:
                self._model = SentenceTransformer(
                    self.settings.model_name, device=self.settings.device
                )
            except Exception as exc:  # noqa: BLE001 - 벤더 예외를 경계에서 덮는다
                raise EmbeddingError(
                    f"임베딩 모델 로딩 실패 ({self.settings.model_name}): {exc}"
                ) from exc
        return self._model

    @property
    def dimension(self) -> int:
        return int(self._load().get_sentence_embedding_dimension())

    def _encode(self, texts: Sequence[str], *, prefix: str) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        prepared = [f"{prefix}{t}" for t in texts] if prefix else list(texts)
        try:
            vectors = model.encode(
                prepared,
                batch_size=self.settings.batch_size,
                normalize_embeddings=True,  # 코사인 유사도를 내적으로 계산하기 위함
                show_progress_bar=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(f"임베딩 생성 실패: {exc}") from exc
        return [[float(x) for x in vec] for vec in vectors]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode(texts, prefix=self.settings.passage_prefix)

    def embed_query(self, text: str) -> list[float]:
        return self._encode([text], prefix=self.settings.query_prefix)[0]


def get_embedding_provider(
    settings: EmbeddingSettings | None = None,
) -> EmbeddingProvider:
    """기본 임베딩 프로바이더. 검색 툴은 이 함수만 알면 된다."""
    return SentenceTransformerEmbeddings(settings)
