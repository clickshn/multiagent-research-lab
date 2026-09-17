"""외부 LLM 벤더 호출 차단 검증 (ADR-021).

두 계층을 따로 본다.

1. **코드 계층** — `src/providers/egress.py` + `ProviderSettings.__post_init__`.
   환경변수를 바꿔 목적지를 벤더로 돌리는 경로를 막는다.
2. **도구 계층** — `.claude/hooks/check-external-llm.sh`.
   실제 케이스 검사는 `tests/gate/external_llm_cases.sh`가 돌리고, 여기서는 그 스크립트를
   호출해 종료 코드만 본다.

그리고 **두 계층의 거부 목록이 갈라지지 않는지**를 대조한다. 훅은 셸 스크립트라 Python
목록을 임포트할 수 없어 목록을 복제해 들고 있는데, 복제본이 조용히 갈라지면 "코드에서는
막히는데 명령줄에서는 통과"하는 구멍이 생긴다. 그 구멍을 사람이 기억으로 막지 않는다.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src.providers.config import ProviderSettings, load_settings
from src.providers.egress import (
    EXTERNAL_LLM_HOST_SUBSTRINGS,
    EXTERNAL_LLM_HOSTS,
    ExternalEndpointError,
    assert_internal_endpoint,
    host_of,
    match_external_vendor,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / ".claude" / "hooks" / "check-external-llm.sh"
GATE_SCRIPT = REPO_ROOT / "tests" / "gate" / "external_llm_cases.sh"


# ---------------------------------------------------------------------------
# 호스트 판정
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://api.anthropic.com/v1",
        "https://api.anthropic.com:443/v1/messages",
        "https://user:pass@api.anthropic.com/v1",
        "HTTPS://API.ANTHROPIC.COM/v1",
        "https://api.openai.com/v1",
        "https://my-resource.openai.azure.com/openai/deployments/x",
        "https://bedrock-runtime.ap-northeast-2.amazonaws.com/model/m/invoke",
        "https://generativelanguage.googleapis.com/v1beta",
        "https://openrouter.ai/api/v1",
    ],
)
def test_known_vendor_hosts_are_matched(url: str) -> None:
    assert match_external_vendor(url) is not None


@pytest.mark.parametrize(
    "url",
    [
        "https://gpu-endpoint.internal.example/v1",
        "http://localhost:8000/v1",
        "http://vllm:8000/v1",
        # 벤더 이름이 **경로**에 있을 뿐인 경우. 목적지는 우리 엔드포인트다.
        "https://internal.example/v1/openai-compat",
        # 도메인이 벤더 호스트를 접미사가 아니라 접두사로 포함하는 경우.
        "https://api.anthropic.com.internal.example/v1",
    ],
)
def test_internal_hosts_pass(url: str) -> None:
    assert match_external_vendor(url) is None
    assert_internal_endpoint(url)


def test_host_of_survives_garbage() -> None:
    """검사 경로에서 예외가 나면 검사가 없는 것과 같아진다."""
    assert host_of("") == ""
    assert host_of("not a url") == "not a url"
    assert match_external_vendor("") is None


# ---------------------------------------------------------------------------
# 코드 계층 — 타입이 불변식을 들고 있다
# ---------------------------------------------------------------------------


def test_provider_settings_rejects_vendor_endpoint() -> None:
    """`load_settings()`가 아니라 **생성자**에서 막힌다. 직접 생성 경로도 같은 검사를 받는다."""
    with pytest.raises(ExternalEndpointError):
        ProviderSettings(base_url="https://api.anthropic.com/v1", model="claude-opus-5")


def test_env_swap_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """`VLLM_BASE`만 바꿔 목적지를 벤더로 돌리는 우회 경로."""
    monkeypatch.setenv("VLLM_BASE", "https://api.anthropic.com/v1")
    monkeypatch.setenv("VLLM_MODEL", "claude-opus-5")
    with pytest.raises(ExternalEndpointError):
        load_settings(env_file=None)


def test_error_message_does_not_leak_the_url() -> None:
    """`VLLM_BASE`는 시크릿이다 (governance.md). 진단에 필요한 것은 어느 벤더인가뿐이다."""
    secret_path = "/v1/super-secret-deployment-id"
    with pytest.raises(ExternalEndpointError) as excinfo:
        assert_internal_endpoint("https://api.openai.com" + secret_path)
    assert secret_path not in str(excinfo.value)
    assert "api.openai.com" in str(excinfo.value)


def test_internal_endpoint_still_constructs() -> None:
    """차단이 정상 경로를 막지 않는다 — 오탐 대조."""
    settings = ProviderSettings(base_url="https://internal.example/v1", model="gemma-4-31B-it")
    assert settings.litellm_model == "openai/gemma-4-31B-it"


# ---------------------------------------------------------------------------
# 두 계층의 거부 목록 대조
# ---------------------------------------------------------------------------


def _expand_alternations(pattern: str) -> set[str]:
    """`api\\.cohere\\.(ai|com)` 같은 그룹을 리터럴 집합으로 편다."""
    out = {pattern}
    while True:
        expanded: set[str] = set()
        changed = False
        for item in out:
            m = re.search(r"\(([^()]*)\)", item)
            if m is None:
                expanded.add(item)
                continue
            changed = True
            for option in m.group(1).split("|"):
                expanded.add(item[: m.start()] + option + item[m.end() :])
        out = expanded
        if not changed:
            return out


def _hook_denylist() -> set[str]:
    """훅 스크립트의 `VENDOR_HOSTS=` 한 줄을 리터럴 호스트 집합으로 되돌린다."""
    text = HOOK.read_text(encoding="utf-8")
    m = re.search(r"^VENDOR_HOSTS='([^']*)'", text, re.MULTILINE)
    assert m is not None, "훅에서 VENDOR_HOSTS를 찾지 못했습니다"
    # 최상위 `|`는 대안 구분자이지만 그룹 안의 `|`는 아니다. **그룹을 먼저 편 뒤** 나눈다.
    hosts: set[str] = set()
    for expanded in _expand_alternations(m.group(1)):
        hosts.update(part.replace("\\", "") for part in expanded.split("|"))
    return {h for h in hosts if h}


def test_hook_and_code_denylists_agree() -> None:
    """복제본이 갈라지면 "코드에서는 막히고 명령줄에서는 통과"하는 구멍이 생긴다."""
    expected = set(EXTERNAL_LLM_HOSTS) | set(EXTERNAL_LLM_HOST_SUBSTRINGS)
    assert _hook_denylist() == expected


# ---------------------------------------------------------------------------
# 도구 계층 — 훅을 실제로 돌린다
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash가 없는 환경")
def test_hook_gate_cases_all_pass() -> None:
    """`tests/gate/external_llm_cases.sh` 전량. 차단 케이스와 오탐 대조를 함께 돈다.

    ⚠️ 느리다(이 머신에서 약 40초). 케이스마다 훅 프로세스를 실제로 띄우기 때문이고,
    **읽는 것과 돌려 보는 것은 다르다**는 것이 이 게이트가 존재하는 이유이므로
    모킹하지 않는다.
    """
    # Windows 경로를 bash에 넘기면 역슬래시가 이스케이프로 먹힌다. cwd를 레포 루트로
    # 두고 **POSIX 상대 경로**로만 넘긴다.
    env = dict(os.environ, CLAUDE_PROJECT_DIR=".")
    proc = subprocess.run(
        ["bash", GATE_SCRIPT.relative_to(REPO_ROOT).as_posix()],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
