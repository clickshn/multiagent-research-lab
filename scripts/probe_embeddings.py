"""서빙 엔드포인트가 임베딩을 제공하는지 확인한다 (재현 가능한 실측).

ADR-005의 근거가 되는 확인이다. 엔드포인트가 나중에 `/v1/embeddings`를 지원하게
되면 이 스크립트가 먼저 알려주고, 그때 ADR-005의 Review Trigger가 발동한다.

실행: `python scripts/probe_embeddings.py` (레포 루트에서)

출력에 엔드포인트 URL 전체를 찍지 않는다 — URL이 곧 접근 권한이다
(`docs/governance.md` "시크릿 취급").
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.providers.config import load_settings  # noqa: E402


def _request(url: str, api_key: str, payload: dict | None = None) -> tuple[int | None, str, float]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode(), time.perf_counter() - started
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:400], time.perf_counter() - started
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}", time.perf_counter() - started


def main() -> int:
    settings = load_settings()
    print("대상:", json.dumps(settings.redacted(), ensure_ascii=False))

    status, body, latency = _request(f"{settings.base_url}/models", settings.api_key)
    print(f"\n[GET /v1/models] status={status} ({latency:.2f}s)")
    if status == 200:
        for model in json.loads(body).get("data", []):
            print(f"  - {model.get('id')} (max_model_len={model.get('max_model_len')})")

    status, body, latency = _request(
        f"{settings.base_url}/embeddings",
        settings.api_key,
        {"model": settings.model, "input": "임베딩 지원 여부 확인"},
    )
    print(f"\n[POST /v1/embeddings] status={status} ({latency:.2f}s)")
    print(f"  {body[:200]}")

    if status == 200:
        print("\n결론: 엔드포인트가 임베딩을 제공한다. ADR-005 Review Trigger 발동 대상.")
        return 0

    print(
        "\n결론: 엔드포인트는 임베딩을 제공하지 않는다 (생성 전용).\n"
        "      → 로컬 sentence-transformers로 임베딩한다 (ADR-005)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
