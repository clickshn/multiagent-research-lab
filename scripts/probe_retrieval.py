"""검색 단계만 떼어내 재현율·점수 분포를 재는 읽기 전용 프로브 (ADR-005 Amendment 재현).

**왜 별도 스크립트인가.** `bench_golden.py`는 파이프라인 전체(LLM 호출 포함)를 돌린다.
코퍼스가 바뀌었을 때 "검색이 무엇을 바꿨나"만 보려면 LLM을 빼야 한다 — 생성 품질이
섞이면 변화의 원인을 검색에 귀속시킬 수 없다. 이 스크립트는 **LLM 엔드포인트를 전혀
호출하지 않는다** (임베딩은 로컬 sentence-transformers).

session-05(ADR-005 Amendment)의 프로브는 스크립트로 남지 않아 재현이 불가능했다.
이 파일이 그 자리를 메운다. **검색 코드는 건드리지 않는다** — `ChromaRetriever`를
있는 그대로 호출할 뿐이다.

측정하는 것:
  - 기대 문서의 순위와 top-4 진입 여부 (positive 케이스)
  - 코퍼스에 답이 없는 질의(negative)의 최고 점수
  - "완전 무관"과 "정답" 사이의 점수 간격 — ADR-005 §4의 핵심 지표
  - 1위와 4위의 점수 차 (변별력)
  - 임베딩 콜드 로딩 시간 (측정값이 아니라 기동 1회 비용으로 따로 적는다)

실행:
    python scripts/probe_retrieval.py --label session-12-baseline
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.providers.config import (  # noqa: E402
    load_cache_settings,
    load_embedding_settings,
)
from src.providers.embeddings import get_embedding_provider  # noqa: E402
from src.tools.retrieval import ChromaRetriever  # noqa: E402

GOLDEN_SET = REPO_ROOT / "docs" / "eval" / "golden-set.json"
GOLDEN_SET_EN = REPO_ROOT / "docs" / "eval" / "golden-set-en.json"
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "eval"

TOP_K = 4  # 운영 설정. ADR-006 Amendment의 Researcher 기본값과 같다.


def _warm_up(embeddings) -> tuple[float, float]:
    """임베딩 모델을 상주시키고 (콜드 로딩, 웜 인코딩) 시간을 잰다.

    이걸 먼저 하지 않으면 첫 질의의 지연에 모델 로딩이 통째로 들어간다
    (`docs/governance.md` "측정 방법론").
    """
    started = time.perf_counter()
    embeddings.embed_query("워밍업")
    cold_s = time.perf_counter() - started

    started = time.perf_counter()
    embeddings.embed_query("워밍업 2회차")
    warm_s = time.perf_counter() - started
    return cold_s, warm_s


def _probe_one(
    retriever: ChromaRetriever, query: str, corpus_size: int
) -> tuple[list[dict], float]:
    """코퍼스 전체를 순위로 받아온다.

    top-4만 받으면 "몇 위로 밀렸는가"를 알 수 없다. 실패 케이스의 순위가 이 측정의
    핵심 관측치이므로 k를 코퍼스 크기로 둔다. 운영 설정(top-4)은 이 순위에서 잘라 판정한다.
    """
    started = time.perf_counter()
    chunks = retriever.search(query, k=corpus_size)
    latency = time.perf_counter() - started
    return [
        {"rank": i + 1, "doc_id": c.doc_id, "score": c.score, "source": c.source, "title": c.title}
        for i, c in enumerate(chunks)
    ], latency


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="probe", help="출력 파일 이름에 붙일 라벨")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    golden = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
    en_queries = json.loads(GOLDEN_SET_EN.read_text(encoding="utf-8"))["queries"]
    cases = golden["cases"]

    cache = load_cache_settings()
    emb_settings = load_embedding_settings()

    embeddings = get_embedding_provider()
    cold_s, warm_s = _warm_up(embeddings)

    retriever = ChromaRetriever(embeddings=embeddings)
    corpus_size = retriever.count()

    results: list[dict] = []
    for case in cases:
        case_id = case["id"]
        expected = list(case["expect"]["expected_doc_ids"])
        positive = not case["expect"]["expect_uncovered"]

        entry: dict = {
            "id": case_id,
            "positive": positive,
            "expected_doc_ids": expected,
            "by_lang": {},
        }
        for lang, query in (("ko", case["query"]), ("en", en_queries[case_id])):
            ranking, latency = _probe_one(retriever, query, corpus_size)
            by_doc = {r["doc_id"]: r for r in ranking}
            ranks = [by_doc[d]["rank"] for d in expected if d in by_doc]
            scores = [r["score"] for r in ranking]
            entry["by_lang"][lang] = {
                "query": query,
                "retrieval_latency_s": round(latency, 4),
                "expected_ranks": ranks,
                # positive 케이스는 기대 문서가 **하나라도** top-4에 들어오면 성공으로 센다.
                # session-05와 같은 셈법이다 (GS-003은 기대 문서 2건이지만 1건으로 카운트).
                "in_top_k": bool(ranks) and min(ranks) <= TOP_K,
                "best_expected_rank": min(ranks) if ranks else None,
                "top_score": scores[0] if scores else None,
                "rank4_score": scores[TOP_K - 1] if len(scores) >= TOP_K else None,
                "rank1_minus_rank4": (
                    round(scores[0] - scores[TOP_K - 1], 4) if len(scores) >= TOP_K else None
                ),
                "top_k": [
                    {"rank": r["rank"], "doc_id": r["doc_id"], "score": r["score"], "source": r["source"]}
                    for r in ranking[:TOP_K]
                ],
            }
        results.append(entry)

    positives = [r for r in results if r["positive"]]
    negatives = [r for r in results if not r["positive"]]

    def _recall(lang: str) -> dict:
        hits = sum(1 for r in positives if r["by_lang"][lang]["in_top_k"])
        return {
            "hits": hits,
            "n": len(positives),
            "recall": round(hits / len(positives), 4) if positives else None,
            "mean_expected_rank": round(
                statistics.fmean(r["by_lang"][lang]["best_expected_rank"] for r in positives), 4
            ),
        }

    def _by_id(case_id: str) -> dict:
        return next(r for r in results if r["id"] == case_id)

    # ADR-005 §4가 고른 두 앵커를 그대로 다시 잰다 —
    # "코퍼스에 답이 전혀 없는 질의"(GS-008)와 "정답을 1위로 맞힌 질의"(GS-005).
    # 다른 케이스로 바꾸면 v1.0 수치와 비교가 성립하지 않는다.
    summary: dict = {
        "label": args.label,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "corpus_size": corpus_size,
        "top_k": TOP_K,
        "golden_set_cases": len(cases),
        "positive_cases": len(positives),
        "negative_cases": len(negatives),
        "embedding_model": emb_settings.model_name,
        "embedding_revision": emb_settings.revision,
        # 이 프로브는 LLM을 부르지 않으므로 캐시는 결과에 영향이 없다.
        # 그래도 기록한다 — 기록하지 않으면 "확인했다"를 증명할 수 없다.
        "cache_enabled": cache.enabled,
        "llm_calls": 0,
        "cold_load_s": round(cold_s, 3),
        "warm_encode_s": round(warm_s, 4),
        "recall_top4": {"ko": _recall("ko"), "en": _recall("en")},
        "score_gap": {},
    }
    for lang in ("ko", "en"):
        irrelevant = _by_id("GS-008")["by_lang"][lang]["top_score"]
        correct = _by_id("GS-005")["by_lang"][lang]["top_score"]
        summary["score_gap"][lang] = {
            "irrelevant_query_top_score_GS-008": irrelevant,
            "correct_at_rank1_top_score_GS-005": correct,
            "gap": round(correct - irrelevant, 4),
        }
        summary[f"rank1_minus_rank4_{lang}"] = {
            r["id"]: r["by_lang"][lang]["rank1_minus_rank4"] for r in results
        }

    payload = {"summary": summary, "cases": results}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / f"probe-retrieval-{args.label}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n→ {out_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
