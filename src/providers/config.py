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
