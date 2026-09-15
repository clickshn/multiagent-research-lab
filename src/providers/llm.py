"""LiteLLM 기반 LLM 프로바이더.

Orchestrator와 Sub-agent는 이 모듈의 `LLMProvider` 인터페이스에만 의존한다.
엔드포인트·모델·프로바이더 종류가 바뀌어도 호출부는 바뀌지 않는다 (ADR-003).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from .config import ProviderSettings, load_settings

Role = Literal["system", "user", "assistant"]


class LLMError(RuntimeError):
    """모델 호출 실패. 원인 예외는 `__cause__`에 연결된다."""


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class LLMResponse:
    """모델 응답 + 계측에 필요한 메타데이터.

    토큰·지연을 응답에 함께 실어, 노드가 별도 계측 코드를 갖지 않아도
    감사 로그(CLAUDE.md)와 운영 가시성 요구를 충족할 수 있게 한다.
    """

    text: str
    model: str
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    raw: Any = field(default=None, repr=False, compare=False)

    # 캐시 적중으로 돌려준 응답인지 (ADR-008). 적중이면 `latency_s`는 0에 가깝고
    # 실제 청구 토큰도 0이다 — 비용·지연 집계가 이 플래그로 둘을 구분한다.
    cached: bool = False
    # 캐시에 저장될 당시 이 호출이 실제로 걸렸던 지연. 적중일 때만 의미가 있으며
    # "캐시가 없었다면 걸렸을 시간"을 재는 데 쓴다.
    origin_latency_s: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def billed_tokens(self) -> int:
        """실제로 엔드포인트에 청구된 토큰. 캐시 적중이면 0이다.

        `total_tokens`와 나누는 이유: 캐시 효과를 재려면 "요청이 요구한 토큰"과
        "실제로 모델이 처리한 토큰"이 서로 다른 수라는 것을 타입이 드러내야 한다.
        """
        return 0 if self.cached else self.total_tokens

    @property
    def truncated(self) -> bool:
        """출력 토큰 상한에 걸려 잘렸는지. 검증 루프에서 재시도 판단에 쓴다."""
        return self.finish_reason == "length"


@runtime_checkable
class LLMProvider(Protocol):
    """노드가 의존하는 유일한 모델 호출 인터페이스."""

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        stop: Sequence[str] | None = None,
    ) -> LLMResponse: ...


# 호출 1건이 끝날 때마다 불리는 관측 훅. Langfuse 연동 자리를 미리 열어둔다.
CallObserver = Callable[["LLMResponse"], None]


class LiteLLMProvider:
    """OpenAI 호환 커스텀 엔드포인트를 LiteLLM으로 호출한다.

    LiteLLM을 쓰는 이유는 여러 벤더를 섞어 쓰기 위함이 아니라, 프로바이더 교체
    지점을 한 곳(`ProviderSettings`)으로 모으기 위함이다.
    """

    def __init__(
        self,
        settings: ProviderSettings | None = None,
        *,
        observer: CallObserver | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self._observer = observer

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        stop: Sequence[str] | None = None,
    ) -> LLMResponse:
        # litellm은 임포트가 무거워 모듈 로드 시점이 아니라 호출 시점에 가져온다.
        import litellm

        payload: dict[str, Any] = {
            "model": self.settings.litellm_model,
            "api_base": self.settings.base_url,
            "api_key": self.settings.api_key,
            "messages": [m.as_dict() for m in messages],
            "temperature": temperature,
            "timeout": self.settings.timeout_s,
            "num_retries": self.settings.max_retries,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if stop:
            payload["stop"] = list(stop)

        started = time.perf_counter()
        try:
            raw = litellm.completion(**payload)
        except Exception as exc:  # noqa: BLE001 - 벤더 예외를 경계에서 단일 타입으로 덮는다
            raise LLMError(
                f"모델 호출 실패 ({self.settings.redacted()}): {exc}"
            ) from exc
        latency_s = time.perf_counter() - started

        response = _to_response(raw, latency_s=latency_s, fallback_model=self.settings.model)
        if self._observer is not None:
            self._observer(response)
        return response


def _to_response(raw: Any, *, latency_s: float, fallback_model: str) -> LLMResponse:
    """LiteLLM 응답 객체를 우리 타입으로 좁힌다.

    벤더 응답 스키마 변화가 노드 코드까지 번지지 않도록 여기서만 흡수한다.
    """
    try:
        choice = raw.choices[0]
        text = choice.message.content or ""
        finish_reason = getattr(choice, "finish_reason", None)
    except (AttributeError, IndexError, TypeError) as exc:
        raise LLMError(f"예상치 못한 응답 형식: {raw!r}") from exc

    usage = getattr(raw, "usage", None)
    return LLMResponse(
        text=text,
        model=getattr(raw, "model", None) or fallback_model,
        finish_reason=finish_reason,
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        latency_s=latency_s,
        raw=raw,
    )


def get_provider(
    settings: ProviderSettings | None = None,
    *,
    observer: CallObserver | None = None,
    cache: bool | None = None,
) -> LLMProvider:
    """기본 프로바이더를 만든다. 노드는 이 함수만 알면 된다.

    `cache`가 None이면 `.env`의 `LLM_CACHE`를 따른다 (기본 꺼짐 — ADR-008).
    캐시는 `LLMProvider`를 감싸는 데코레이터라, 켜고 꺼도 노드가 보는 인터페이스는
    똑같다. 측정 스크립트가 `cache=True/False`로 같은 파이프라인을 두 조건에서
    돌릴 수 있는 이유다.
    """
    base = LiteLLMProvider(settings, observer=observer)

    from .config import load_cache_settings

    cache_settings = load_cache_settings()
    use_cache = cache_settings.enabled if cache is None else cache
    if not use_cache:
        return base

    from .cache import CachingLLMProvider

    return CachingLLMProvider(base, cache_settings)
