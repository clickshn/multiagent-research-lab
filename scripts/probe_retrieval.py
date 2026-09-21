"""검색 단계만 떼어내 재현율·점수 분포를 재는 읽기 전용 프로브 (ADR-005 Amendment 재현).

**왜 별도 스크립트인가.** `bench_golden.py`는 파이프라인 전체(LLM 호출 포함)를 돌린다.
코퍼스가 바뀌었을 때 "검색이 무엇을 바꿨나"만 보려면 LLM을 빼야 한다 — 생성 품질이
섞이면 변화의 원인을 검색에 귀속시킬 수 없다. 이 스크립트는 **LLM 엔드포인트를 전혀
호출하지 않는다** (임베딩은 로컬 sentence-transformers).

session-05(ADR-005 Amendment)의 프로브는 스크립트로 남지 않아 재현이 불가능했다.
이 파일이 그 자리를 메운다. **검색 코드는 건드리지 않는다** — `ChromaRetriever`를
있는 그대로 호출할 뿐이다.

**session-13에서 층화 집계를 추가했다 (ADR-022).** 골든셋이 9 -> 30건이 되면서
기대 문서에 온톨로지 메타가 없는 케이스(층 B)가 9건 생겼다. 이들은 Session 3의
strict 필터에서 **구조적으로 재현율 0**이 되므로, 합산 평균에 섞으면 "필터가 나쁘다"로
잘못 읽힌다. 층은 **골든셋 파일이 선언한 값을 읽을 뿐 여기서 계산하지 않는다** —
측정 시점에 층을 정하면 사후 제외와 구별되지 않는다.

**session-14에서 온톨로지 필터 arm을 배선했다 (ADR-023).** `--arm`으로 `release_type` /
`tech_domain` 필터를 **프로브 안에서 후처리로** 건다 — k=37 전수를 받아 걸러내므로
`src/tools/retrieval.py`는 계속 변경 0줄이고 재인덱싱도 없다 (ADR-022 Decision 6).
같은 세션에서 `score_gap_canonical`도 넣었다 (ADR-005 Amendment 2 후속 체크박스):
간격 표기 규칙을 **사람 기억이 아니라 도구가 지키게** 한다.

측정하는 것:
  - 기대 문서의 순위와 top-4 진입 여부 (positive 케이스)
  - 층 A / 층 B / v1.0 부분집합(GS-001~007)별 재현율 — **합산은 필터 없음에서만 참고**
  - 코퍼스에 답이 없는 질의(negative)의 최고 점수
  - `score_gap_canonical` — 최난도 / 평균 / n / 골든셋 버전 (ADR-005 Amendment 2 표기 규칙)
  - 1위와 4위의 점수 차 (변별력)
  - **필터 arm:** 걸러진 문서의 출신 구성 · 필터 후 출신별 밀어내기 재분해 ·
    한국어 단문 잔존 수 (= 개선을 "오염 제거"로 설명할 수 있는지의 판정 재료)
  - 임베딩 콜드 로딩 시간 (측정값이 아니라 기동 1회 비용으로 따로 적는다)

실행:
    python scripts/probe_retrieval.py --label session-14-nofilter
    python scripts/probe_retrieval.py --arm tech_domain --null-policy strict --label ...
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.providers.config import (  # noqa: E402
    load_cache_settings,
    load_embedding_settings,
    load_vectorstore_settings,
)
from src.providers.embeddings import get_embedding_provider  # noqa: E402
from src.tools.retrieval import ChromaRetriever  # noqa: E402

# 출신 × 길이 분류는 session-12의 프로브에서 **그대로 가져온다.** 복사하면 두 스크립트가
# 조용히 어긋나고, 그러면 "필터 전/후 밀어내기 분해"가 같은 잣대로 잰 값이 아니게 된다.
from probe_length_vs_language import SHORT_CHARS, _group  # noqa: E402

GOLDEN_SET = REPO_ROOT / "docs" / "eval" / "golden-set.json"
GOLDEN_SET_EN = REPO_ROOT / "docs" / "eval" / "golden-set-en.json"
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "eval"

TOP_K = 4  # 운영 설정. ADR-006 Amendment의 Researcher 기본값과 같다.

# score_gap의 positive 앵커. **고정한다** — 앵커를 바꾸면 과거 수치와 비교가 끊긴다.
#   연속성 앵커: ADR-005 §4가 고른 GS-005. 단 층 B라 **필터 arm에서는 쓸 수 없다**
#                (골든셋 GS-005 targets_ontology note가 이 사실을 미리 적어 뒀다).
#   층 A 앵커:   필터 arm에서 쓰는 앵커. 층 A · anchor_meta=present · 무필터 ko 1위 ·
#                release_type/tech_domains 둘 다 선언된 케이스 중 ID가 가장 앞선 GS-013.
CANONICAL_ANCHOR = "GS-005"
CANONICAL_ANCHOR_STRATUM_A = "GS-013"

# 걸러진 문서 구성을 셀 때 쓰는 출신 군 순서 (probe_length_vs_language와 동일)
GROUPS = ("v1.0-long", "new-en-long", "new-en-short", "new-ko")

# `filter_axis`에서 도메인 값을 뽑는다. 골든셋이 "tech_domains=Reasoning"처럼 적어 둔 것을
# 읽을 뿐 여기서 축을 고르지 않는다 — 측정 시점에 축을 고르면 사후 선택이다.
_TECH_AXIS_RE = re.compile(r"tech_domains\s*=\s*([^ (,]+)")


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


def _load_corpus_meta() -> dict[str, dict]:
    """인덱스에 들어 있는 메타데이터를 읽는다 (읽기 전용).

    `RetrievedChunk`에는 온톨로지 필드가 실리지 않는다. 필터를 걸려면 메타가 필요한데,
    **그걸 위해 `retrieval.py`를 고치지 않는다** — 컬렉션을 직접 읽어 프로브 안에서 쓴다
    (ADR-022 Decision 6: 후처리 필터).
    """
    settings = load_vectorstore_settings()
    import chromadb

    collection = chromadb.PersistentClient(path=str(settings.persist_dir)).get_collection(
        settings.collection
    )
    raw = collection.get(include=["metadatas", "documents"])

    docs: dict[str, dict] = {}
    for meta, text in zip(raw["metadatas"], raw["documents"], strict=False):
        # 인덱싱된 임베딩 입력과 같은 방식으로 길이를 센다 (`ChromaRetriever.index`).
        emb_input = f"{meta.get('title', '')}\n\n{text or ''}".strip()
        domains = meta.get("tech_domains")
        docs[meta["doc_id"]] = {
            "release_type": meta.get("release_type"),
            "tech_domains": tuple(domains.split("|")) if domains else (),
            "lang": meta.get("lang"),
            "group": _group(meta, len(emb_input)),
            "chars": len(emb_input),
            "title": meta.get("title", ""),
            "extraction_model": meta.get("extraction_model"),
        }
    return docs


def _axis_value(doc: dict, arm: str):
    return doc["release_type"] if arm == "release_type" else doc["tech_domains"]


def _resolve_filter_value(case: dict, arm: str, domain_counts: dict[str, int]):
    """이 케이스에 걸 필터 값을 **골든셋 선언에서** 끌어낸다.

    규칙(사전 선언, ADR-023):
      - `release_type` arm: 앵커의 `targets_ontology.release_type`을 그대로 쓴다.
      - `tech_domain` arm: `filter_axis`가 `tech_domains=X` 형태로 축을 선언했으면 그 값.
        선언이 없으면 앵커의 `tech_domains` 중 **코퍼스 내 선택도가 가장 높은(빈도가 가장
        낮은) 도메인**, 동률이면 선언 순서가 앞선 것.
      - 둘 다 없으면(층 B) `None` — 필터 값 자체가 선언되지 않는다.

    ⚠️ **이 값은 앵커에서 나온다. 즉 오라클이다.** 질의로부터 필터 값을 고르는 단계는
    여기서 측정하지 않으므로, 이 arm의 개선폭은 **필터 효과의 상한**이다.
    """
    targets = case.get("targets_ontology") or {}
    if arm == "release_type":
        value = targets.get("release_type")
        return value, ("targets_ontology.release_type" if value else "declared-null")

    axis = targets.get("filter_axis") or ""
    matched = _TECH_AXIS_RE.search(axis)
    if matched:
        return matched.group(1), "targets_ontology.filter_axis"
    raw = targets.get("tech_domains")
    if raw:
        domains = raw.split("|")
        # min은 안정 정렬이라 동률이면 선언 순서가 앞선 것이 선택된다.
        return (
            min(domains, key=lambda d: domain_counts.get(d, 0)),
            "most-selective-of targets_ontology.tech_domains",
        )
    return None, "declared-null"


def _survives(doc: dict, arm: str, value, null_policy: str) -> bool:
    """후처리 필터 1건 판정.

    - `strict`: 해당 축의 메타가 없으면 탈락한다 (운영 필터의 실제 동작, ADR-022 Decision 1).
    - `pass`:   메타가 없으면 통과시킨다 (ADR-022 Alternatives (나) — 2차 arm).

    **필터 값이 선언되지 않은 케이스(층 B)는 매치 대상이 존재할 수 없다.** strict에서는
    생존자가 0이 되고, 이것이 ADR-022 Decision 5가 말한 "구조적 재현율 0"의 정확한 형태다.
    """
    got = _axis_value(doc, arm)
    has_meta = bool(got)
    if value is None:
        return null_policy == "pass" and not has_meta
    if not has_meta:
        return null_policy == "pass"
    return got == value if arm == "release_type" else value in got


class IncompleteRetrieval(RuntimeError):
    """k=코퍼스 크기로 물었는데 전수가 오지 않았다 (session-14에서 발견)."""


def _probe_one(
    retriever: ChromaRetriever, query: str, corpus_size: int
) -> tuple[list[dict], float]:
    """코퍼스 전체를 순위로 받아온다.

    top-4만 받으면 "몇 위로 밀렸는가"를 알 수 없다. 실패 케이스의 순위가 이 측정의
    핵심 관측치이므로 k를 코퍼스 크기로 둔다. 운영 설정(top-4)은 이 순위에서 잘라 판정한다.
    필터 arm도 여기서 받은 전수 위에 후처리로 건다.

    ⚠️ **"k=전수면 전수가 온다"는 전제는 사실이 아니다 (session-14).** Chroma의 HNSW는
    근사 검색이라 프로세스에 따라 37건 중 36건만 오는 실행이 관측됐고, 빠진 문서가
    꼴찌가 아니라 **3위 문서(GS-021의 기대 문서)** 였던 실행도 있다. 조용히 넘어가면
    재현율이 검색 품질이 아니라 인덱스 로드 운으로 흔들린다 — **측정을 중단시킨다.**
    """
    started = time.perf_counter()
    chunks = retriever.search(query, k=corpus_size)
    latency = time.perf_counter() - started
    if len(chunks) < corpus_size:
        raise IncompleteRetrieval(
            f"k={corpus_size}로 질의했는데 {len(chunks)}건만 반환됐다. "
            "HNSW 근사 검색의 실행별 편차다 (session-14 §ANN 재현성). "
            "이 실행의 수치는 버리고 프로세스를 다시 띄워라."
        )
    return [
        {"rank": i + 1, "doc_id": c.doc_id, "score": c.score, "source": c.source, "title": c.title}
        for i, c in enumerate(chunks)
    ], latency


def _compose(doc_ids, docs: dict[str, dict]) -> dict[str, int]:
    """문서 묶음을 출신 군별 건수로 센다 (session-12 `_group` 재사용)."""
    counts = {g: 0 for g in GROUPS}
    for doc_id in doc_ids:
        counts[docs[doc_id]["group"]] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="probe", help="출력 파일 이름에 붙일 라벨")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--arm",
        choices=("none", "release_type", "tech_domain"),
        default="none",
        help="온톨로지 필터 축. none이면 session-13 baseline과 같은 측정이다",
    )
    parser.add_argument(
        "--null-policy",
        choices=("strict", "pass"),
        default="strict",
        help="메타 결측 문서의 처리 (ADR-022). strict=탈락(주 arm) / pass=통과(2차 arm)",
    )
    args = parser.parse_args()

    golden = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
    en_queries = json.loads(GOLDEN_SET_EN.read_text(encoding="utf-8"))["queries"]
    cases = golden["cases"]
    golden_version = golden["version"]

    cache = load_cache_settings()
    emb_settings = load_embedding_settings()

    docs = _load_corpus_meta()
    domain_counts: dict[str, int] = {}
    for doc in docs.values():
        for domain in doc["tech_domains"]:
            domain_counts[domain] = domain_counts.get(domain, 0) + 1

    embeddings = get_embedding_provider()
    cold_s, warm_s = _warm_up(embeddings)

    retriever = ChromaRetriever(embeddings=embeddings)
    corpus_size = retriever.count()

    arm = args.arm
    null_policy = args.null_policy if arm != "none" else "n/a"

    # 필터 값을 **질의를 돌리기 전에 전건 확정한다.** negative의 점수 간격을 잴 때
    # "앵커가 돌았던 것과 같은 필터 값"이 필요한데, 루프 안에서 그때그때 정하면
    # 그 값이 측정 순서에 의존하게 된다.
    resolved: dict[str, tuple] = {
        case["id"]: (
            (None, "n/a") if arm == "none" else _resolve_filter_value(case, arm, domain_counts)
        )
        for case in cases
    }
    # 이 arm에서 실제로 쓰인 필터 값들. negative를 같은 값으로 걸어 간격을 잰다.
    arm_filter_values = sorted(
        {
            resolved[case["id"]][0]
            for case in cases
            if not case["expect"]["expect_uncovered"] and resolved[case["id"]][0]
        }
    )

    results: list[dict] = []
    for case in cases:
        case_id = case["id"]
        expected = list(case["expect"]["expected_doc_ids"])
        positive = not case["expect"]["expect_uncovered"]
        filter_value, value_source = resolved[case_id]

        # ⚠️ **negative의 주 순위는 필터를 걸지 않는다 (사전 선언).** negative는 앵커가
        # 없어 자기 필터 값을 선언할 수 없고, 여기서 값을 고르면 사후에 만든 축이 된다.
        # 골든셋 GS-008도 "필터와 무관하게 최고 점수 자체가 관측치"라고 적어 뒀다.
        #
        # 다만 **점수 간격을 잴 때는 그 정책으로는 아무것도 재지 못한다.** 필터는 점수를
        # 바꾸지 않고 후보만 지우므로, positive만 걸고 negative를 안 걸면 간격은
        # **arm과 무관하게 항상 같은 값**이 나온다 — 구성상 상수다.
        # 그래서 간격용으로는 `top_score_by_filter_value`를 따로 낸다:
        # **앵커가 돌았던 것과 같은 필터 값**으로 negative를 걸어 양쪽을 맞춘다.
        apply_filter = arm != "none" and positive

        entry: dict = {
            "id": case_id,
            "positive": positive,
            # ADR-022: 층·앵커 메타 보유 여부는 **골든셋 파일이 선언한 값을 그대로 읽는다.**
            # 여기서 계산하면 측정 시점에 층을 정하는 것이 되고, 그건 사후 제외와 같다.
            "stratum": case.get("stratum"),
            "anchor_meta": case.get("anchor_meta"),
            "case_type": case.get("case_type"),
            "expected_doc_ids": expected,
            "filter": {
                "arm": arm,
                "null_policy": null_policy,
                "applied": apply_filter,
                "value": filter_value,
                "value_source": value_source,
                "value_declared": filter_value is not None,
            },
            "by_lang": {},
        }

        if apply_filter:
            survivor_ids = [
                d for d in docs if _survives(docs[d], arm, filter_value, null_policy)
            ]
            removed_ids = [d for d in docs if d not in set(survivor_ids)]
            entry["filter"].update(
                {
                    "survivors": len(survivor_ids),
                    "removed": len(removed_ids),
                    "survivor_composition": _compose(survivor_ids, docs),
                    "removed_composition": _compose(removed_ids, docs),
                    # ③ 대조군 판정 재료: 한국어 단문이 남았는가.
                    "ko_short_survivors": [
                        d for d in survivor_ids if docs[d]["group"] == "new-ko"
                    ],
                    "expected_survives": [d for d in expected if d in set(survivor_ids)],
                }
            )
            survivors = set(survivor_ids)
        else:
            survivors = set(docs)

        for lang, query in (("ko", case["query"]), ("en", en_queries[case_id])):
            ranking, latency = _probe_one(retriever, query, corpus_size)
            unfiltered_rank = {r["doc_id"]: r["rank"] for r in ranking}
            kept = [r for r in ranking if r["doc_id"] in survivors]
            # 필터 후 순위를 1부터 다시 매긴다. 원래 순위도 남긴다 — 얼마나 올라왔는지가
            # 이번 측정의 관측치다.
            ranked = [
                {**r, "rank": i + 1, "rank_unfiltered": r["rank"]}
                for i, r in enumerate(kept)
            ]
            by_doc = {r["doc_id"]: r for r in ranked}
            ranks = [by_doc[d]["rank"] for d in expected if d in by_doc]
            scores = [r["score"] for r in ranked]
            best = min(ranks) if ranks else None

            lang_entry = {
                "query": query,
                "retrieval_latency_s": round(latency, 4),
                "expected_ranks": ranks,
                # positive 케이스는 기대 문서가 **하나라도** top-4에 들어오면 성공으로 센다.
                # session-05와 같은 셈법이다 (GS-003은 기대 문서 2건이지만 1건으로 카운트).
                "in_top_k": bool(ranks) and best <= TOP_K,
                "best_expected_rank": best,
                "best_expected_rank_unfiltered": min(
                    (unfiltered_rank[d] for d in expected if d in unfiltered_rank),
                    default=None,
                ),
                "best_expected_score": (
                    min(
                        (by_doc[d] for d in expected if d in by_doc),
                        key=lambda r: r["rank"],
                    )["score"]
                    if ranks
                    else None
                ),
                "candidates_after_filter": len(ranked),
                "top_score": scores[0] if scores else None,
                "rank4_score": scores[TOP_K - 1] if len(scores) >= TOP_K else None,
                "rank1_minus_rank4": (
                    round(scores[0] - scores[TOP_K - 1], 4) if len(scores) >= TOP_K else None
                ),
                "top_k": [
                    {
                        "rank": r["rank"],
                        "rank_unfiltered": r["rank_unfiltered"],
                        "doc_id": r["doc_id"],
                        "score": r["score"],
                        "source": r["source"],
                        "group": docs[r["doc_id"]]["group"],
                    }
                    for r in ranked[:TOP_K]
                ],
            }
            # ② 출신별 밀어내기 재분해 — 기대 문서 위에 선 문서들의 출신 구성.
            # 무필터 baseline(session-13 §4.1)과 같은 방식으로 세야 비교가 성립한다.
            if positive and best is not None:
                above = [r["doc_id"] for r in ranked[: best - 1]]
                lang_entry["displacement"] = {
                    "n": len(above),
                    "by_group": _compose(above, docs),
                    "doc_ids": above,
                }
            elif positive:
                lang_entry["displacement"] = None

            # negative를 arm의 각 필터 값으로 걸었을 때의 최고 점수.
            # 간격 계산에서 **positive 앵커와 같은 값**을 골라 쓴다 (양쪽 동일 조건).
            if not positive and arm != "none":
                per_value: dict[str, float | None] = {}
                for value in arm_filter_values:
                    survived = [
                        r["score"]
                        for r in ranking
                        if _survives(docs[r["doc_id"]], arm, value, null_policy)
                    ]
                    per_value[value] = survived[0] if survived else None
                lang_entry["top_score_by_filter_value"] = per_value
            entry["by_lang"][lang] = lang_entry
        results.append(entry)

    positives = [r for r in results if r["positive"]]
    negatives = [r for r in results if not r["positive"]]

    def _recall(lang: str, subset: list[dict] | None = None) -> dict:
        rows = positives if subset is None else subset
        if not rows:
            return {"hits": 0, "n": 0, "recall": None, "mean_expected_rank": None}
        hits = sum(1 for r in rows if r["by_lang"][lang]["in_top_k"])
        found = [
            r["by_lang"][lang]["best_expected_rank"]
            for r in rows
            if r["by_lang"][lang]["best_expected_rank"] is not None
        ]
        return {
            "hits": hits,
            "n": len(rows),
            "recall": round(hits / len(rows), 4),
            # 필터에 기대 문서가 걸러진 케이스는 순위가 없다. 그 케이스를 빼고 낸 평균은
            # **생존한 것만의 평균**이므로 n을 함께 적는다 — 안 적으면 "순위가 좋아졌다"가
            # 실은 "나쁜 케이스가 사라졌다"인 것을 구별할 수 없다.
            "mean_expected_rank": round(statistics.fmean(found), 4) if found else None,
            "ranked_n": len(found),
            "expected_filtered_out": [
                r["id"] for r in rows if r["by_lang"][lang]["best_expected_rank"] is None
            ],
            "case_ids": [r["id"] for r in rows],
            "misses": [r["id"] for r in rows if not r["by_lang"][lang]["in_top_k"]],
        }

    def _by_id(case_id: str) -> dict:
        return next(r for r in results if r["id"] == case_id)

    # --- ADR-022 층화 -------------------------------------------------------
    # 층 A = positive & 앵커 메타 보유 (필터 적격, Session 3의 주 지표)
    # 층 B = positive & 앵커 메타 결측 (strict 필터에서 구조적으로 재현율 0)
    # 층 C = negative
    # **A+B 합산은 필터 없음(baseline)에서만 참고로 낸다.** 필터 적용 시에는 내지 않는다.
    stratum_a = [r for r in positives if r["stratum"] == "A"]
    stratum_b = [r for r in positives if r["stratum"] == "B"]
    # v1.0(GS-001~009)만 뽑은 부분집합. 골든셋이 30건으로 바뀌어 합산 수치는
    # session-12와 직접 비교할 수 없다 — 연속 비교는 이 부분집합으로만 성립한다.
    v1_positives = [r for r in positives if r["id"] <= "GS-007"]

    # ADR-005 §4가 고른 두 앵커를 그대로 다시 잰다 —
    # "코퍼스에 답이 전혀 없는 질의"(GS-008)와 "정답을 1위로 맞힌 질의"(GS-005).
    # 다른 케이스로 바꾸면 v1.0 수치와 비교가 성립하지 않는다.
    summary: dict = {
        "label": args.label,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "arm": arm,
        "null_policy": null_policy,
        "corpus_size": corpus_size,
        "top_k": TOP_K,
        "golden_set_version": golden_version,
        "golden_set_cases": len(cases),
        "positive_cases": len(positives),
        "negative_cases": len(negatives),
        "embedding_model": emb_settings.model_name,
        "embedding_revision": emb_settings.revision,
        # 필터 효과에 추출 모델 교체가 섞이지 않았는지의 확인값 (session-12 §0).
        "extraction_models": sorted(
            {d["extraction_model"] or "(none)" for d in docs.values()}
        ),
        # 이 프로브는 LLM을 부르지 않으므로 캐시는 결과에 영향이 없다.
        # 그래도 기록한다 — 기록하지 않으면 "확인했다"를 증명할 수 없다.
        "cache_enabled": cache.enabled,
        "llm_calls": 0,
        "cold_load_s": round(cold_s, 3),
        "warm_encode_s": round(warm_s, 4),
        # ⚠️ ADR-022: 아래 4개 중 판정에 쓰는 것은 stratum_A다.
        # all_positives는 **필터 없음 조건에서만** 의미가 있다.
        "recall_top4": {
            "stratum_A": {"ko": _recall("ko", stratum_a), "en": _recall("en", stratum_a)},
            "stratum_B": {"ko": _recall("ko", stratum_b), "en": _recall("en", stratum_b)},
            "all_positives": (
                {"ko": _recall("ko"), "en": _recall("en")}
                if arm == "none"
                else "필터 arm에서는 합산을 내지 않는다 (ADR-022 Decision 4)"
            ),
            "v1_subset_GS001_007": {
                "ko": _recall("ko", v1_positives),
                "en": _recall("en", v1_positives),
            },
            "note": (
                "stratum_A만이 필터 판정의 주 지표다 (ADR-022). "
                "all_positives는 필터 없음 조건에서만 참고로 낸다. "
                "v1_subset_GS001_007만이 session-12와 동일 구성이라 연속 비교가 성립한다."
            ),
        },
        "strata_counts": {
            "A": len(stratum_a),
            "B": len(stratum_b),
            "C": len(negatives),
        },
        "score_gap": {},
        "score_gap_canonical": {},
        "negatives_top_score": {},
    }

    if arm != "none":
        # ① 걸러진 문서의 구성 — 층 A 전체에 대한 합산과 케이스 평균.
        by_case = {
            r["id"]: {
                "value": r["filter"]["value"],
                "survivors": r["filter"].get("survivors"),
                "removed_composition": r["filter"].get("removed_composition"),
                "ko_short_survivors": len(r["filter"].get("ko_short_survivors") or []),
                "expected_survives": bool(r["filter"].get("expected_survives")),
            }
            for r in positives
        }
        removed_totals = {g: 0 for g in GROUPS}
        for r in stratum_a:
            for group, n in (r["filter"].get("removed_composition") or {}).items():
                removed_totals[group] += n
        summary["filter_composition"] = {
            "stratum_A_cases": len(stratum_a),
            "removed_total_by_group_sum_over_stratum_A": removed_totals,
            "removed_mean_per_case_by_group": {
                g: round(removed_totals[g] / len(stratum_a), 2) for g in GROUPS
            },
            "ko_short_corpus_total": sum(
                1 for d in docs.values() if d["group"] == "new-ko"
            ),
            "ko_short_survivors_by_case": {
                r["id"]: len(r["filter"].get("ko_short_survivors") or []) for r in stratum_a
            },
            "by_case": by_case,
            "note": (
                "필터 값은 앵커(targets_ontology)에서 끌어낸 오라클이다 — "
                "질의에서 필터 값을 고르는 단계는 측정하지 않았으므로 개선폭은 상한이다. "
                "negative에는 필터를 걸지 않았다(사전 선언)."
            ),
        }

    for lang in ("ko", "en"):
        irrelevant = _by_id("GS-008")["by_lang"][lang]["top_score"]
        correct = _by_id(CANONICAL_ANCHOR)["by_lang"][lang]["top_score"]
        summary["score_gap"][lang] = {
            "irrelevant_query_top_score_GS-008": irrelevant,
            "correct_at_rank1_top_score_GS-005": correct,
            "gap": round(correct - irrelevant, 4) if None not in (correct, irrelevant) else None,
            "note": "연속성 지표다. 정본 간격이 아니다 (ADR-005 Amendment 2 규칙 5).",
        }
        # negative가 2건 -> 7건이 됐다. GS-008 하나로 "무관 질의의 최고 점수"를 대표시키면
        # 근접 negative(GS-026~030)가 그보다 높은지 낮은지를 볼 수 없다. 전건을 적는다.
        neg_scores = {
            r["id"]: r["by_lang"][lang]["top_score"] for r in negatives
        }
        summary["negatives_top_score"][lang] = neg_scores
        summary[f"rank1_minus_rank4_{lang}"] = {
            r["id"]: r["by_lang"][lang]["rank1_minus_rank4"] for r in results
        }

        # --- ADR-005 Amendment 2 후속: 표기 규칙을 도구가 지킨다 --------------
        # 키 순서가 규칙 순서다: hardest -> mean -> n -> golden_set_version.
        # 하나만 적는 것을 막으려고 **파생값을 따로 내지 않고 한 덩어리로** 낸다.
        canonical: dict[str, dict] = {}
        for anchor_key, anchor_id in (
            ("continuity_anchor_GS-005", CANONICAL_ANCHOR),
            ("stratum_A_anchor_GS-013", CANONICAL_ANCHOR_STRATUM_A),
        ):
            anchor = _by_id(anchor_id)
            anchor_value = anchor["filter"]["value"]
            if arm == "none" or anchor_value is None:
                matched = neg_scores
                policy = "무필터 (arm=none이거나 앵커에 필터 값이 선언되지 않았다)"
            else:
                # 앵커와 **같은 필터 값**으로 negative를 건다. 이래야 "필터가 간격을
                # 벌렸는가"가 측정 가능해진다 — 한쪽만 걸면 간격은 구성상 상수다.
                matched = {
                    r["id"]: r["by_lang"][lang]["top_score_by_filter_value"].get(anchor_value)
                    for r in negatives
                }
                policy = f"negative도 앵커와 동일한 필터 값({arm}={anchor_value})으로 걸었다"
            canonical[anchor_key] = _canonical_gap(
                anchor["by_lang"][lang]["best_expected_score"],
                matched,
                golden_version,
                anchor_id=anchor_id,
                anchor_stratum=anchor["stratum"],
                arm=arm,
                policy=policy,
            )
        summary["score_gap_canonical"][lang] = canonical

    payload = {"summary": summary, "cases": results}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / f"probe-retrieval-{args.label}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # 케이스별 표 — 평균만 보면 개별 회귀가 가려진다 (v1.0 GS-006이 그 사례다).
    print(f"\n케이스별 (arm={arm} / null={null_policy})  ko 질의 / en 질의")
    header = (
        f"{'ID':8} {'층':3} {'유형':13} {'필터값':18} {'생존':>4} "
        f"{'ko순위':>7} {'ko':3} {'en순위':>7} {'en':3}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        ko, en = r["by_lang"]["ko"], r["by_lang"]["en"]

        def _fmt(d: dict) -> tuple[str, str]:
            rank = d["best_expected_rank"]
            if rank is None:
                return ("걸러짐", "❌")
            return (str(rank), "✅" if d["in_top_k"] else "❌")

        ko_r, ko_hit = _fmt(ko)
        en_r, en_hit = _fmt(en)
        if not r["positive"]:
            ko_r = en_r = "-"
            ko_hit = en_hit = "neg"
        print(
            f"{r['id']:8} {r['stratum'] or '-':3} {r['case_type'] or '-':13} "
            f"{str(r['filter']['value'] or '-'):18} "
            f"{str(r['filter'].get('survivors') or corpus_size):>4} "
            f"{ko_r:>7} {ko_hit:3} {en_r:>7} {en_hit:3}"
        )

    for lang in ("ko", "en"):
        canon = summary["score_gap_canonical"][lang]["stratum_A_anchor_GS-013"]
        print(f"\n[{lang}] score_gap_canonical (층 A 앵커): {canon['display']}")

    try:
        shown = out_path.relative_to(REPO_ROOT)
    except ValueError:  # --out-dir이 레포 밖이면(스크래치 측정) 절대 경로로 적는다
        shown = out_path.resolve()
    print(f"\n→ {shown}")
    return 0


def _canonical_gap(
    anchor_score: float | None,
    negative_top_scores: dict[str, float],
    golden_version: str,
    *,
    anchor_id: str,
    anchor_stratum: str | None,
    arm: str,
    policy: str,
) -> dict:
    """ADR-005 Amendment 2의 표기 규칙을 그대로 구조화한다.

    규칙 1·2: 두 값을 병기하고 **최난도를 먼저** 쓴다 → 키 순서가 곧 규칙 순서다.
    규칙 4: negative 집합(골든셋 버전 + n)을 함께 밝힌다.
    앵커가 필터에 걸려 사라졌으면 **다른 앵커로 갈아타지 않고 null과 사유를 낸다** —
    갈아타면 그 순간 "가장 관대한 쪽으로 수렴"이 다시 일어난다.
    """
    usable = [s for s in negative_top_scores.values() if s is not None]
    n = len(usable)
    if anchor_score is None or not usable:
        return {
            "hardest": None,
            "mean": None,
            "n": n,
            "golden_set_version": golden_version,
            "display": f"측정 불가 (앵커 {anchor_id} 소실), negative n={n}",
            "unavailable_reason": (
                f"앵커 {anchor_id}(층 {anchor_stratum})의 기대 문서가 arm={arm} 필터에서 제거됐다"
                if anchor_score is None
                else "negative 최고점 없음"
            ),
            "anchor": anchor_id,
        }
    hardest_id = max(
        (k for k, v in negative_top_scores.items() if v is not None),
        key=lambda k: negative_top_scores[k],
    )
    hardest = round(anchor_score - negative_top_scores[hardest_id], 4)
    mean = round(anchor_score - statistics.fmean(usable), 4)
    return {
        "hardest": hardest,
        "mean": mean,
        "n": n,
        "golden_set_version": golden_version,
        "display": f"{hardest:.4f}(최난도) / {mean:.4f}(평균), negative n={n}",
        "anchor": anchor_id,
        "anchor_stratum": anchor_stratum,
        "anchor_expected_score": anchor_score,
        "hardest_negative": hardest_id,
        "hardest_negative_top_score": negative_top_scores[hardest_id],
        "negative_mean_top_score": round(statistics.fmean(usable), 4),
        "negative_filter_policy": policy,
        # 필터에 후보가 하나도 남지 않은 negative는 간격 계산에서 빠진다.
        # 빠진 사실 자체가 관측치이므로 적는다 — n만 줄어들면 "간격이 커졌다"로 오독된다.
        "negatives_with_no_survivor": [
            k for k, v in negative_top_scores.items() if v is None
        ],
    }


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except IncompleteRetrieval as exc:
        # 종료 코드 3 = "측정하지 못했다". 0(깨끗함)·1(오류)과 구별한다 —
        # 부분 결과를 기록하고 0으로 끝내면 그 JSON이 baseline이 된다.
        print(f"\n⛔ 측정 중단: {exc}", file=sys.stderr)
        raise SystemExit(3) from exc
