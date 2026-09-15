"""리서치 그래프 end-to-end 실행.

실행 1건 = trace 1개다. 실행이 끝나면 `var/traces/<run_id>.jsonl`에 단계별
입력·출력·토큰·지연이 남는다 (ADR-007).

실행:
    python scripts/run_research.py "리서치 질의"
    python scripts/run_research.py "질의" --max-revisions 2 --top-k 4 --out docs/eval/run.md
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.obs import get_tracer  # noqa: E402
from src.orchestrator import compile_graph, initial_state  # noqa: E402
from src.orchestrator.prompts import PROMPT_VERSION  # noqa: E402
from src.orchestrator.state import LLMCallRecord  # noqa: E402
from src.providers import get_provider  # noqa: E402
from src.providers.config import load_embedding_settings, load_settings  # noqa: E402
from src.tools.retrieval import ChromaRetriever  # noqa: E402


def _print_trace_summary(records: list[LLMCallRecord]) -> None:
    """단계별 호출 수·토큰·지연 요약.

    '어느 단계에서 문제가 생겼는가'(problem-statement.md §3 목표 3)를 터미널에서
    바로 볼 수 있게 한다. 같은 내용이 JSONL에도 남는다.
    """
    if not records:
        print("  (모델 호출 기록 없음)")
        return

    print(f"  {'노드':<12} {'호출':>4} {'입력토큰':>9} {'출력토큰':>9} {'지연합(s)':>10}")
    print(f"  {'-' * 12} {'-' * 4} {'-' * 9} {'-' * 9} {'-' * 10}")

    by_node: dict[str, list[LLMCallRecord]] = {}
    for record in records:
        by_node.setdefault(record.node, []).append(record)

    for node in ("outliner", "researcher", "verifier", "writer"):
        node_records = by_node.get(node)
        if not node_records:
            continue
        print(
            f"  {node:<12} {len(node_records):>4} "
            f"{sum(r.prompt_tokens for r in node_records):>9} "
            f"{sum(r.completion_tokens for r in node_records):>9} "
            f"{sum(r.latency_s for r in node_records):>10.2f}"
        )

    print(f"  {'-' * 12} {'-' * 4} {'-' * 9} {'-' * 9} {'-' * 10}")
    print(
        f"  {'합계':<12} {len(records):>4} "
        f"{sum(r.prompt_tokens for r in records):>9} "
        f"{sum(r.completion_tokens for r in records):>9} "
        f"{sum(r.latency_s for r in records):>10.2f}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="리서치 그래프 end-to-end 실행")
    parser.add_argument("query", help="리서치 질의")
    parser.add_argument("--max-revisions", type=int, default=2, help="재검색 루프 상한")
    parser.add_argument("--top-k", type=int, default=4, help="조사 항목당 검색 후보 수")
    parser.add_argument("--out", type=Path, default=None, help="초안을 저장할 파일 경로")
    args = parser.parse_args()

    llm_settings = load_settings()
    embedding_settings = load_embedding_settings()

    print("=" * 72)
    print("리서치 실행")
    print("=" * 72)
    # redacted()를 쓴다 — 엔드포인트 URL은 시크릿이다 (docs/governance.md).
    print("LLM        :", llm_settings.redacted())
    print("임베딩     :", embedding_settings.model_name)
    print("프롬프트   :", PROMPT_VERSION)
    print("질의       :", args.query)
    print()

    retriever = ChromaRetriever()
    print("인덱스 문서 수:", retriever.count())

    tracer = get_tracer()
    trace = tracer.trace(
        "research_run",
        input={"query": args.query},
        metadata={
            "prompt_version": PROMPT_VERSION,
            "model": llm_settings.model,
            "embedding_model": embedding_settings.model_name,
            "max_revisions": args.max_revisions,
            "top_k": args.top_k,
        },
    )
    print("run_id        :", trace.run_id)
    print()

    app = compile_graph(
        get_provider(),
        retriever=retriever,
        trace=trace,
        top_k=args.top_k,
    )

    started = time.perf_counter()
    # 재검색 사이클이 있어 LangGraph 기본 재귀 상한(25)에 걸릴 수 있다.
    # 루프 상한은 max_revisions가 담당하므로 여유를 준다.
    result = app.invoke(
        initial_state(args.query, max_revisions=args.max_revisions),
        config={"recursion_limit": 50},
    )
    elapsed = time.perf_counter() - started

    outline = result.get("outline") or []
    uncovered = result.get("uncovered") or []
    records = result.get("trace") or []

    trace.end(
        output={"draft": result.get("draft", "")},
        metadata={
            "outline_size": len(outline),
            "uncovered": uncovered,
            "revisions": result.get("revision", 0),
            "wall_clock_s": round(elapsed, 2),
            "total_tokens": sum(r.prompt_tokens + r.completion_tokens for r in records),
        },
    )
    tracer.flush()

    print("-" * 72)
    print("결과")
    print("-" * 72)
    print(f"조사 항목 ({len(outline)}개):")
    for topic in outline:
        mark = "근거없음" if topic in uncovered else "근거있음"
        print(f"  [{mark}] {topic}")
    print(f"\n재검색 횟수: {result.get('revision', 0)} (상한 {args.max_revisions})")
    print(f"총 소요     : {elapsed:.2f}s")
    print()
    print("단계별 계측:")
    _print_trace_summary(records)

    draft = result.get("draft", "")
    print()
    print("-" * 72)
    print("초안")
    print("-" * 72)
    print(draft)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(draft + "\n", encoding="utf-8")
        print(f"\n초안 저장: {args.out}")

    print(f"\n계측 로그: var/traces/{trace.run_id}.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
