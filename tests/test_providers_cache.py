"""LLM 응답 캐시 테스트 (ADR-008).

여기서 지키려는 것은 세 가지다.

1. 같은 입력이면 엔드포인트를 **다시 부르지 않는다** (호출 수로 확인한다 —
   응답이 같은 것만으로는 캐시가 동작했다는 증거가 못 된다).
2. 입력이 조금이라도 다르면 **부른다**. 키가 너무 헐거우면 다른 질의에 같은
   답을 돌려주는 조용한 오류가 난다.
3. `temperature > 0` 호출은 캐시하지 않는다. 샘플링 다양성을 캐시가 없애면
   실험 결과가 달라진다.
"""

from __future__ import annotations

import pytest

from src.providers.cache import CachingLLMProvider, clear_cache, make_cache_key
from src.providers.config import CacheSettings
from src.providers.llm import ChatMessage, LLMResponse


class RecordingProvider:
    """호출 횟수를 세는 가짜 프로바이더."""

    def __init__(self, text: str = "응답") -> None:
        self.calls: list[dict] = []
        self.text = text
        # CachingLLMProvider가 모델 이름을 캐시 키에 넣기 위해 읽는 곳.
        self.settings = type("S", (), {"model": "fake-model"})()

    def complete(self, messages, *, temperature=0.0, max_tokens=None, stop=None):
        self.calls.append(
            {
                "messages": list(messages),
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return LLMResponse(
            text=self.text,
            model="fake-model",
            finish_reason="stop",
            prompt_tokens=100,
            completion_tokens=20,
            latency_s=1.5,
        )


@pytest.fixture()
def cache_settings(tmp_path):
    return CacheSettings(enabled=True, cache_dir=tmp_path / "llm_cache")


def _msgs(user: str = "질문"):
    return [ChatMessage("system", "시스템"), ChatMessage("user", user)]


def test_second_identical_call_does_not_reach_endpoint(cache_settings):
    inner = RecordingProvider()
    provider = CachingLLMProvider(inner, cache_settings)

    first = provider.complete(_msgs(), max_tokens=256)
    second = provider.complete(_msgs(), max_tokens=256)

    assert len(inner.calls) == 1, "같은 입력인데 엔드포인트를 두 번 불렀다"
    assert second.text == first.text
    assert second.cached is True and first.cached is False
    assert provider.stats.hits == 1 and provider.stats.misses == 1


def test_cache_hit_reports_zero_billed_tokens_and_keeps_origin_latency(cache_settings):
    """비용·지연 집계의 근거가 되는 두 값.

    적중 응답은 토큰 수 자체는 유지하되(무엇을 아꼈는지 알아야 하므로)
    `billed_tokens`는 0이어야 한다.
    """
    provider = CachingLLMProvider(RecordingProvider(), cache_settings)
    provider.complete(_msgs(), max_tokens=256)
    hit = provider.complete(_msgs(), max_tokens=256)

    assert hit.total_tokens == 120
    assert hit.billed_tokens == 0
    assert hit.latency_s == 0.0
    assert hit.origin_latency_s == pytest.approx(1.5, abs=0.01)
    assert provider.stats.saved_prompt_tokens == 100
    assert provider.stats.saved_completion_tokens == 20


@pytest.mark.parametrize(
    "kwargs_a, kwargs_b",
    [
        ({"max_tokens": 256}, {"max_tokens": 512}),
        ({"max_tokens": 256}, {"max_tokens": 256, "stop": ["\n\n"]}),
    ],
)
def test_different_parameters_miss(cache_settings, kwargs_a, kwargs_b):
    inner = RecordingProvider()
    provider = CachingLLMProvider(inner, cache_settings)

    provider.complete(_msgs(), **kwargs_a)
    provider.complete(_msgs(), **kwargs_b)

    assert len(inner.calls) == 2


def test_different_message_content_misses(cache_settings):
    inner = RecordingProvider()
    provider = CachingLLMProvider(inner, cache_settings)

    provider.complete(_msgs("질문 A"), max_tokens=256)
    provider.complete(_msgs("질문 B"), max_tokens=256)

    assert len(inner.calls) == 2


def test_sampling_calls_are_not_cached(cache_settings):
    """temperature > 0이면 캐시를 우회한다 — 다양성을 조용히 없애지 않는다."""
    inner = RecordingProvider()
    provider = CachingLLMProvider(inner, cache_settings)

    provider.complete(_msgs(), temperature=0.7, max_tokens=256)
    provider.complete(_msgs(), temperature=0.7, max_tokens=256)

    assert len(inner.calls) == 2
    assert provider.stats.total == 0, "캐시를 우회한 호출은 적중률 통계에 섞이면 안 된다"


def test_cache_survives_a_new_provider_instance(cache_settings):
    """프로세스 경계를 넘어 살아남는다 — 인메모리였다면 실패한다."""
    inner_a = RecordingProvider()
    CachingLLMProvider(inner_a, cache_settings).complete(_msgs(), max_tokens=256)

    inner_b = RecordingProvider()
    second = CachingLLMProvider(inner_b, cache_settings).complete(_msgs(), max_tokens=256)

    assert len(inner_b.calls) == 0
    assert second.cached is True


def test_corrupt_cache_file_falls_back_to_the_endpoint(cache_settings):
    """캐시는 최적화지 정확성의 일부가 아니다. 깨져도 실행은 계속돼야 한다."""
    inner = RecordingProvider()
    provider = CachingLLMProvider(inner, cache_settings)
    provider.complete(_msgs(), max_tokens=256)

    for path in cache_settings.cache_dir.rglob("*.json"):
        path.write_text("{ 깨진 파일", encoding="utf-8")

    result = provider.complete(_msgs(), max_tokens=256)
    assert len(inner.calls) == 2
    assert result.cached is False


def test_clear_cache_removes_entries(cache_settings):
    inner = RecordingProvider()
    provider = CachingLLMProvider(inner, cache_settings)
    provider.complete(_msgs(), max_tokens=256)

    assert clear_cache(cache_settings) == 1
    provider.complete(_msgs(), max_tokens=256)
    assert len(inner.calls) == 2


def test_cache_key_is_stable_across_calls():
    """키가 흔들리면 캐시가 조용히 무력해진다."""
    args = dict(
        model="m", messages=_msgs(), temperature=0.0, max_tokens=128, stop=None
    )
    assert make_cache_key(**args) == make_cache_key(**args)


def test_get_provider_returns_plain_provider_when_cache_off(monkeypatch):
    """기본은 캐시 꺼짐이다 (ADR-008 — 측정 왜곡 방지)."""
    monkeypatch.setenv("VLLM_BASE", "http://example.invalid/v1")
    monkeypatch.setenv("VLLM_MODEL", "fake-model")
    monkeypatch.delenv("LLM_CACHE", raising=False)

    from src.providers import get_provider
    from src.providers.cache import CachingLLMProvider as CLP

    assert not isinstance(get_provider(), CLP)
    assert isinstance(get_provider(cache=True), CLP)
