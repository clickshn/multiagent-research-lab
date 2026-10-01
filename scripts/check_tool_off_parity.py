"""도구 off 경로 = p1-on 경로인가 — **입력 해시 기준**, LLM 호출 0건 (v1.2-T2, 사전 등록 §4).

**방법.** 기준 회차(`bench-v1.2-p1-on.json`)의 로컬 트레이스에는 LLM 호출마다 `input_hash`
(메시지·temperature·max_tokens 전체의 sha256, 잘림 전 — session-21 §0)와 응답 원문(span output)이
남아 있다. 이 스크립트는 **지금 코드**로 그래프를 다시 돌리되 프로바이더 자리에 "재생 프로바이더"를
끼운다. 재생 프로바이더는 들어온 호출의 입력 해시를 계산해 기준 트레이스에서 같은 해시의 응답을
돌려주고, **해시가 없으면 그 자리에서 멈춘다.** 검색은 실제 로컬 인덱스(`ChromaRetriever`)다.

그래서 30건이 끝까지 돌고 케이스별 입력 해시 다중집합이 기준과 같으면, 지금 코드의 off 경로가
기준 회차와 **같은 순서의 같은 입력을 만든다**는 뜻이다(각 호출의 입력이 앞선 응답·검색 결과에
의존하므로, 한 곳이라도 달라지면 그 뒤의 해시가 전부 어긋난다).

    python scripts/check_tool_off_parity.py docs/eval/bench-v1.2-p1-on.json --out <path>

종료 코드: 0 = 전건 일치 · 1 = 불일치 · 2 = 대조 불가(트레이스 없음 · 필요한 응답이 잘림)

LLM 호출 0건. 임베딩은 로컬. 인덱스는 읽기만 한다. 도구 off(`tech_domain_vocab=None`) 고정.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.orchestrator import compile_graph, initial_state  # noqa: E402
from src.orchestrator.nodes import _input_hash  # noqa: E402
from src.providers import LLMResponse  # noqa: E402
from src.providers.config import load_concurrency_settings, load_tracing_settings  # noqa: E402
from src.tools.retrieval import ChromaRetriever  # noqa: E402

EXIT_MISMATCH = 1
EXIT_UNCHECKABLE = 2
_TRUNCATED = re.compile(r"\.\.\.<\d+chars>$")


class ReplayMiss(RuntimeError):
    """기준 회차에 없는 입력이 나왔다 — off 경로가 기준과 다르다."""


class ReplayProvider:
    """입력 해시 → 기준 회차의 응답. 없는 해시는 예외."""

    def __init__(self, responses: dict[str, str], truncated: set[str]) -> None:
        self.responses = responses
        self.truncated = truncated
        self.hashes: list[str] = []
        self.used_truncated: list[str] = []
        self._lock = threading.Lock()

    def complete(self, messages, *, temperature=0.0, max_tokens=None, stop=None,
                 response_format=None) -> LLMResponse:
        digest = _input_hash(messages, temperature=temperature, max_tokens=max_tokens,
                             response_format=response_format)
        with self._lock:
            self.hashes.append(digest)
        if digest not in self.responses:
            raise ReplayMiss(f"기준 회차에 없는 입력 해시 {digest[:12]}…")
        if digest in self.truncated:
            with self._lock:
                self.used_truncated.append(digest)
        return LLMResponse(text=self.responses[digest], model="replay", finish_reason="stop",
                           prompt_tokens=0, completion_tokens=0, latency_s=0.0)


def _baseline(trace_path: Path) -> tuple[dict[str, str], set[str], list[str], list[str]]:
    """(해시→응답, 잘린 응답의 해시, 기준 해시 목록, 해시 충돌)."""
    responses: dict[str, str] = {}
    truncated: set[str] = set()
    hashes: list[str] = []
    conflicts: list[str] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") != "generation":
            continue
        meta = event.get("metadata") or {}
        digest = meta.get("input_hash")
        if not digest or meta.get("error"):
            continue
        output = event.get("output") or ""
        hashes.append(digest)
        if digest in responses and responses[digest] != output:
            conflicts.append(digest)
        responses.setdefault(digest, output)
        if _TRUNCATED.search(output) and meta.get("node") != "writer":
            truncated.add(digest)  # Writer 출력은 어떤 입력에도 들어가지 않는다
    return responses, truncated, hashes, conflicts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bench", type=Path, help="기준 bench 결과 JSON (p1-on)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    bench = json.loads(args.bench.read_text(encoding="utf-8"))
    golden = json.loads((REPO_ROOT / "docs/eval/golden-set.json").read_text(encoding="utf-8"))
    queries = {c["id"]: c["query"] for c in golden["cases"]}
    trace_dir = load_tracing_settings().local_trace_dir
    retriever = ChromaRetriever()
    concurrency = load_concurrency_settings()
    started = time.perf_counter()

    cases: list[dict] = []
    unchecked: list[str] = []
    for row in bench["rows"]:
        trace_path = trace_dir / f"{row['run_id']}.jsonl"
        if not trace_path.exists():
            unchecked.append(f"{row['case_id']}: 트레이스 없음")
            continue
        responses, truncated, base_hashes, conflicts = _baseline(trace_path)
        provider = ReplayProvider(responses, truncated)
        app = compile_graph(
            provider, retriever=retriever, top_k=bench["top_k"],
            max_concurrency=concurrency.effective, tech_domain_vocab=None,
        )
        miss = None
        try:
            app.invoke(initial_state(queries[row["case_id"]], max_revisions=bench["max_revisions"]),
                       config={"recursion_limit": 50})
        except ReplayMiss as exc:
            miss = str(exc)
        same = miss is None and Counter(provider.hashes) == Counter(base_hashes)
        cases.append({
            "case_id": row["case_id"], "stratum": row.get("stratum"),
            "baseline_calls": len(base_hashes), "replayed_calls": len(provider.hashes),
            "baseline_llm_calls_row": row.get("llm_calls"),
            "identical": same, "miss": miss, "hash_conflicts": len(conflicts),
        })
        if provider.used_truncated:
            unchecked.append(f"{row['case_id']}: 잘린 응답을 입력으로 재생 {len(provider.used_truncated)}건")

    identical = sum(c["identical"] for c in cases)
    calls = sum(c["replayed_calls"] for c in cases if c["identical"])
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "baseline": args.bench.name,
        "baseline_prompt_version": bench.get("prompt_version"),
        "tech_domain_tool": False,
        "max_concurrency": concurrency.effective,
        "llm_calls": 0,
        "cases_identical": identical, "cases": len(cases),
        "calls_identical": calls, "baseline_calls_total": bench["calls"]["total"],
        "unchecked": unchecked,
        "elapsed_s": round(time.perf_counter() - started, 1),
        "per_case": cases,
    }
    print(f"케이스 {identical}/{len(cases)} 동일 · 호출 {calls}/{bench['calls']['total']} 입력 해시 동일 "
          f"· 대조 불가 {len(unchecked)}건")
    for c in cases:
        if not c["identical"]:
            print("  불일치:", c["case_id"], c["miss"] or f"{c['replayed_calls']} vs {c['baseline_calls']}")
    for u in unchecked:
        print("  대조 불가:", u)
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("→", args.out)
    if unchecked:
        return EXIT_UNCHECKABLE
    return 0 if identical == len(cases) == len(bench["rows"]) else EXIT_MISMATCH


if __name__ == "__main__":
    raise SystemExit(main())
