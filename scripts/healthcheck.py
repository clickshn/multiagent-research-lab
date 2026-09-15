"""엔드포인트 헬스체크 — 프로바이더 계층을 그대로 태워서 확인한다.

curl로 직접 치지 않고 `src.providers`를 경유하는 이유: 엔드포인트가 살아 있는지와
우리 추상화 계층이 그 엔드포인트를 제대로 호출하는지를 한 번에 본다.

사용: python scripts/healthcheck.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.providers import ChatMessage, ConfigError, get_provider, LLMError, load_settings


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"[FAIL] 설정 오류: {exc}")
        return 2

    print(f"[INFO] 대상: {settings.redacted()}")

    provider = get_provider(settings)
    messages = [ChatMessage(role="user", content="Reply with exactly: OK")]

    try:
        response = provider.complete(messages, temperature=0.0, max_tokens=16)
    except LLMError as exc:
        print(f"[FAIL] {exc}")
        return 1

    print(
        f"[OK] model={response.model} "
        f"latency={response.latency_s:.3f}s "
        f"tokens={response.prompt_tokens}+{response.completion_tokens}"
        f"={response.total_tokens} "
        f"finish={response.finish_reason}"
    )
    print(f"[OK] text={response.text.strip()!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
