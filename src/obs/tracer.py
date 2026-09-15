"""Tracer 파사드와 백엔드 구현 (ADR-007).

사용 형태 — 실행 1건당 trace 하나, 노드 호출마다 span 하나:

    trace = tracer.trace(name="research_run", input={"query": q})
    span = trace.span(name="outliner_call", kind="generation")
    ...
    span.end(output=result, metadata={"tokens": response.total_tokens})
    trace.end(output={"draft": draft})

**왜 실행 1건 = trace 1개인가.** 노드마다 별도 trace를 만들면 한 질의의 여러 호출이
서로 무관한 기록으로 흩어져, "어느 단계에서 품질이 떨어졌는가"(problem-statement.md
§3 목표 3)를 답하기 어려워진다. 실행을 trace로 묶고 단계를 span으로 두면 단계별
지연·토큰이 한 트리 안에서 비교된다.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from src.providers.config import TracingSettings, load_tracing_settings

SpanKind = Literal["span", "generation"]


# ---------------------------------------------------------------------------
# 파사드 프로토콜
# ---------------------------------------------------------------------------


@runtime_checkable
class Span(Protocol):
    def end(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
        model: str | None = None,
    ) -> None: ...


@runtime_checkable
class RunTrace(Protocol):
    @property
    def run_id(self) -> str: ...

    def span(self, name: str, *, kind: SpanKind = "span", input: Any = None) -> Span: ...

    def end(self, *, output: Any = None, metadata: dict[str, Any] | None = None) -> None: ...


@runtime_checkable
class Tracer(Protocol):
    def trace(
        self, name: str, *, input: Any = None, metadata: dict[str, Any] | None = None
    ) -> RunTrace: ...

    def flush(self) -> None: ...


# ---------------------------------------------------------------------------
# No-op — 테스트에서 계측을 끄고 노드 로직만 보고 싶을 때
# ---------------------------------------------------------------------------


class _NullSpan:
    def end(self, **_: Any) -> None:
        return None


class _NullTrace:
    def __init__(self, run_id: str) -> None:
        self._run_id = run_id

    @property
    def run_id(self) -> str:
        return self._run_id

    def span(self, name: str, *, kind: SpanKind = "span", input: Any = None) -> Span:
        return _NullSpan()

    def end(self, **_: Any) -> None:
        return None


class NullTracer:
    """아무것도 기록하지 않는다. 단위 테스트 기본값."""

    def trace(
        self, name: str, *, input: Any = None, metadata: dict[str, Any] | None = None
    ) -> RunTrace:
        return _NullTrace(uuid.uuid4().hex[:12])

    def flush(self) -> None:
        return None


# ---------------------------------------------------------------------------
# 로컬 JSONL — 항상 켜져 있는 감사 로그
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _safe(value: Any, *, limit: int = 4000) -> Any:
    """JSON으로 직렬화 가능한 형태로 좁히고, 너무 길면 자른다.

    로그가 원문 전체를 그대로 삼키면 파일이 금세 커지고 열어보기 어려워진다.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"...<{len(value)}chars>"
    if isinstance(value, dict):
        return {str(k): _safe(v, limit=limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v, limit=limit) for v in value]
    return _safe(repr(value), limit=limit)


class _JsonlSpan:
    def __init__(self, trace: "_JsonlTrace", name: str, kind: SpanKind, input: Any) -> None:
        self._trace = trace
        self._name = name
        self._kind = kind
        self._input = input
        self._started = time.perf_counter()
        self._start_ts = _utc_now()
        self._ended = False

    def end(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
        model: str | None = None,
    ) -> None:
        if self._ended:  # 두 번 닫아도 기록은 한 번만
            return
        self._ended = True
        self._trace._write(
            {
                "type": self._kind,
                "run_id": self._trace.run_id,
                "name": self._name,
                "started_at": self._start_ts,
                "ended_at": _utc_now(),
                "latency_s": round(time.perf_counter() - self._started, 4),
                "model": model,
                "usage": usage,
                "input": _safe(self._input),
                "output": _safe(output),
                "metadata": _safe(metadata),
            }
        )


class _JsonlTrace:
    def __init__(
        self, path: Path, run_id: str, name: str, input: Any, metadata: dict | None
    ) -> None:
        self._path = path
        self._run_id = run_id
        self._started = time.perf_counter()
        self._write(
            {
                "type": "trace",
                "run_id": run_id,
                "name": name,
                "started_at": _utc_now(),
                "input": _safe(input),
                "metadata": _safe(metadata),
            }
        )

    @property
    def run_id(self) -> str:
        return self._run_id

    def _write(self, record: dict) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def span(self, name: str, *, kind: SpanKind = "span", input: Any = None) -> Span:
        return _JsonlSpan(self, name, kind, input)

    def end(self, *, output: Any = None, metadata: dict[str, Any] | None = None) -> None:
        self._write(
            {
                "type": "trace_end",
                "run_id": self._run_id,
                "ended_at": _utc_now(),
                "latency_s": round(time.perf_counter() - self._started, 4),
                "output": _safe(output),
                "metadata": _safe(metadata),
            }
        )


