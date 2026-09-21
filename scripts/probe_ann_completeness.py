"""k=전수 질의가 전수를 돌려주는지 **프로세스 단위로** 표본한다 (session-14 §5 → session-15 §7.1).

session-14에서 관측된 것: 같은 코드·같은 인덱스·같은 질의가 실행에 따라 37건 중 36건만
반환한다. `count()`와 `get()`은 항상 37이라 개수로는 이상을 알 수 없다. 표본 ≈3/40이었지만
**서로 다른 세 실험을 사후에 합친 값**이라 빈도라고 부를 수 없었다. 이 스크립트는 같은 조건에서
n회를 한 번에 돌려 빈도를 낸다.

두 가지를 지킨다:

1. **임베딩 모델을 띄우지 않는다.** 인덱스에 이미 들어 있는 벡터를 질의 벡터로 재사용한다.
   관측 대상은 "질의가 무엇이냐"가 아니라 "벡터 세그먼트에 몇 건이 실렸느냐"다.
   콜드 로딩 12~25s × n회는 측정과 무관한 비용이다.
2. **표본 1건 = 프로세스 1개.** 결함이 프로세스 내에서는 일관되므로 (session-14 §5)
   한 프로세스에서 여러 번 질의해도 표본은 1건이다. 부모가 자식을 n번 띄운다.

`--clients`로 session-14 §7.1(3)의 후보 (다)를 가른다 — 프로브는 `PersistentClient`를
두 번 열었다(`_load_corpus_meta` + `ChromaRetriever`). 1회 개방과 2회 개방의 표본을
같은 n으로 비교한다.

사용법:

    python scripts/probe_ann_completeness.py --runs 40 --clients 2 --label two-clients
    python scripts/probe_ann_completeness.py --runs 40 --clients 1 --label one-client

LLM 호출 0건. 외부 API 호출 0건.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "eval"


# --- 자식 프로세스: 표본 1건 -------------------------------------------------


def _sample(clients: int, queries: int) -> dict:
    """이 프로세스에서 인덱스를 열고 k=전수 질의를 걸어 결과를 보고한다.

    `clients=2`는 session-14 프로브의 실제 개방 형태를 재현한다 — 메타 읽기용 클라이언트를
    먼저 열고, 그 다음 검색용 클라이언트를 따로 연다.
    """
    import chromadb

    from src.providers.config import load_vectorstore_settings

    settings = load_vectorstore_settings()

    t0 = time.perf_counter()
    first = chromadb.PersistentClient(path=str(settings.persist_dir))
    col_meta = first.get_collection(settings.collection)

    # 메타 경로: 프로브의 `_load_corpus_meta`가 하는 것과 같은 읽기.
    raw = col_meta.get(include=["metadatas", "embeddings"])
    all_ids = [m["doc_id"] for m in raw["metadatas"]]
    n_total = len(all_ids)
    expected = set(all_ids)

    if clients == 2:
        # 검색 경로는 클라이언트를 새로 연다 (`ChromaRetriever._get_collection`과 같은 형태).
        col_query = chromadb.PersistentClient(path=str(settings.persist_dir)).get_collection(
            settings.collection
        )
    else:
        col_query = col_meta
    open_s = time.perf_counter() - t0

    vectors = [list(v) for v in list(raw["embeddings"])[:queries]]

    per_query: list[dict] = []
    for i, vec in enumerate(vectors):
        res = col_query.query(
            query_embeddings=[vec], n_results=n_total, include=["metadatas"]
        )
        got = [m["doc_id"] for m in res["metadatas"][0]]
        per_query.append(
            {
                "i": i,
                "returned": len(got),
                "missing": sorted(expected - set(got)),
            }
        )

    missing_union = sorted({d for q in per_query for d in q["missing"]})
    returned_counts = sorted({q["returned"] for q in per_query})
    # 프로세스 내 일관성: 모든 질의가 같은 문서를 빠뜨렸는가.
    missing_sets = {tuple(q["missing"]) for q in per_query}

    return {
        "pid": __import__("os").getpid(),
        "clients": clients,
        "n_total": n_total,
        "collection_count": int(col_query.count()),
        "get_count": len(all_ids),
        "queries": len(per_query),
        "returned_counts": returned_counts,
        "complete": returned_counts == [n_total],
        "missing_union": missing_union,
        "consistent_within_process": len(missing_sets) == 1,
        "open_s": round(open_s, 3),
        "per_query": per_query,
    }


# --- 부모 프로세스: n회 반복 -------------------------------------------------


def _run_children(runs: int, clients: int, queries: int) -> list[dict]:
    samples: list[dict] = []
    for r in range(runs):
        proc = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--child",
                "--clients",
                str(clients),
                "--queries",
                str(queries),
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"표본 {r} 실패 (rc={proc.returncode}):\n{proc.stderr[-2000:]}"
            )
        sample = json.loads(proc.stdout.strip().splitlines()[-1])
        sample["run"] = r
        samples.append(sample)
        mark = "." if sample["complete"] else "X"
        print(mark, end="", flush=True)
    print()
    return samples


def _aggregate(samples: list[dict]) -> dict:
    incomplete = [s for s in samples if not s["complete"]]
    doc_tally: Counter[str] = Counter()
    for s in incomplete:
        doc_tally.update(s["missing_union"])
    return {
        "runs": len(samples),
        "incomplete_runs": len(incomplete),
        "incomplete_rate": round(len(incomplete) / len(samples), 4) if samples else None,
        "missing_doc_tally": dict(doc_tally.most_common()),
        "distinct_missing_docs": len(doc_tally),
        "all_consistent_within_process": all(s["consistent_within_process"] for s in samples),
        "count_always_n_total": all(s["collection_count"] == s["n_total"] for s in samples),
        "get_always_n_total": all(s["get_count"] == s["n_total"] for s in samples),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=40, help="표본 수 (= 자식 프로세스 수)")
    ap.add_argument(
        "--clients", type=int, choices=(1, 2), default=2, help="프로세스당 PersistentClient 개방 횟수"
    )
    ap.add_argument("--queries", type=int, default=3, help="프로세스당 질의 수")
    ap.add_argument("--label", default="", help="출력 파일 라벨")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.child:
        print(json.dumps(_sample(args.clients, args.queries), ensure_ascii=False))
        return 0

    print(
        f"표본 {args.runs}회 · clients={args.clients} · 질의 {args.queries}/프로세스 "
        f"(. = 전수, X = 유실)"
    )
    started = time.time()
    samples = _run_children(args.runs, args.clients, args.queries)
    summary = _aggregate(samples)

    import chromadb

    payload = {
        "probe": "ann-completeness",
        "session": "session-15",
        "label": args.label or f"clients-{args.clients}",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
        "elapsed_s": round(time.time() - started, 1),
        "environment": {
            "chromadb": chromadb.__version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "config": {"runs": args.runs, "clients": args.clients, "queries": args.queries},
        "summary": summary,
        "samples": samples,
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"ann-completeness-session-15-{payload['label']}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    try:
        shown = out.relative_to(REPO_ROOT)
    except ValueError:
        shown = out
    print(f"\n-> {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
