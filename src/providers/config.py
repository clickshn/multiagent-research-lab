"""프로바이더 설정 로딩.

엔드포인트 주소와 모델 이름은 코드에 고정하지 않고 `.env`에서 주입한다
(CLAUDE.md, ADR-003). 이 모듈이 환경변수를 읽는 유일한 지점이며, 나머지
코드는 `ProviderSettings`만 본다.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .egress import assert_internal_endpoint

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

    def __post_init__(self) -> None:
        """외부 LLM 벤더 엔드포인트면 **객체가 만들어지지 않는다** (ADR-021).

        검사를 `load_settings()`가 아니라 여기에 두는 이유: `load_settings()`에만 두면
        `ProviderSettings(base_url=...)`를 직접 만드는 경로가 검사를 비껴간다.
        타입 자체가 불변식을 들고 있으면 **어느 경로로 만들어도** 같은 검사를 받는다.
        """
        assert_internal_endpoint(self.base_url)

    @property
    def litellm_model(self) -> str:
        """LiteLLM 모델 식별자.

        `openai/` 접두사는 "OpenAI 호환 프로토콜로 말하라"는 뜻이지 OpenAI사(社)
        엔드포인트를 쓴다는 뜻이 아니다. 실제 목적지는 `base_url`이 결정한다.
        """
        return f"openai/{self.model}"

    def redacted(self) -> dict[str, object]:
        """로그·에러 메시지에 넣어도 되는 형태.

        **호스트를 가린다 (session-06에 수정).** 원래 이 함수는 경로만 버리고 호스트를
        그대로 남겼다. 실제 엔드포인트를 넣어 보니 그 설계가 틀렸다는 것이 드러났다 —
        경로가 `/v1`뿐이라 **가려지는 정보가 없고**, 인증이 없어 호스트를 아는 것만으로
        접근이 된다 (`docs/governance.md` "시크릿 취급"). 즉 비밀은 경로가 아니라 호스트다.

        대신 두 가지를 남긴다.

        - 공개 등록 도메인(뒤 두 라벨) — "어느 벤더인가"는 진단에 필요하고, 그 자체로는
          접근 권한이 아니다. 추측 불가능한 부분은 앞쪽 라벨이다.
        - `endpoint_fp` — `base_url`의 sha256 앞 12자리. 되돌릴 수 없지만 **비교는 된다.**
          "지난주 측정과 같은 엔드포인트인가"를 URL을 드러내지 않고 답할 수 있다.
          엔드포인트가 바뀌면 캐시를 비워야 하는데(ADR-008), 그 판단에 쓰라고 남긴다.
        """
        host = self.base_url.split("://")[-1].split("/")[0].split(":")[0]
        labels = host.split(".")
        public_suffix = ".".join(labels[-2:]) if len(labels) > 2 else host
        masked = f"***.{public_suffix}" if len(labels) > 2 else "***"

        return {
            "host": masked,
            "endpoint_fp": hashlib.sha256(self.base_url.encode("utf-8")).hexdigest()[:12],
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

# 기본 모델의 **가중치 리비전(git 커밋 해시)**. 값의 출처는 `infra/model-pin.json`이며
# 이 상수는 그 파일을 읽어 채운다 — 매니페스트와 코드가 따로 놀면 고정의 의미가 없다.
#
# 왜 고정하는가: 모델 이름만 적으면 런타임에 HuggingFace `main`의 **현재** 내용을 받는다.
# 같은 이름으로 다른 가중치가 재배포되면 (a) 과거 측정치를 재현할 수 없고 (b) 바뀐 사실을
# 탐지할 수단도 없다. 재현성과 공급망 무결성이 동시에 깨진다 (ADR-011, owasp-notes §3.1 LLM03).
_MODEL_PIN_FILE = REPO_ROOT / "infra" / "model-pin.json"


def _load_pinned_revision() -> tuple[str, str]:
    """`infra/model-pin.json`에서 (repo_id, revision)을 읽는다.

    매니페스트가 없거나 깨져 있으면 빈 값을 돌려준다 — 고정이 풀린 채로 도는 것이
    임포트 실패보다 낫다고 판단해서가 아니라, **고정 여부를 `EmbeddingSettings.pinned`로
    드러내 호출부가 볼 수 있게** 하기 위해서다. 조용히 `latest`로 떨어지지는 않는다.
    """
    try:
        raw = json.loads(_MODEL_PIN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ("", "")
    return (str(raw.get("repo_id", "")), str(raw.get("revision", "")))


_PINNED_REPO_ID, _PINNED_REVISION = _load_pinned_revision()

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
    # 가중치 리비전(HuggingFace git 커밋 해시). 빈 문자열이면 `main`의 현재 내용을
    # 받는다 = 고정되지 않은 상태다 (ADR-011).
    revision: str = ""
    # 미리 내려받은 가중치 디렉터리. 채워져 있으면 네트워크를 타지 않는다.
    # 컨테이너는 빌드 시점에 받아 이 경로를 가리킨다 (ADR-010).
    local_path: str = ""

    @property
    def uses_prefixes(self) -> bool:
        """e5 계열만 접두사를 쓴다. 다른 모델로 바꾸면 접두사를 비워야 한다."""
        return bool(self.query_prefix or self.passage_prefix)

    @property
    def pinned(self) -> bool:
        """가중치가 특정 리비전에 고정돼 있는가.

        로컬 경로를 쓰는 경우도 고정으로 본다 — 그 경로는 `scripts/fetch_model.py`가
        리비전 + sha256 검증을 거쳐 만든 것이기 때문이다.
        """
        return bool(self.revision or self.local_path)

    @property
    def load_target(self) -> str:
        """sentence-transformers에 넘길 모델 식별자.

        로컬 경로가 있으면 그쪽이 우선이다. 네트워크 없이 도는 것이 컨테이너·CI에서
        기본 동작이어야 한다.
        """
        return self.local_path or self.model_name

    def redacted(self) -> dict[str, object]:
        """진단 출력용. 여기엔 시크릿이 없지만 다른 설정과 형태를 맞춘다."""
        return {
            "model": self.model_name,
            "revision": self.revision[:12] if self.revision else None,
            "pinned": self.pinned,
            "source": "local" if self.local_path else "huggingface",
            "device": self.device,
        }


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

    # 리비전 결정 순서: 환경변수 > 매니페스트(단, 같은 repo일 때만) > 빈 값.
    #
    # **매니페스트 리비전을 다른 모델에 적용하지 않는 것이 핵심이다.** `EMBEDDING_MODEL`을
    # 바꿨는데 e5-small의 커밋 해시를 그대로 들이밀면 "그런 리비전 없음"으로 실패하거나,
    # 더 나쁘게는 엉뚱한 해시가 우연히 존재해 다른 가중치를 받는다.
    revision = (os.getenv("EMBEDDING_REVISION") or "").strip()
    if not revision and model_name == _PINNED_REPO_ID:
        revision = _PINNED_REVISION

    return EmbeddingSettings(
        model_name=model_name,
        device=(os.getenv("EMBEDDING_DEVICE") or "cpu").strip() or "cpu",
        query_prefix=_E5_QUERY_PREFIX if is_e5 else "",
        passage_prefix=_E5_PASSAGE_PREFIX if is_e5 else "",
        revision=revision,
        local_path=(os.getenv("EMBEDDING_LOCAL_PATH") or "").strip(),
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


@dataclass(frozen=True)
class CacheSettings:
    """LLM 응답 캐시 설정 (ADR-008).

    기본값이 **꺼짐**인 이유: 캐시는 측정을 왜곡한다. 캐시가 기본으로 켜져 있으면
    "이 하네스 변경이 지연을 줄였다"와 "두 번째 실행이라 캐시를 탔다"를 구분할 수
    없게 된다. 이 프로젝트가 재려는 것이 정확히 하네스의 효과이므로(ADR-002),
    캐시는 명시적으로 켠다.
    """

    enabled: bool = False
    cache_dir: Path = REPO_ROOT / "var" / "llm_cache"

    def redacted(self) -> dict[str, object]:
        return {"enabled": self.enabled, "cache_dir": str(self.cache_dir)}


def load_cache_settings(env_file: Path | None = DEFAULT_ENV_FILE) -> CacheSettings:
    _ensure_env_loaded(env_file)
    raw_dir = (os.getenv("LLM_CACHE_DIR") or "").strip()
    cache_dir = Path(raw_dir) if raw_dir else REPO_ROOT / "var" / "llm_cache"
    if not cache_dir.is_absolute():
        cache_dir = REPO_ROOT / cache_dir
    return CacheSettings(
        enabled=(os.getenv("LLM_CACHE") or "").strip().lower() in {"1", "true", "yes", "on"},
        cache_dir=cache_dir,
    )
