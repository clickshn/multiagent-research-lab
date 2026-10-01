"""json_schema 강제 판별 — 서버가 `response_format`을 **존중하는가**를 호출로 가린다 (v1.2-T2, ADR-028).

**왜 프롬프트가 "JSON을 쓰지 마라"인가.** 프롬프트가 JSON을 시키면, 스키마대로 나온 출력이
서버의 강제 덕인지 모델이 지시를 따른 덕인지 구별되지 않는다. 생산자 레포 ADR-019가 기록했듯
vLLM은 **모르는 파라미터를 오류 없이 무시할 수 있다** — 그러면 "요청은 성공했고 출력도 그럴듯한데
강제는 걸리지 않은" 상태가 조용히 생긴다. 그래서 프롬프트는 반대 방향(자연어로 답하라)을 시키고,
그래도 스키마대로 나오면 강제가 걸린 것으로 본다.

호출 구성 (최대 3건, 승인 범위):

    control  — 같은 프롬프트, 스키마 없음. 프롬프트가 실제로 JSON을 막는지 확인(판별력의 전제)
    enforce1 — 스키마 있음. 어휘 안 주제(합성)
    enforce2 — 스키마 있음. 어휘 밖 주제(합성) — "없음"도 enum 안에서 나오는가

입력은 전부 합성 문장이다. 골든셋을 쓰지 않는다. 캐시는 끈다.

    python scripts/probe_schema_enforcement.py --out docs/eval/t2-schema-enforcement.json

종료 코드: 0 = 강제 확인 · 1 = 강제 실패(스키마 위반 출력) · 2 = 판별 불가(control도 JSON)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.orchestrator.nodes import (  # noqa: E402
    TECH_DOMAIN_ABSTAIN,
    _extract_json,
    tech_domain_schema,
)
from src.providers import ChatMessage, get_provider  # noqa: E402
from src.providers.config import load_settings  # noqa: E402
from src.tools.retrieval import ChromaRetriever  # noqa: E402

SYSTEM = (
    "너는 분류 보조다. 주어진 조사 항목이 다음 기술 영역 중 어디에 속하는지 판단한다.\n"
    "영역: {vocab}\n"
    "어느 것에도 맞지 않으면 '{abstain}'.\n\n"
    "**JSON을 쓰지 마라.** 중괄호·따옴표·코드펜스 없이, 이유를 포함한 자연어 한두 문장으로만 답한다."
)
CASES = [
    ("control", "여러 LLM 에이전트가 역할을 나눠 협업하는 프로토콜 설계", False),
    ("enforce1", "여러 LLM 에이전트가 역할을 나눠 협업하는 프로토콜 설계", True),
    ("enforce2", "도심 교차로 신호 주기를 바꿨을 때의 보행자 대기 시간", True),
]


def _judge(text: str, enum: list[str]) -> dict:
    parsed = None
    try:
        parsed = json.loads(text)
        strict_json = True
    except json.JSONDecodeError:
        strict_json = False
        parsed = _extract_json(text)
    schema_ok = (
        isinstance(parsed, dict)
        and set(parsed) == {"tech_domain"}
        and parsed.get("tech_domain") in enum
    )
    return {
        "strict_json": strict_json,
        "contains_json": parsed is not None,
        "schema_ok": bool(schema_ok and strict_json),
        "value": parsed.get("tech_domain") if isinstance(parsed, dict) else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    settings = load_settings()
    vocab = sorted(ChromaRetriever(embeddings=object()).tech_domain_vocab())
    schema = tech_domain_schema(vocab)
    enum = schema["json_schema"]["schema"]["properties"]["tech_domain"]["enum"]
    provider = get_provider(settings, cache=False)
    system = SYSTEM.format(vocab=", ".join(vocab), abstain=TECH_DOMAIN_ABSTAIN)

    print(f"endpoint: {settings.redacted()}")
    results = []
    for label, topic, with_schema in CASES:
        messages = [ChatMessage("system", system), ChatMessage("user", f"조사 항목: {topic}")]
        kwargs = {"response_format": schema} if with_schema else {}
        response = provider.complete(messages, temperature=0.0, max_tokens=128, **kwargs)
        verdict = _judge(response.text, enum)
        results.append({
            "label": label,
            "topic": topic,
            "response_format": "json_schema" if with_schema else None,
            "raw_text": response.text,
            "finish_reason": response.finish_reason,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "latency_s": round(response.latency_s, 3),
            **verdict,
        })
        print(f"[{label}] schema_ok={verdict['schema_ok']} strict_json={verdict['strict_json']} "
              f"value={verdict['value']!r} raw={response.text[:120]!r}")

    control = results[0]
    enforced = [r for r in results if r["response_format"]]
    if control["contains_json"]:
        status, code = "판별 불가 — 스키마 없이도 JSON이 나왔다", 2
    elif all(r["schema_ok"] for r in enforced):
        status, code = "강제 확인", 0
    else:
        status, code = "강제 실패", 1

    out = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "endpoint_fp": settings.redacted().get("endpoint_fp"),
        "model": settings.model,
        "parameter": "response_format={type: json_schema, json_schema: {name, strict, schema}}",
        "cache_enabled": False,
        "llm_calls": len(results),
        "enum": enum,
        "status": status,
        "results": results,
    }
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(status)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
