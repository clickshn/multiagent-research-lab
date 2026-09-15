"""계측 파사드 검증 (ADR-007).

감사 로그 요구가 "조건부가 아니다"라는 점이 이 테스트의 핵심이다 — Langfuse가
없어도 기록이 남아야 하고, 계측이 실패해도 본 작업이 죽으면 안 된다.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.obs.tracer import CompositeTracer, LocalJsonlTracer, NullTracer


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_local_tracer_writes_one_file_per_run(tmp_path: Path) -> None:
    tracer = LocalJsonlTracer(tmp_path)

    trace = tracer.trace("research_run", input={"query": "질의"})
    span = trace.span("outliner_call", kind="generation", input="프롬프트")
    span.end(
        output="응답",
        metadata={"tokens": 15, "node": "outliner"},
        usage={"input": 10, "output": 5},
        model="gemma-4-31B-it",
    )
    trace.end(output={"draft": "초안"})

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    assert files[0].stem == trace.run_id

    events = _events(files[0])
    assert [e["type"] for e in events] == ["trace", "generation", "trace_end"]

    generation = events[1]
    # 토큰·지연·모델이 한 이벤트에 함께 남아야 단계별 비교가 된다.
    assert generation["usage"] == {"input": 10, "output": 5}
    assert generation["model"] == "gemma-4-31B-it"
    assert generation["metadata"]["tokens"] == 15
    assert generation["latency_s"] >= 0


def test_span_end_is_idempotent(tmp_path: Path) -> None:
    """두 번 닫아도 기록은 한 번만 — 중복 집계를 막는다."""
    tracer = LocalJsonlTracer(tmp_path)
    trace = tracer.trace("run")
    span = trace.span("call")
    span.end(output="a")
    span.end(output="b")

    events = _events(next(iter(tmp_path.glob("*.jsonl"))))
    assert sum(1 for e in events if e["type"] == "span") == 1


def test_long_values_are_truncated(tmp_path: Path) -> None:
    """원문 전체를 삼키면 로그 파일이 금세 커지고 열어보기 어려워진다."""
    tracer = LocalJsonlTracer(tmp_path)
    trace = tracer.trace("run")
    trace.span("call").end(output="가" * 10_000)

    events = _events(next(iter(tmp_path.glob("*.jsonl"))))
    output = next(e["output"] for e in events if e["type"] == "span")
    assert len(output) < 10_000
    assert "chars>" in output


def test_non_serializable_values_do_not_break_logging(tmp_path: Path) -> None:
    class Opaque:
        pass

    tracer = LocalJsonlTracer(tmp_path)
    trace = tracer.trace("run", input={"obj": Opaque()})
    trace.span("call").end(output=Opaque())
    trace.end()

    # 예외 없이 파일이 쓰였고 JSON으로 다시 읽힌다.
    assert len(_events(next(iter(tmp_path.glob("*.jsonl"))))) == 3


def test_null_tracer_records_nothing_but_keeps_contract() -> None:
    tracer = NullTracer()
    trace = tracer.trace("run")
    trace.span("call").end(output="x", metadata={"tokens": 1})
    trace.end()
    tracer.flush()
    assert isinstance(trace.run_id, str) and trace.run_id


class _BrokenTracer:
    """계측 백엔드가 고장 난 상황을 흉내 낸다."""

    def trace(self, name, *, input=None, metadata=None):
        raise RuntimeError("backend down")

    def flush(self) -> None:
        raise RuntimeError("backend down")


def test_composite_uses_first_trace_run_id(tmp_path: Path) -> None:
    """run_id는 로컬 파일 이름과 같아야 로그를 찾을 수 있다."""
    local = LocalJsonlTracer(tmp_path)
    composite = CompositeTracer([local, NullTracer()])

    trace = composite.trace("run")
    trace.span("call").end(output="x")
    trace.end()

    assert (tmp_path / f"{trace.run_id}.jsonl").exists()