class LocalJsonlTracer:
    """실행 1건 = 파일 1개(JSONL). 추가 인프라 없이 감사 로그 요구를 충족한다.

    JSONL을 고른 이유: 한 줄이 한 이벤트라 실행 중에도 tail로 볼 수 있고, 파싱에
    라이브러리가 필요 없다. 나중에 Langfuse로 재적재할 때도 그대로 읽으면 된다.
    """

    def __init__(self, trace_dir: Path) -> None:
        self._dir = trace_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def trace(
        self, name: str, *, input: Any = None, metadata: dict[str, Any] | None = None
    ) -> RunTrace:
        run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        path = self._dir / f"{run_id}.jsonl"
        return _JsonlTrace(path, run_id, name, input, metadata)

    def flush(self) -> None:
        return None  # 매 이벤트를 즉시 쓴다


# ---------------------------------------------------------------------------
# Langfuse — 서버가 구성돼 있을 때만
# ---------------------------------------------------------------------------


class _LangfuseSpan:
    def __init__(self, observation: Any) -> None:
        self._obs = observation

    def end(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
        model: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {}
        if output is not None:
            payload["output"] = _safe(output)
        if metadata is not None:
            payload["metadata"] = _safe(metadata)
        if usage is not None:
            payload["usage_details"] = usage
        if model is not None:
            payload["model"] = model
        try:
            if payload:
                self._obs.update(**payload)
            self._obs.end()
        except Exception:  # noqa: BLE001 - 계측 실패가 파이프라인을 죽이면 안 된다
            pass


class _LangfuseTrace:
    def __init__(self, root: Any, run_id: str) -> None:
        self._root = root
        self._run_id = run_id

    @property
    def run_id(self) -> str:
        return self._run_id

    def span(self, name: str, *, kind: SpanKind = "span", input: Any = None) -> Span:
        try:
            obs = self._root.start_observation(name=name, as_type=kind, input=_safe(input))
            return _LangfuseSpan(obs)
        except Exception:  # noqa: BLE001
            return _NullSpan()

    def end(self, *, output: Any = None, metadata: dict[str, Any] | None = None) -> None:
        try:
            payload: dict[str, Any] = {}
            if output is not None:
                payload["output"] = _safe(output)
            if metadata is not None:
                payload["metadata"] = _safe(metadata)
            if payload:
                self._root.update(**payload)
            self._root.end()
        except Exception:  # noqa: BLE001
            pass


class LangfuseTracer:
    """Langfuse(self-host)로 보내는 백엔드.

    계측 실패가 리서치 실행을 실패시키지 않도록 모든 호출을 감싼다 — 관측은
    본 작업의 부수 효과지 전제 조건이 아니다.
    """

    def __init__(self, settings: TracingSettings) -> None:
        from langfuse import Langfuse

        self._client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )

    def trace(
        self, name: str, *, input: Any = None, metadata: dict[str, Any] | None = None
    ) -> RunTrace:
        root = self._client.start_observation(
            name=name, as_type="span", input=_safe(input), metadata=_safe(metadata)
        )
        run_id = ""
        try:
            run_id = self._client.get_current_trace_id() or ""
        except Exception:  # noqa: BLE001
            pass
        return _LangfuseTrace(root, run_id or uuid.uuid4().hex[:12])

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 합성 — 로컬은 항상, Langfuse는 구성돼 있으면
# ---------------------------------------------------------------------------


class _CompositeSpan:
    def __init__(self, spans: Sequence[Span]) -> None:
        self._spans = spans

    def end(self, **kwargs: Any) -> None:
        for span in self._spans:
            span.end(**kwargs)


class _CompositeTrace:
    def __init__(self, traces: Sequence[RunTrace]) -> None:
        self._traces = traces

    @property
    def run_id(self) -> str:
        # 로컬 trace가 첫 번째다. 파일 이름과 run_id가 같아야 로그를 찾을 수 있다.
        return self._traces[0].run_id

    def span(self, name: str, *, kind: SpanKind = "span", input: Any = None) -> Span:
        return _CompositeSpan([t.span(name, kind=kind, input=input) for t in self._traces])

    def end(self, **kwargs: Any) -> None:
        for trace in self._traces:
            trace.end(**kwargs)


class CompositeTracer:
    """여러 백엔드에 동시에 기록한다."""

    def __init__(self, tracers: Sequence[Tracer]) -> None:
        self._tracers = list(tracers)

    def trace(
        self, name: str, *, input: Any = None, metadata: dict[str, Any] | None = None
    ) -> RunTrace:
        return _CompositeTrace(
            [t.trace(name, input=input, metadata=metadata) for t in self._tracers]
        )

    def flush(self) -> None:
        for tracer in self._tracers:
            tracer.flush()


def get_tracer(settings: TracingSettings | None = None) -> Tracer:
    """기본 tracer.

    로컬 JSONL은 항상 켠다(감사 로그). Langfuse는 `.env`에 host·키가 모두 있을 때만
    추가로 붙는다 — 외부 SaaS 관측 도구로 기본 전송되는 일이 없게 하기 위함이다
    (problem-statement.md §2).
    """
    settings = settings or load_tracing_settings()
    if os.getenv("DISABLE_TRACING", "").strip().lower() in {"1", "true", "yes"}:
        return NullTracer()

    tracers: list[Tracer] = [LocalJsonlTracer(settings.local_trace_dir)]
    if settings.langfuse_enabled:
        try:
            tracers.append(LangfuseTracer(settings))
        except Exception as exc:  # noqa: BLE001
            # Langfuse를 못 붙여도 로컬 기록은 계속된다. 다만 조용히 넘어가지 않는다.
            print(f"[obs] Langfuse 연결 실패, 로컬 JSONL만 사용합니다: {exc}")
    return CompositeTracer(tracers) if len(tracers) > 1 else tracers[0]
