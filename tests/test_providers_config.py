"""프로바이더 설정 로딩 검증.

session-02 핸드오프의 남은 항목: "설정 로딩 / 엔드포인트 교체 시 호출부 불변 검증".

이 계층의 목적은 **엔드포인트 교체가 코드 변경이 되지 않게 하는 것**이다. 그래서
테스트도 "설정을 바꾸면 호출부를 건드리지 않고 목적지가 바뀌는가"를 본다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.providers.config import (
    ConfigError,
    EmbeddingSettings,
    ProviderSettings,
    TracingSettings,
    load_embedding_settings,
    load_settings,
    load_tracing_settings,
    load_vectorstore_settings,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """실제 `.env`가 테스트 결과에 새어 들어오지 않게 한다."""
    for key in (
        "VLLM_BASE",
        "VLLM_MODEL",
        "VLLM_API_KEY",
        "EMBEDDING_MODEL",
        "EMBEDDING_DEVICE",
        "CHROMA_DIR",
        "CHROMA_COLLECTION",
        "LANGFUSE_HOST",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "TRACE_LOG_DIR",
    ):
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# LLM 설정
# ---------------------------------------------------------------------------


def test_missing_env_raises_with_actionable_message(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="VLLM_BASE"):
        load_settings(env_file=None)


def test_base_url_must_be_openai_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/v1`이 아니면 호출 시점이 아니라 설정 시점에 실패한다."""
    monkeypatch.setenv("VLLM_BASE", "https://example.invalid/api")
    monkeypatch.setenv("VLLM_MODEL", "some-model")
    with pytest.raises(ConfigError, match="/v1"):
        load_settings(env_file=None)


def test_trailing_slash_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VLLM_BASE", "https://example.invalid/v1/")
    monkeypatch.setenv("VLLM_MODEL", "some-model")
    assert load_settings(env_file=None).base_url == "https://example.invalid/v1"


def test_redacted_hides_path_and_key() -> None:
    """진단 출력에 URL 전체가 실리면 안 된다 — URL이 곧 접근 권한이다."""
    settings = ProviderSettings(
        base_url="https://secret-host.example/private-path/v1", model="m"
    )
    redacted = settings.redacted()

    assert redacted["host"] == "secret-host.example"
    assert "private-path" not in str(redacted)
    assert redacted["auth"] == "none"


def test_redacted_reports_api_key_presence_without_value() -> None:
    settings = ProviderSettings(
        base_url="https://h.example/v1", model="m", api_key="super-secret"
    )
    redacted = settings.redacted()

    assert redacted["auth"] == "api-key"
    assert "super-secret" not in str(redacted)


def test_litellm_model_prefix_is_protocol_not_vendor() -> None:
    """`openai/` 접두사는 프로토콜 표시지 목적지가 아니다. 목적지는 base_url이다."""
    settings = ProviderSettings(base_url="https://h.example/v1", model="gemma-4-31B-it")
    assert settings.litellm_model == "openai/gemma-4-31B-it"


def test_endpoint_swap_does_not_change_call_site(monkeypatch: pytest.MonkeyPatch) -> None:
    """엔드포인트·모델 교체가 설정 한 곳에서만 일어나는지."""
    monkeypatch.setenv("VLLM_BASE", "https://first.example/v1")
    monkeypatch.setenv("VLLM_MODEL", "model-a")
    first = load_settings(env_file=None)

    monkeypatch.setenv("VLLM_BASE", "https://second.example/v1")
    monkeypatch.setenv("VLLM_MODEL", "model-b")
    second = load_settings(env_file=None)

    assert (first.base_url, first.model) != (second.base_url, second.model)
    # 두 설정 모두 같은 타입이라, 호출부는 ProviderSettings만 알면 된다.
    assert type(first) is type(second) is ProviderSettings


# ---------------------------------------------------------------------------
# 임베딩 설정 (ADR-005)
# ---------------------------------------------------------------------------


def test_embedding_defaults_to_multilingual_model() -> None:
    settings = load_embedding_settings(env_file=None)
    assert settings.model_name == "intfloat/multilingual-e5-small"
    assert settings.uses_prefixes


def test_e5_prefixes_are_disabled_for_non_e5_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """모델을 바꿨는데 접두사가 남아 검색 품질이 조용히 나빠지는 상황을 막는다."""
    monkeypatch.setenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    settings = load_embedding_settings(env_file=None)

    assert settings.query_prefix == ""
    assert settings.passage_prefix == ""
    assert not settings.uses_prefixes


def test_e5_prefixes_are_distinct() -> None:
    settings = EmbeddingSettings()
    assert settings.query_prefix != settings.passage_prefix


# ---------------------------------------------------------------------------
# 벡터 스토어 / 계측 설정
# ---------------------------------------------------------------------------


def test_vectorstore_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_vectorstore_settings(env_file=None)
    assert settings.collection == "research_corpus"
    assert settings.persist_dir.is_absolute()
    assert settings.persist_dir.name == "chroma"


def test_relative_chroma_dir_is_anchored_to_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    """상대 경로를 줘도 실행 위치에 따라 인덱스가 갈라지지 않는다."""
    monkeypatch.setenv("CHROMA_DIR", "tmp/index")
    settings = load_vectorstore_settings(env_file=None)

    assert settings.persist_dir.is_absolute()
    assert settings.persist_dir.parts[-2:] == ("tmp", "index")


def test_langfuse_is_off_unless_fully_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """부분 설정으로 외부 전송이 켜지지 않는다."""
    assert not load_tracing_settings(env_file=None).langfuse_enabled

    monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.internal")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    assert not load_tracing_settings(env_file=None).langfuse_enabled

    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    assert load_tracing_settings(env_file=None).langfuse_enabled


def test_tracing_redacted_hides_keys() -> None:
    settings = TracingSettings(
        langfuse_host="https://langfuse.internal/path",
        langfuse_public_key="pk-secret",
        langfuse_secret_key="sk-secret",
        local_trace_dir=Path("var/traces"),
    )
    redacted = settings.redacted()

    assert redacted["backend"] == "langfuse"
    assert "pk-secret" not in str(redacted)
    assert "sk-secret" not in str(redacted)


def test_tracing_backend_label_reflects_fallback() -> None:
    assert TracingSettings().redacted()["backend"] == "local-jsonl"
