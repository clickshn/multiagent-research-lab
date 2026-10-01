"""`response_format` 전달 경로 테스트 (v1.2-T2).

session-17 §5.4는 `tools`가 세 곳에서 빠진다고 적었다 — 프로바이더 시그니처, 응답 파싱,
캐시 키. `response_format`도 같은 세 곳(+ span 입력 해시)에서 빠져 있었다. 여기서 붙드는 것:

1. **보낸다.** 인자를 주면 LiteLLM 요청 본문에 그대로 실린다(로컬 가짜 서버로 본문을 본다).
2. **안 주면 아무것도 바뀌지 않는다.** payload·캐시 키·입력 해시가 인자가 없던 때와 같다.
   도구 off 경로가 p1-on과 같다는 주장이 이 성질에 기댄다.
3. **스키마가 다르면 캐시 키가 다르다.** 같으면 스키마만 다른 호출이 서로의 응답을 받는다.
4. **응답 파싱:** json_schema 응답은 `message.content`에 JSON 문자열로 온다 — 기존 파싱 그대로.

외부 호출은 없다. 가짜 서버는 127.0.0.1에만 뜬다.
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.orchestrator import nodes
from src.providers.cache import CachingLLMProvider, make_cache_key
from src.providers.config import CacheSettings, ProviderSettings
from src.providers.llm import ChatMessage, LiteLLMProvider, LLMResponse, _to_response

SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "pick",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"tech_domain": {"type": "string", "enum": ["Agent", "없음"]}},
            "required": ["tech_domain"],
            "additionalProperties": False,
        },
    },
}
OTHER_SCHEMA = json.loads(json.dumps(SCHEMA).replace("Agent", "RAG"))


def _msgs():
    return [ChatMessage("system", "시스템"), ChatMessage("user", "질문")]


def _response(text: str = "{}") -> LLMResponse:
    return LLMResponse(
        text=text, model="m", finish_reason="stop",
        prompt_tokens=1, completion_tokens=1, latency_s=0.1,
    )


# ---------------------------------------------------------------------------
# 1·2. 프로바이더 payload
# ---------------------------------------------------------------------------


class _FakeLiteLLM:
    def __init__(self):
        self.payloads: list[dict] = []

    def completion(self, **payload):
        self.payloads.append(payload)
        raw = type("R", (), {})()
        msg = type("M", (), {"content": '{"tech_domain": "Agent"}'})()
        raw.choices = [type("C", (), {"message": msg, "finish_reason": "stop"})()]
        raw.usage = None
        raw.model = "m"
        return raw


@pytest.fixture()
def fake_litellm(monkeypatch):
    import litellm

    fake = _FakeLiteLLM()
    monkeypatch.setattr(litellm, "completion", fake.completion)
    return fake


def _provider():
    return LiteLLMProvider(ProviderSettings(base_url="http://example.invalid/v1", model="m"))


def test_payload_carries_response_format(fake_litellm):
    _provider().complete(_msgs(), max_tokens=16, response_format=SCHEMA)
    assert fake_litellm.payloads[0]["response_format"] == SCHEMA


def test_payload_has_no_response_format_key_when_none(fake_litellm):
    """None이면 키 자체가 없다 — `response_format: null`을 보내면 요청 본문이 바뀐다."""
    _provider().complete(_msgs(), max_tokens=16)
    _provider().complete(_msgs(), max_tokens=16, response_format=None)
    assert all("response_format" not in p for p in fake_litellm.payloads)
    assert fake_litellm.payloads[0] == fake_litellm.payloads[1]


def test_litellm_sends_response_format_unchanged_on_the_wire():
    """LiteLLM이 `openai/` 경로에서 값을 변형하지 않는지 — 실제 HTTP 본문으로 본다.

    일부 프로바이더 경로에서 LiteLLM은 json_schema를 도구 호출로 바꿔 보낸다. 우리 경로에서
    그렇게 되면 서버가 받는 것이 우리가 강제한 스키마가 아니다.
    """
    bodies: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers["Content-Length"]))
            bodies.append(json.loads(body))
            out = json.dumps({
                "id": "x", "object": "chat.completion", "created": 0, "model": "m",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant",
                                         "content": '{"tech_domain": "Agent"}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = ProviderSettings(
            base_url=f"http://127.0.0.1:{server.server_port}/v1", model="m", max_retries=0
        )
        response = LiteLLMProvider(settings).complete(
            _msgs(), max_tokens=16, response_format=SCHEMA
        )
    finally:
        server.shutdown()

    assert bodies[0]["response_format"] == SCHEMA
    assert "tools" not in bodies[0] and "tool_choice" not in bodies[0]
    # 4. 응답 파싱 — 구조화 출력은 content에 문자열로 온다.
    assert json.loads(response.text) == {"tech_domain": "Agent"}


def test_to_response_reads_structured_content():
    raw = _FakeLiteLLM().completion()
    assert json.loads(_to_response(raw, latency_s=0.0, fallback_model="m").text) == {
        "tech_domain": "Agent"
    }


# ---------------------------------------------------------------------------
# 3. 캐시 키
# ---------------------------------------------------------------------------


def _legacy_key(**kw) -> str:
    """`response_format` 도입 전 `make_cache_key`의 직렬화 그대로."""
    payload = {
        "v": 1,
        "model": kw["model"],
        "temperature": kw["temperature"],
        "max_tokens": kw["max_tokens"],
        "stop": None,
        "messages": [[m.role, m.content] for m in kw["messages"]],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def test_cache_key_unchanged_without_schema():
    base = dict(model="m", messages=_msgs(), temperature=0.0, max_tokens=16, stop=None)
    assert make_cache_key(**base) == _legacy_key(**base)
    assert make_cache_key(**base, response_format=None) == _legacy_key(**base)


def test_cache_key_reflects_schema():
    base = dict(model="m", messages=_msgs(), temperature=0.0, max_tokens=16, stop=None)
    plain = make_cache_key(**base)
    with_schema = make_cache_key(**base, response_format=SCHEMA)
    other = make_cache_key(**base, response_format=OTHER_SCHEMA)
    assert len({plain, with_schema, other}) == 3


class _Inner:
    def __init__(self):
        self.kwargs: list[dict] = []
        self.settings = type("S", (), {"model": "m"})()

    def complete(self, messages, **kwargs):
        self.kwargs.append(kwargs)
        return _response(json.dumps({"n": len(self.kwargs)}))


class _LegacyInner:
    """`response_format`을 모르는 옛 시그니처."""

    settings = type("S", (), {"model": "m"})()

    def complete(self, messages, *, temperature=0.0, max_tokens=None, stop=None):
        return _response()


def test_caching_provider_separates_schemas_and_passes_them_through(tmp_path):
    inner = _Inner()
    provider = CachingLLMProvider(inner, CacheSettings(enabled=True, cache_dir=tmp_path))

    first = provider.complete(_msgs(), max_tokens=16, response_format=SCHEMA)
    provider.complete(_msgs(), max_tokens=16, response_format=OTHER_SCHEMA)
    again = provider.complete(_msgs(), max_tokens=16, response_format=SCHEMA)
    provider.complete(_msgs(), max_tokens=16)

    assert len(inner.kwargs) == 3  # 스키마 둘 + 무스키마 하나, 세 번째 호출은 적중
    assert again.cached and again.text == first.text
    assert inner.kwargs[0]["response_format"] == SCHEMA
    assert inner.kwargs[1]["response_format"] == OTHER_SCHEMA
    assert "response_format" not in inner.kwargs[2]


def test_caching_provider_works_with_legacy_inner_without_schema(tmp_path):
    provider = CachingLLMProvider(_LegacyInner(), CacheSettings(enabled=True, cache_dir=tmp_path))
    provider.complete(_msgs(), max_tokens=16)
    provider.complete(_msgs(), max_tokens=16, temperature=0.5)


# ---------------------------------------------------------------------------
# span 입력 해시 · 노드 호출 헬퍼
# ---------------------------------------------------------------------------


def test_input_hash_unchanged_without_schema():
    legacy = hashlib.sha256(json.dumps(
        {"messages": [[m.role, m.content] for m in _msgs()], "temperature": 0.0, "max_tokens": 16},
        sort_keys=True, ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    assert nodes._input_hash(_msgs(), temperature=0.0, max_tokens=16) == legacy
    assert nodes._input_hash(_msgs(), temperature=0.0, max_tokens=16, response_format=None) == legacy
    assert nodes._input_hash(_msgs(), temperature=0.0, max_tokens=16, response_format=SCHEMA) != legacy


def test_call_helper_passes_schema_only_when_given():
    inner = _Inner()
    nodes._call(inner, node="x", trace=None, system="s", user="u", max_tokens=8)
    nodes._call(inner, node="x", trace=None, system="s", user="u", max_tokens=8,
                response_format=SCHEMA)
    assert "response_format" not in inner.kwargs[0]
    assert inner.kwargs[1]["response_format"] == SCHEMA
    # 옛 시그니처 프로바이더도 스키마 없이는 그대로 불린다.
    nodes._call(_LegacyInner(), node="x", trace=None, system="s", user="u", max_tokens=8)


# ---------------------------------------------------------------------------
# 선택 스키마 (ADR-028)
# ---------------------------------------------------------------------------


def test_tech_domain_schema_is_order_independent_and_ends_with_abstain():
    a = nodes.tech_domain_schema(["RAG", "Agent", "LLM"])
    b = nodes.tech_domain_schema(["LLM", "RAG", "Agent", "Agent"])
    assert a == b
    enum = a["json_schema"]["schema"]["properties"]["tech_domain"]["enum"]
    assert enum == ["Agent", "LLM", "RAG", nodes.TECH_DOMAIN_ABSTAIN]
    assert a["json_schema"]["strict"] is True
    assert a["json_schema"]["schema"]["additionalProperties"] is False


def test_tech_domain_schema_rejects_abstain_inside_vocab():
    with pytest.raises(ValueError):
        nodes.tech_domain_schema(["Agent", nodes.TECH_DOMAIN_ABSTAIN])
