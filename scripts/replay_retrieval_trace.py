"""bench 회차의 Researcher 검색을 트레이스에서 꺼내 **지금의 검색 경로로 다시 돌려** 대조한다.

**왜 있나 (v1.2-T1, ADR-027).** 검색 계층을 고친 뒤 "파이프라인이 받는 입력이 그대로인가"는
골든 질의 프로브만으로는 답이 절반이다 — 프로브 질의는 사람이 쓴 것이고, 파이프라인이
실제로 던지는 검색어는 Outliner가 만든 항목(`search_text = 질의 + 항목`)이다.
이 스크립트는 **그 실제 검색어**를 bench 결과가 가리키는 로컬 트레이스(`researcher_retrieve`
span)에서 꺼내, 같은 `k`로 `ChromaRetriever.search()`(필터 없음)를 다시 호출하고
기록된 후보와 **문서 ID·순위·점수를 허용오차 0으로** 비교한다.

S0c 이후 점수는 모델 입력에 들어가지 않는다(ADR-025, `candidate_score_exposed=false`).
그래도 점수까지 비교한다 — 점수가 움직였다면 인덱스나 임베딩이 바뀐 것이고, 그건
순위가 우연히 같더라도 알아야 하는 사실이다.

    python scripts/replay_retrieval_trace.py docs/eval/bench-v1.2-p1-on.json --out <path>

종료 코드: 0 = 전건 일치 · 1 = 불일치 있음 · 2 = 대조 불가(트레이스 없음 · 잘린 검색어 · 건수 불일치)

LLM 호출 0건. 임베딩은 로컬. 인덱스는 읽기만 한다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.providers.config import load_tracing_settings  # noqa: E402
from src.tools.retrieval import ChromaRetriever  # noqa: E402

EXIT_MISMATCH = 1
EXIT_UNCHECKABLE = 2

# `_safe()`(src/obs/tracer.py)가 4,000자에서 자르며 붙이는 표식. 잘린 검색어는 재생할 수 없다.
_TRUNCATED = re.compile(r"\.\.\.<\d+chars>$")


def _retrieve_spans(trace_path: Path) -> list[dict]:
    spans = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") == "span" and event.get("name") == "researcher_retrieve":
            spans.append(event)
    return spans


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bench", type=Path, nargs="+", help="bench_golden 결과 JSON")
    parser.add_argument("--out", type=Path, default=None, help="결과 JSON 경로")
    args = parser.parse_args()

    trace_dir = load_tracing_settings().local_trace_dir
    retriever = ChromaRetriever()
    started = time.perf_counter()

    report: dict = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "tolerance": 0, "runs": []}
    unchecked: list[str] = []
    total = matched = 0
    mismatches: list[dict] = []

    for bench_path in args.bench:
        bench = json.loads(bench_path.read_text(encoding="utf-8"))
        run = {"bench": bench_path.name, "label": bench.get("label"), "spans": 0,
               "matched": 0, "error_spans": 0, "expected_calls": 0}
        for row in bench["rows"]:
            run["expected_calls"] += int(row.get("retrieval_calls") or 0)
            trace_path = trace_dir / f"{row['run_id']}.jsonl"
            if not trace_path.exists():
                unchecked.append(f"{row['case_id']}: 트레이스 없음 ({trace_path.name})")
                continue
            for span in _retrieve_spans(trace_path):
                run["spans"] += 1
                if span.get("output") is None:  # 검색 오류 span — 재생할 후보가 없다
                    run["error_spans"] += 1
                    continue
                search_text = span["input"]["search_text"]
                if _TRUNCATED.search(search_text):
                    unchecked.append(f"{row['case_id']}: 잘린 검색어")
                    continue
                k = int(span["input"]["k"])
                recorded = [(c["doc_id"], c["score"]) for c in span["output"]]
                replayed = [(c.doc_id, c.score) for c in retriever.search(search_text, k=k)]
                total += 1
                if recorded == replayed:
                    matched += 1
                    run["matched"] += 1
                else:
                    mismatches.append({
                        "bench": bench_path.name, "case_id": row["case_id"],
                        "run_id": row["run_id"], "topic": span["input"].get("topic"),
                        "revision": span["input"].get("revision"),
                        "item_index": span["input"].get("item_index"),
                        "recorded": recorded, "replayed": replayed,
                    })
        if run["spans"] != run["expected_calls"]:
            unchecked.append(
                f"{bench_path.name}: span {run['spans']}건 ≠ 행의 retrieval_calls 합 "
                f"{run['expected_calls']}건"
            )
        report["runs"].append(run)

    report.update({"replayed": total, "matched": matched, "mismatches": mismatches,
                   "unchecked": unchecked,
                   "elapsed_s": round(time.perf_counter() - started, 1)})

    for run in report["runs"]:
        print(f"{run['bench']}: span {run['spans']} (행 합계 {run['expected_calls']}) · "
              f"일치 {run['matched']} · 오류 span {run['error_spans']}")
    print(f"재생 {total}건 · 일치 {matched}건 · 불일치 {len(mismatches)}건 · 대조 불가 {len(unchecked)}건")
    for problem in unchecked[:10]:
        print("  대조 불가:", problem)
    for m in mismatches[:5]:
        print("  불일치:", m["case_id"], m["topic"], m["recorded"], "->", m["replayed"])

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print("→", args.out)

    if unchecked:
        return EXIT_UNCHECKABLE
    return EXIT_MISMATCH if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
