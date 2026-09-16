"""프로바이더 설정 로딩 검증.

session-02 핸드오프의 남은 항목: "설정 로딩 / 엔드포인트 교체 시 호출부 불변 검증".

이 계층의 목적은 **엔드포인트 교체가 코드 변경이 되지 않게 하는 것**이다. 그래서
테스트도 "설정을 바꾸면 호출부를 건드리지 않고 목적지가 바뀌는가"를 본다.
"""

from __future__ import annotations

import json
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
        "EMBEDDING_REVISION",
        "EMBEDDING_LOCAL_PATH",
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


def test_redacted_hides_host_not_just_path() -> None:
    """진단 출력에 URL 전체가 실리면 안 된다 — URL이 곧 접근 권한이다.

    **session-06에 강화됨.** 이전 버전은 경로만 버리고 호스트를 남겼는데, 실제
    엔드포인트의 경로는 `/v1`뿐이라 아무것도 가려지지 않았다. 추측 불가능한 부분이
    호스트의 앞쪽 라벨이므로 거기를 가린다.
    """
    settings = ProviderSettings(
        base_url="https://secret-token-abc123.proxy.vendor.example/v1", model="m"
    )
    redacted = settings.redacted()

    assert "secret-token-abc123" not in str(redacted)
    assert "proxy" not in str(redacted)
    # 벤더를 알아볼 수 있는 공개 도메인은 남긴다 — 진단에 필요하고 접근 권한은 아니다.
    assert redacted["host"] == "***.vendor.example"
    assert redacted["auth"] == "none"


def test_redacted_fingerprint_compares_without_disclosing() -> None:
    """엔드포인트가 바뀌었는지를 URL을 드러내지 않고 판단할 수 있어야 한다.

    모델·엔드포인트가 바뀌면 캐시를 비워야 한다 (ADR-008). 그 판단 근거를
    남기되 값 자체는 남기지 않는다.
    """
    a = ProviderSettings(base_url="https://one.vendor.example/v1", model="m")
    b = ProviderSettings(base_url="https://two.vendor.example/v1", model="m")

    assert a.redacted()["endpoint_fp"] != b.redacted()["endpoint_fp"]
    assert a.redacted()["endpoint_fp"] == a.redacted()["endpoint_fp"]
    # 지문에서 URL을 복원할 수 없다.
    assert "one" not in str(a.redacted()["endpoint_fp"])


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
# 임베딩 가중치 리비전 고정 (ADR-011)
#
# 여기서 지키려는 성질은 "런타임에 HuggingFace latest를 받지 않는다" 하나다.
# 고정이 풀리면 에러가 아니라 **조용한 품질 변화**로 나타나므로 테스트로 못 박는다.
# ---------------------------------------------------------------------------


def test_default_embedding_model_is_pinned_to_a_revision() -> None:
    """기본 모델은 항상 커밋 해시에 고정돼 있어야 한다."""
    settings = load_embedding_settings(env_file=None)

    assert settings.pinned, "기본 임베딩 모델이 고정돼 있지 않다 (ADR-011)"
    assert len(settings.revision) == 40, f"git 커밋 해시가 아니다: {settings.revision!r}"
    assert set(settings.revision) <= set("0123456789abcdef")


def test_pin_manifest_and_config_agree() -> None:
    """매니페스트와 코드가 따로 놀면 고정의 의미가 없다."""
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "infra" / "model-pin.json").read_text(
            encoding="utf-8"
        )
    )
    settings = load_embedding_settings(env_file=None)

    assert manifest["repo_id"] == settings.model_name
    assert manifest["revision"] == settings.revision


def test_pinned_revision_is_not_applied_to_a_different_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """다른 모델로 바꾸면 e5-small의 해시를 물려주지 않는다.

    물려주면 "그런 리비전 없음"으로 실패하거나, 더 나쁘게는 우연히 존재하는
    엉뚱한 해시의 가중치를 받는다.
    """
    monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    settings = load_embedding_settings(env_file=None)

    assert settings.revision == ""
    assert not settings.pinned


def test_explicit_revision_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_REVISION", "a" * 40)
    settings = load_embedding_settings(env_file=None)
    assert settings.revision == "a" * 40


def test_local_path_takes_priority_as_load_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """컨테이너는 이미지에 구운 경로에서 읽는다 — 런타임에 네트워크를 타지 않는다."""
    monkeypatch.setenv("EMBEDDING_LOCAL_PATH", "/opt/models/e5-small")
    settings = load_embedding_settings(env_file=None)

    assert settings.load_target == "/opt/models/e5-small"
    assert settings.pinned
    # 인덱스 메타데이터 대조에 쓰이는 이름은 경로가 아니라 여전히 repo id여야 한다.
    # 경로가 이름이 되면 컨테이너에서 만든 인덱스를 로컬에서 못 읽는다.
    assert settings.model_name == "intfloat/multilingual-e5-small"


def test_embedding_redacted_has_no_surprises() -> None:
    settings = load_embedding_settings(env_file=None)
    assert settings.redacted()["pinned"] is True
    assert settings.redacted()["source"] == "huggingface"


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
