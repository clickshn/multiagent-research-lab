"""프로바이더 설정 로딩.

엔드포인트 주소와 모델 이름은 코드에 고정하지 않고 `.env`에서 주입한다
(CLAUDE.md, ADR-003). 이 모듈이 환경변수를 읽는 유일한 지점이며, 나머지
코드는 `ProviderSettings`만 본다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"

# 엔드포인트가 인증을 요구하지 않을 때 쓰는 더미 키.
# LiteLLM/OpenAI 클라이언트는 키가 비어 있으면 요청 전에 실패하므로 값이 필요하다.
_NO_AUTH_PLACEHOLDER = "not-needed"


class ConfigError(RuntimeError):
    """필수 설정이 없거나 형식이 잘못된 경우."""


@dataclass(frozen=True)
class ProviderSettings:
    """모델 호출에 필요한 설정 일체.

    이 객체를 바꾸는 것만으로 엔드포인트·모델을 교체할 수 있어야 한다.
    Orchestrator / Sub-agent는 이 타입을 직접 다루지 않는다.
    """

    base_url: str
    model: str
    api_key: str = _NO_AUTH_PLACEHOLDER
    timeout_s: float = 120.0
    max_retries: int = 2

    @property
    def litellm_model(self) -> str:
        """LiteLLM 모델 식별자.

        `openai/` 접두사는 "OpenAI 호환 프로토콜로 말하라"는 뜻이지 OpenAI사(社)
        엔드포인트를 쓴다는 뜻이 아니다. 실제 목적지는 `base_url`이 결정한다.
        """
        return f"openai/{self.model}"

    def redacted(self) -> dict[str, object]:
        """로그·에러 메시지에 넣어도 되는 형태.

        엔드포인트 URL은 사내 전용 정보라 호스트까지만 남기고 경로는 버린다.
        """
        host = self.base_url.split("://")[-1].split("/")[0]
        return {
            "host": host,
            "model": self.model,
            "auth": "none" if self.api_key == _NO_AUTH_PLACEHOLDER else "api-key",
            "timeout_s": self.timeout_s,
            "max_retries": self.max_retries,
        }


def load_settings(env_file: Path | None = DEFAULT_ENV_FILE) -> ProviderSettings:
    """`.env` + 환경변수에서 설정을 읽는다.

    이미 설정된 환경변수가 `.env`보다 우선한다 (CI/컨테이너에서 덮어쓰기 위함).
    """
    if env_file is not None and env_file.exists():
        load_dotenv(env_file, override=False)

    base_url = (os.getenv("VLLM_BASE") or "").strip().rstrip("/")
    model = (os.getenv("VLLM_MODEL") or "").strip()

    missing = [k for k, v in (("VLLM_BASE", base_url), ("VLLM_MODEL", model)) if not v]
    if missing:
        raise ConfigError(
            f"필수 환경변수 누락: {', '.join(missing)}. "
            "`.env.example`을 `.env`로 복사한 뒤 값을 채우세요."
        )

    if not base_url.endswith("/v1"):
        raise ConfigError(
            f"VLLM_BASE는 OpenAI 호환 경로(`/v1`)로 끝나야 합니다: {base_url!r}"
        )

    api_key = (os.getenv("VLLM_API_KEY") or "").strip() or _NO_AUTH_PLACEHOLDER

    return ProviderSettings(base_url=base_url, model=model, api_key=api_key)


# ---------------------------------------------------------------------------
# 임베딩 / 벡터 스토어 / 계측 설정 (session-03)
#
# 이 모듈이 환경변수를 읽는 유일한 지점이라는 규칙(governance.md "코드 규칙")은
# LLM 설정뿐 아니라 아래 설정에도 똑같이 적용된다. 다른 모듈은 아래 dataclass만 본다.
# ---------------------------------------------------------------------------

# 로컬 임베딩 기본 모델.
# 선정 이유는 ADR-005. 요약하면 (1) 서빙 엔드포인트가 /v1/embeddings를 제공하지 않고,
# (2) 대상 문서가 한국어·영어 혼재라 다국어 모델이어야 하며, (3) 118M 파라미터로
# CPU에서도 돌아갈 만큼 가볍다.
_DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-small"

# e5 계열은 질의와 문서에 서로 다른 접두사를 요구한다. 이걸 빠뜨리면 검색 품질이
# 조용히 나빠지므로(에러가 아니라 점수만 나빠진다) 설정으로 들고 다닌다.
_E5_QUERY_PREFIX = "query: "
_E5_PASSAGE_PREFIX = "passage: "


@dataclass(frozen=True)
class EmbeddingSettings:
    """임베딩 모델 설정.

    LLM과 마찬가지로 모델 이름을 코드에 고정하지 않는다. 임베딩 모델을 바꾸면
    인덱스를 다시 만들어야 하므로(차원·의미 공간이 달라진다), 인덱스 메타데이터에
    모델 이름을 함께 기록해 불일치를 감지한다 (`src/tools/retrieval.py`).
    """

    model_name: str = _DEFAULT_EMBEDDING_MODEL
    device: str = "cpu"
    batch_size: int = 16
    query_prefix: str = _E5_QUERY_PREFIX
    passage_prefix: str = _E5_PASSAGE_PREFIX

    @property
    def uses_prefixes(self) -> bool:
        """e5 계열만 접두사를 쓴다. 다른 모델로 바꾸면 접두사를 비워야 한다."""
        return bool(self.query_prefix or self.passage_prefix)


@dataclass(frozen=True)
class VectorStoreSettings:
    """Chroma 영속 저장소 설정 (ADR-005).

    임베디드 모드라 서버 주소가 아니라 디스크 경로가 설정 대상이다. 세션 6에서
    Qdrant 같은 서버형으로 옮기면 이 dataclass가 접속 정보로 바뀐다.
    """

    persist_dir: Path = REPO_ROOT / "var" / "chroma"
    collection: str = "research_corpus"


@dataclass(frozen=True)
class TracingSettings:
    """계측 백엔드 설정 (ADR-007).

    Langfuse 서버가 구성돼 있으면 그쪽으로, 아니면 로컬 JSONL로 기록한다.
    관측 데이터도 우리가 통제하는 인프라에 둔다는 제약(problem-statement.md §2)
    때문에, 외부 SaaS 호스트를 기본값으로 두지 않는다 — 명시적으로 넣어야 켜진다.
    """

    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    local_trace_dir: Path = REPO_ROOT / "var" / "traces"

    @property
    def langfuse_enabled(self) -> bool:
        return bool(
            self.langfuse_host and self.langfuse_public_key and self.langfuse_secret_key
        )

    def redacted(self) -> dict[str, object]:
        """키를 노출하지 않는 진단용 표현."""
        return {
            "backend": "langfuse" if self.langfuse_enabled else "local-jsonl",
            "langfuse_host": self.langfuse_host.split("://")[-1].split("/")[0]
            if self.langfuse_host
            else None,
            "local_trace_dir": str(self.local_trace_dir),
        }


def _ensure_env_loaded(env_file: Path | None = DEFAULT_ENV_FILE) -> None:
    if env_file is not None and env_file.exists():
        load_dotenv(env_file, override=False)


def load_embedding_settings(env_file: Path | None = DEFAULT_ENV_FILE) -> EmbeddingSettings:
    _ensure_env_loaded(env_file)
    model_name = (os.getenv("EMBEDDING_MODEL") or "").strip() or _DEFAULT_EMBEDDING_MODEL

    # e5 계열이 아니면 접두사를 자동으로 끈다. 모델을 바꿨는데 접두사가 남아
    # 검색 품질이 조용히 나빠지는 상황을 막는다.
    is_e5 = "e5" in model_name.lower()
    return EmbeddingSettings(
        model_name=model_name,
        device=(os.getenv("EMBEDDING_DEVICE") or "cpu").strip() or "cpu",
        query_prefix=_E5_QUERY_PREFIX if is_e5 else "",
        passage_prefix=_E5_PASSAGE_PREFIX if is_e5 else "",
    )


def load_vectorstore_settings(env_file: Path | None = DEFAULT_ENV_FILE) -> VectorStoreSettings:
    _ensure_env_loaded(env_file)
    raw_dir = (os.getenv("CHROMA_DIR") or "").strip()
    persist_dir = Path(raw_dir) if raw_dir else REPO_ROOT / "var" / "chroma"
    if not persist_dir.is_absolute():
        persist_dir = REPO_ROOT / persist_dir
    return VectorStoreSettings(
        persist_dir=persist_dir,
        collection=(os.getenv("CHROMA_COLLECTION") or "").strip() or "research_corpus",
    )


def load_tracing_settings(env_file: Path | None = DEFAULT_ENV_FILE) -> TracingSettings:
    _ensure_env_loaded(env_file)
    raw_dir = (os.getenv("TRACE_LOG_DIR") or "").strip()
    trace_dir = Path(raw_dir) if raw_dir else REPO_ROOT / "var" / "traces"
    if not trace_dir.is_absolute():
        trace_dir = REPO_ROOT / trace_dir
    return TracingSettings(
        langfuse_host=(os.getenv("LANGFUSE_HOST") or "").strip().rstrip("/"),
        langfuse_public_key=(os.getenv("LANGFUSE_PUBLIC_KEY") or "").strip(),
        langfuse_secret_key=(os.getenv("LANGFUSE_SECRET_KEY") or "").strip(),
        local_trace_dir=trace_dir,
    )
