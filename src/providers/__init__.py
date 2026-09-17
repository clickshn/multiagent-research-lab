"""모델 프로바이더 추상화 계층 (ADR-003).

엔드포인트·모델 교체가 Orchestrator / Sub-agent 코드에 번지지 않도록,
모델 호출은 전부 이 패키지를 거친다. 바깥에서는 아래 이름만 임포트한다.
"""

from .cache import CacheStats, CachingLLMProvider, clear_cache
from .config import (
    CacheSettings,
    ConfigError,
    ProviderSettings,
    load_cache_settings,
    load_settings,
)
from .egress import (
    EXTERNAL_LLM_HOST_SUBSTRINGS,
    EXTERNAL_LLM_HOSTS,
    ExternalEndpointError,
    assert_internal_endpoint,
    match_external_vendor,
)
from .llm import (
    ChatMessage,
    LiteLLMProvider,
    LLMError,
    LLMProvider,
    LLMResponse,
    get_provider,
)

__all__ = [
    "assert_internal_endpoint",
    "CacheSettings",
    "CacheStats",
    "CachingLLMProvider",
    "ChatMessage",
    "clear_cache",
    "EXTERNAL_LLM_HOST_SUBSTRINGS",
    "EXTERNAL_LLM_HOSTS",
    "ExternalEndpointError",
    "match_external_vendor",
    "load_cache_settings",
    "ConfigError",
    "get_provider",
    "LiteLLMProvider",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "load_settings",
    "ProviderSettings",
]
