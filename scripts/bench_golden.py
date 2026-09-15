"""골든셋 전체를 한 프로세스에서 돌려 지연·토큰·근거 커버리지를 측정한다.

**왜 별도 스크립트인가.** `run_research.py`는 질의 1건마다 프로세스를 새로 띄운다.
그 방식에서는 임베딩 모델 첫 로딩(CPU, 수십 초)이 매 실행의 벽시계에 섞여 들어가
"파이프라인이 느리다"와 "모델을 매번 새로 읽는다"를 구분할 수 없다 (session-03 관찰:
벽시계 33.93s 중 LLM은 5.76s뿐). 이 스크립트는

1. 임베딩 모델을 **먼저 한 번 워밍업**해 상주시키고 그 시간을 따로 보고한 뒤,
2. 같은 프로세스에서 골든셋 N건을 연속 실행한다.

그래서 여기 나오는 "파이프라인 지연"은 상주 서비스로 배포했을 때의 지연에 해당한다.
콜드 로딩 시간은 측정값이 아니라 **기동 1회 비용**으로 따로 적힌다.

**세 가지 지연을 나눠 적는다.**

- `llm_latency_s` — 모델 호출 지연의 합. 캐시 적중은 0에 가깝다.
- `retrieval_latency_s` — 검색(임베딩 인코딩 + Chroma 질의) 지연의 합. 모델이
  워밍업된 뒤의 값이므로 로딩 시간이 섞이지 않는다.
- `wall_clock_s` — 파이프라인 전체. 위 둘에 파싱·그래프 오버헤드가 더해진 값이다.

실행:
    python scripts/bench_golden.py --label no-cache
    python scripts/bench_golden.py --label cached --cache
    python scripts/bench_golden.py --cache --clear-cache   # 캐시를 비우고 시작
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.obs import get_tracer  # noqa: E402
from src.orchestrator import compile_graph, initial_state  # noqa: E402
from src.orchestrator.prompts import PROMPT_VERSION  # noqa: E402
from src.providers import get_provider  # noqa: E402
from src.providers.cache import clear_cache  # noqa: E402
from src.providers.config import (  # noqa: E402
    load_cache_settings,
    load_embedding_settings,
    load_settings,
    load_tracing_settings,
)
from src.providers.embeddings import get_embedding_provider  # noqa: E402
from src.tools.retrieval import ChromaRetriever  # noqa: E402

GOLDEN_SET = REPO_ROOT / "docs" / "eval" / "golden-set.json"
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "eval"


def _percentile(values: list[float], pct: float) -> float:
    """가장 가까운 순위(nearest-rank) 백분위.

    표본이 한 자릿수라 보간법을 쓰면 실제로 관측되지 않은 값이 지표가 된다.
    nearest-rank는 항상 **실제 관측값**을 돌려주므로 n이 작을 때 정직하다.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(1, min(len(ordered), int(-(-pct * len(ordered) // 100))))
    return ordered[index - 1]


def _warm_up_embeddings() -> tuple[object, float, float]:
    """임베딩 모델을 상주시키고 (콜드 로딩 시간, 웜 인코딩 시간)을 잰다.

    이 함수가 이 스크립트의 핵심이다. 여기서 로딩을 끝내두지 않으면 첫 질의의
    벽시계에 로딩 시간이 통째로 들어가 측정이 망가진다.
    """
    embeddings = get_embedding_provider()

    started = time.perf_counter()
    embeddings.embed_query("워밍업")  # 이 호출이 모델을 실제로 읽어 들인다
    cold_s = time.perf_counter() - started

    started = time.perf_counter()
    embeddings.embed_query("워밍업 2회차")
    warm_s = time.perf_counter() - started

    return embeddings, cold_s, warm_s


def _retrieval_latency(trace_path: Path) -> tuple[float, int]:
    """JSONL 트레이스에서 검색 span의 지연 합과 건수를 읽는다.

    검색 지연을 노드가 따로 세지 않고 트레이스에서 되읽는 이유: 계측 로그가
    운영 중에도 같은 질문에 답할 수 있어야 하기 때문이다 (ADR-007). 측정
    스크립트만 아는 별도 경로를 만들면 그 경로는 운영에서 검증되지 않는다.
    """
    total = 0.0
    count = 0
    if not trace_path.exists():
        return 0.0, 0
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("name") == "researcher_retrieve":
            total += float(record.get("latency_s") or 0.0)
            count += 1
    return total, count


def _new_evidence_from_retry(findings) -> dict:
    """재검색 루프가 **새 근거**를 실제로 가져왔는지 판정한다.

    session-03의 열린 질문이다. "재검색을 돌았다"가 아니라 "재검색 회차에서만
    등장한 인용이 있는가"를 본다. 같은 문서를 다시 물어온 것은 새 근거가 아니다.
    """
    first_pass: set[tuple[str, str]] = set()
    retry_only: set[tuple[str, str]] = set()
    topics_gained: set[str] = set()

    for finding in findings:
        if finding.revision == 0:
            first_pass.update((c.doc_id, c.locator) for c in finding.citations)

    for finding in findings:
        if finding.revision == 0:
            continue
        for citation in finding.citations:
            key = (citation.doc_id, citation.locator)
            if key not in first_pass:
                retry_only.add(key)
                topics_gained.add(finding.topic)

    return {
        "retry_citations_total": sum(
            len(f.citations) for f in findings if f.revision > 0
        ),
        "new_citations": len(retry_only),
        "topics_gained": sorted(topics_gained),
    }


def _score(case: dict, result: dict) -> dict:
    """골든셋 기대와 실행 결과를 대조한다.

    자동으로 채점하는 것은 **기계적으로 셀 수 있는 것만**이다 (ADR-006과 같은
    원칙): 근거 없음 여부, 최소 인용 수, 기대 문서 ID 적중. `must_mention`은
    의미 판정이라 자동화하지 않고 사람이 보도록 남긴다 — LLM 심판을 쓰면 판정
    모델이 또 하나의 변수가 되어 모델 고정 원칙(ADR-002)과 충돌한다.
    """
    expect = case.get("expect", {})
    topic_count = len(result["outline"])
    uncovered_count = len(result["uncovered"])
    all_uncovered = topic_count > 0 and uncovered_count == topic_count

    cited_docs = {c.doc_id for f in result["findings"] for c in f.citations}
    expected_docs = set(expect.get("expected_doc_ids") or [])

    checks = {}
    if expect.get("expect_uncovered"):
        # 음성 케이스: 모든 항목이 "근거 없음"으로 끝나야 통과다.
        checks["negative_case_held"] = all_uncovered
    else:
        checks["some_topic_covered"] = uncovered_count < topic_count
        if expected_docs:
            checks["expected_doc_cited"] = bool(expected_docs & cited_docs)

    return {
        "checks": checks,
        "passed": all(checks.values()) if checks else None,
        "topic_count": topic_count,
        "uncovered_count": uncovered_count,
        "cited_doc_ids": sorted(cited_docs),
        "expected_doc_ids": sorted(expected_docs),
        "must_mention_manual": expect.get("must_mention") or [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="골든셋 벤치마크 (지연·토큰·커버리지)")
    parser.add_argument("--label", default="run", help="이 측정 조건의 이름")
    parser.add_argument("--cache", action="store_true", help="LLM 응답 캐시를 켠다")
    parser.add_argument(
        "--clear-cache", action="store_true", help="시작 전에 캐시를 비운다"
    )
    parser.add_argument("--max-revisions", type=int, default=2)
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--only", nargs="*", default=None, help="특정 케이스 ID만")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    golden = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
    cases = golden["cases"]
    if args.only:
        wanted = set(args.only)
        cases = [c for c in cases if c["id"] in wanted]

    llm_settings = load_settings()
    embedding_settings = load_embedding_settings()
    tracing_settings = load_tracing_settings()

    print("=" * 72)
    print(f"골든셋 벤치마크 — 조건: {args.label}")
    print("=" * 72)
    print("LLM        :", llm_settings.redacted())  # URL은 시크릿이다 (governance.md)
    print("임베딩     :", embedding_settings.model_name)
    print("계측       :", tracing_settings.redacted())
    print("프롬프트   :", PROMPT_VERSION)
    print("캐시       :", "ON" if args.cache else "OFF")

    if args.clear_cache:
        removed = clear_cache(load_cache_settings())
        print(f"캐시 비움  : {removed}개 항목 삭제")

    # --- 1단계: 임베딩 워밍업 (측정 대상 아님, 기동 1회 비용) -------------------
    print("\n[1/3] 임베딩 모델 워밍업 중... (콜드 로딩)")
    embeddings, cold_s, warm_s = _warm_up_embeddings()
    print(f"      콜드 로딩 {cold_s:.2f}s -> 웜 인코딩 {warm_s * 1000:.1f}ms")
    print("      이후 측정은 전부 모델이 상주한 상태에서 이뤄진다.")

    # 워밍업한 인스턴스를 그대로 넘긴다. 새로 만들면 로딩을 다시 한다.
    retriever = ChromaRetriever(embeddings=embeddings)
    print(f"[2/3] 인덱스 문서 수: {retriever.count()}")

    provider = get_provider(cache=args.cache)
    tracer = get_tracer()
    rows: list[dict] = []

    print(f"[3/3] 케이스 {len(cases)}건 실행\n")
    bench_started = time.perf_counter()

    for case in cases:
        trace = tracer.trace(
            "research_run",
            input={"query": case["query"]},
            metadata={
                "bench_label": args.label,
                "case_id": case["id"],
                "prompt_version": PROMPT_VERSION,
                "model": llm_settings.model,
                "embedding_model": embedding_settings.model_name,
                "cache": args.cache,
                "max_revisions": args.max_revisions,
                "top_k": args.top_k,
            },
        )
        app = compile_graph(
            provider, retriever=retriever, trace=trace, top_k=args.top_k
        )

        started = time.perf_counter()
        state = app.invoke(
            initial_state(case["query"], max_revisions=args.max_revisions),
            config={"recursion_limit": 50},
        )
        wall_s = time.perf_counter() - started

        records = state.get("trace") or []
        findings = state.get("findings") or []
        result = {
            "outline": state.get("outline") or [],
            "uncovered": state.get("uncovered") or [],
            "findings": findings,
        }

        llm_latency = sum(r.latency_s for r in records)
        by_node: dict[str, dict] = defaultdict(
            lambda: {"calls": 0, "prompt": 0, "completion": 0, "latency_s": 0.0, "cached": 0}
        )
        for record in records:
            bucket = by_node[record.node]
            bucket["calls"] += 1
            bucket["prompt"] += record.prompt_tokens
            bucket["completion"] += record.completion_tokens
            bucket["latency_s"] = round(bucket["latency_s"] + record.latency_s, 4)
            bucket["cached"] += int(record.cached)

        billed = sum(
            r.prompt_tokens + r.completion_tokens for r in records if not r.cached
        )

        trace.end(
            output={"draft": state.get("draft", "")},
            metadata={
                "bench_label": args.label,
                "wall_clock_s": round(wall_s, 3),
                "llm_latency_s": round(llm_latency, 3),
                "uncovered": result["uncovered"],
                "revisions": state.get("revision", 0),
            },
        )
        tracer.flush()

        retrieval_s, retrieval_calls = _retrieval_latency(
            tracing_settings.local_trace_dir / f"{trace.run_id}.jsonl"
        )

        row = {
            "case_id": case["id"],
            "query": case["query"],
            "run_id": trace.run_id,
            "wall_clock_s": round(wall_s, 3),
            "llm_latency_s": round(llm_latency, 3),
            "retrieval_latency_s": round(retrieval_s, 3),
            # 위 셋 중 어디에도 안 잡히는 시간 (JSON 파싱, 그래프 오버헤드 등).
            "other_latency_s": round(max(0.0, wall_s - llm_latency - retrieval_s), 3),
            "llm_calls": len(records),
            "cache_hits": sum(1 for r in records if r.cached),
            "retrieval_calls": retrieval_calls,
            "prompt_tokens": sum(r.prompt_tokens for r in records),
            "completion_tokens": sum(r.completion_tokens for r in records),
            "billed_tokens": billed,
            "revisions": state.get("revision", 0),
            "by_node": {k: dict(v) for k, v in by_node.items()},
            "retry_evidence": _new_evidence_from_retry(findings),
            "score": _score(case, result),
            "draft_chars": len(state.get("draft", "")),
        }
        rows.append(row)

        verdict = row["score"]["passed"]
        mark = "PASS" if verdict else ("FAIL" if verdict is False else "----")
        print(
            f"  [{mark}] {case['id']}  "
            f"wall {row['wall_clock_s']:>6.2f}s | llm {row['llm_latency_s']:>6.2f}s | "
            f"retr {row['retrieval_latency_s']:>5.2f}s | "
            f"calls {row['llm_calls']:>2} (hit {row['cache_hits']:>2}) | "
            f"tok {row['prompt_tokens'] + row['completion_tokens']:>6} "
            f"(billed {row['billed_tokens']:>6}) | "
            f"새근거 {row['retry_evidence']['new_citations']}"
        )

    bench_s = time.perf_counter() - bench_started

    walls = [r["wall_clock_s"] for r in rows]
    llms = [r["llm_latency_s"] for r in rows]
    retrs = [r["retrieval_latency_s"] for r in rows]
    per_call = [
        r["llm_latency_s"] / r["llm_calls"] for r in rows if r["llm_calls"]
    ]

    summary = {
        "label": args.label,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "n_cases": len(rows),
        "cache_enabled": args.cache,
        "prompt_version": PROMPT_VERSION,
        "model": llm_settings.model,
        "embedding_model": embedding_settings.model_name,
        "corpus_docs": retriever.count(),
        "max_revisions": args.max_revisions,
        "top_k": args.top_k,
        "embedding_warmup": {
            "cold_load_s": round(cold_s, 3),
            "warm_encode_ms": round(warm_s * 1000, 2),
            "note": (
                "콜드 로딩은 프로세스 기동 1회 비용이며 아래 지연 수치에 포함되지 "
                "않는다. 상주 프로세스에서는 발생하지 않는다."
            ),
        },
        "latency": {
            "llm_mean_s": round(statistics.fmean(llms), 3) if llms else 0.0,
            "llm_median_s": round(statistics.median(llms), 3) if llms else 0.0,
            "llm_p95_s": round(_percentile(llms, 95), 3),
            "llm_max_s": round(max(llms), 3) if llms else 0.0,
            "llm_per_call_mean_s": round(statistics.fmean(per_call), 3) if per_call else 0.0,
            "retrieval_mean_s": round(statistics.fmean(retrs), 3) if retrs else 0.0,
            "pipeline_mean_s": round(statistics.fmean(walls), 3) if walls else 0.0,
            "pipeline_median_s": round(statistics.median(walls), 3) if walls else 0.0,
            "pipeline_p95_s": round(_percentile(walls, 95), 3),
            "pipeline_max_s": round(max(walls), 3) if walls else 0.0,
            "percentile_method": "nearest-rank (n이 작아 보간하지 않는다)",
        },
        "tokens": {
            "prompt_total": sum(r["prompt_tokens"] for r in rows),
            "completion_total": sum(r["completion_tokens"] for r in rows),
            "billed_total": sum(r["billed_tokens"] for r in rows),
            "mean_per_request": round(
                statistics.fmean(
                    [r["prompt_tokens"] + r["completion_tokens"] for r in rows]
                ),
                1,
            )
            if rows
            else 0.0,
            "billed_mean_per_request": round(
                statistics.fmean([r["billed_tokens"] for r in rows]), 1
            )
            if rows
            else 0.0,
        },
        "calls": {
            "total": sum(r["llm_calls"] for r in rows),
            "cache_hits": sum(r["cache_hits"] for r in rows),
            "mean_per_request": round(
                statistics.fmean([r["llm_calls"] for r in rows]), 2
            )
            if rows
            else 0.0,
        },
        "retry_loop": {
            "runs_with_retry": sum(1 for r in rows if r["revisions"] > 1),
            "retry_citations_total": sum(
                r["retry_evidence"]["retry_citations_total"] for r in rows
            ),
            "new_citations_total": sum(
                r["retry_evidence"]["new_citations"] for r in rows
            ),
        },
        "scoring": {
            "passed": sum(1 for r in rows if r["score"]["passed"] is True),
            "failed": sum(1 for r in rows if r["score"]["passed"] is False),
            "unscored": sum(1 for r in rows if r["score"]["passed"] is None),
        },
        "bench_wall_clock_s": round(bench_s, 2),
        "rows": rows,
    }

    out_path = args.out_dir / f"bench-{args.label}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lat = summary["latency"]
    print("\n" + "-" * 72)
    print(f"요약 — 조건 {args.label} (n={len(rows)})")
    print("-" * 72)
    print(f"  임베딩 콜드 로딩   : {cold_s:.2f}s (기동 1회, 아래 수치에 미포함)")
    print(f"  LLM 호출 지연      : 평균 {lat['llm_mean_s']:.2f}s / p95 {lat['llm_p95_s']:.2f}s")
    print(f"  검색 지연          : 평균 {lat['retrieval_mean_s']:.2f}s")
    print(
        f"  파이프라인 지연    : 평균 {lat['pipeline_mean_s']:.2f}s / "
        f"p95 {lat['pipeline_p95_s']:.2f}s"
    )
    print(
        f"  토큰/요청          : 평균 {summary['tokens']['mean_per_request']:.0f} "
        f"(청구 {summary['tokens']['billed_mean_per_request']:.0f})"
    )
    print(
        f"  호출/요청          : 평균 {summary['calls']['mean_per_request']:.2f} "
        f"(캐시 적중 {summary['calls']['cache_hits']})"
    )
    print(
        f"  재검색 새 근거     : {summary['retry_loop']['new_citations_total']}건 "
        f"(재검색 인용 {summary['retry_loop']['retry_citations_total']}건 중)"
    )
    print(
        f"  채점               : {summary['scoring']['passed']} pass / "
        f"{summary['scoring']['failed']} fail"
    )
    # --out-dir이 레포 밖일 수 있으므로(임시 디렉터리 등) 상대 경로를 강요하지 않는다.
    try:
        shown = out_path.relative_to(REPO_ROOT)
    except ValueError:
        shown = out_path
    print(f"\n저장: {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
