"""계측 계층 (ADR-007).

노드는 `Tracer` 파사드만 보고, 실제 기록이 Langfuse로 가는지 로컬 JSONL로 가는지
모른다 — LLM·임베딩 프로바이더와 같은 원칙이다.

파사드를 한 겹 두는 이유는 두 가지다.

1. **감사 로그는 조건부가 아니다.** 어떤 질의에 어떤 프롬프트로 무엇을 답했는지는
   항상 남아야 한다 (problem-statement.md §2). Langfuse 서버가 없다고 계측이
   꺼지면 안 되므로, 로컬 JSONL 기록이 기본이고 Langfuse는 그 위에 얹힌다.
2. **Langfuse SDK의 API가 메이저 버전마다 바뀐다.** v2의 `langfuse.trace()`는
   v4에서 `start_observation()`으로 바뀌었다. 노드 코드가 SDK 시그니처에 직접
   묶이면 SDK 업그레이드가 노드 수정이 된다.
"""

from .tracer import (
    CompositeTracer,
    LangfuseTracer,
    LocalJsonlTracer,
    NullTracer,
    RunTrace,
    Span,
    Tracer,
    get_tracer,
)

__all__ = [
    "CompositeTracer",
    "get_tracer",
    "LangfuseTracer",
    "LocalJsonlTracer",
    "NullTracer",
    "RunTrace",
    "Span",
    "Tracer",
]
