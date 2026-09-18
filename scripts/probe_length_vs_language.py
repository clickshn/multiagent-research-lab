"""§3의 밀어내기 비대칭이 **언어** 때문인지 **길이** 때문인지 분해한다 (읽기 전용).

session-12 baseline에서 신규 21건이 기대 문서를 밀어낸 횟수가 **한국어 11 : 영어 1**이었다.
그런데 유입된 한국어 문서는 전부 GeekNews 단문(166~228자)이고 v1.0 코퍼스는 전부
장문(1192~1887자)이라, **"한국어"와 "짧다"가 같은 문서 집합에 겹쳐 있다.**
이 스크립트는 그 둘을 가른다.

**가를 수 있는 이유 — 길이 통제 대조군이 이미 코퍼스 안에 있다.**
신규 영어 문서 11건은 길이가 두 덩어리로 갈린다: OpenAI News 단문 3건(188~203자)과
arXiv 장문 8건(1364~1983자). 앞쪽은 **한국어 단문과 길이대가 겹친다.**
따라서 한국어 질의에 대해

    new-ko (166~228자) vs new-en-short (188~203자)

를 비교하면 **길이가 통제된 상태에서 언어만 다른 대조**가 된다.

⚠️ **반대 방향 대조군은 없다.** 한국어 장문이 코퍼스에 0건이라
"한국어 안에서 길이를 바꾸는" 비교는 불가능하다. 그 사실 자체를 결과로 출력한다.

**LLM 호출 0건.** 인덱스에 이미 들어 있는 벡터·메타데이터와 로컬 임베딩만 쓴다.
검색 코드는 건드리지 않는다 — `ChromaRetriever`를 있는 그대로 호출한다.

실행:
    python scripts/probe_length_vs_language.py
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.providers.config import load_vectorstore_settings  # noqa: E402
from src.providers.embeddings import get_embedding_provider  # noqa: E402
from src.tools.retrieval import ChromaRetriever  # noqa: E402

GOLDEN_SET = REPO_ROOT / "docs" / "eval" / "golden-set.json"
GOLDEN_SET_EN = REPO_ROOT / "docs" / "eval" / "golden-set-en.json"
OUT_PATH = REPO_ROOT / "docs" / "eval" / "probe-length-vs-language.json"

# 단문/장문 경계. 실측 분포가 188~228자와 1192~1983자로 완전히 갈라져 있어
# 그 사이 아무 값이나 같은 결과를 준다 — 임계값 선택이 결론을 만들지 않는다.
SHORT_CHARS = 400


def _group(meta: dict, emb_chars: int) -> str:
    """문서를 (출신 × 길이) 네 군으로 나눈다.

    v1.0은 전부 장문이라 길이로 다시 쪼갤 것이 없다 — 이것도 관측 결과다.
    """
    if meta.get("export_id") is None or meta.get("merged_with_export"):
        return "v1.0-long"
    lang = meta.get("lang") or "?"
    if lang != "en":
        return f"new-{lang}"
    return "new-en-short" if emb_chars < SHORT_CHARS else "new-en-long"


def main() -> int:
    import numpy as np

    settings = load_vectorstore_settings()
    import chromadb

    collection = chromadb.PersistentClient(path=str(settings.persist_dir)).get_collection(
        settings.collection
    )
    raw = collection.get(include=["metadatas", "documents", "embeddings"])

    docs: dict[str, dict] = {}
    for meta, text, vector in zip(
        raw["metadatas"], raw["documents"], raw["embeddings"], strict=False
    ):
        # 인덱싱된 실제 임베딩 입력은 `title + "\n\n" + text`다 (`ChromaRetriever.index`).
        # 본문 길이(`text_chars`)가 아니라 이 값을 써야 길이 가설을 옳게 검증한다.
        emb_input = f"{meta.get('title', '')}\n\n{text or ''}".strip()
        docs[meta["doc_id"]] = {
            "group": _group(meta, len(emb_input)),
            "chars": len(emb_input),
            "norm": float(np.linalg.norm(vector)),
            "title": meta.get("title", ""),
        }

    groups = ["v1.0-long", "new-en-long", "new-en-short", "new-ko"]
    sizes = {g: sum(1 for d in docs.values() if d["group"] == g) for g in groups}

    golden = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
    en_queries = json.loads(GOLDEN_SET_EN.read_text(encoding="utf-8"))["queries"]
    cases = golden["cases"]

    embeddings = get_embedding_provider()
    embeddings.embed_query("워밍업")  # 콜드 로딩을 측정 밖으로 뺀다
    retriever = ChromaRetriever(embeddings=embeddings)
    corpus_size = retriever.count()

    result: dict = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "corpus_size": corpus_size,
        "llm_calls": 0,
        "group_sizes": sizes,
        "length_by_group": {
            g: {
                "min": min(d["chars"] for d in docs.values() if d["group"] == g),
                "median": int(
                    statistics.median(d["chars"] for d in docs.values() if d["group"] == g)
                ),
                "max": max(d["chars"] for d in docs.values() if d["group"] == g),
            }
            for g in groups
            if sizes[g]
        },
        # 임베딩이 정규화돼 들어가므로(`normalize_embeddings=True`) 노름은 전부 1.0이다.
        # "노름이 커서 유사도가 높다"는 가설은 구조상 성립할 수 없다 — 변수가 아니다.
        "vector_norm": {
            "min": round(min(d["norm"] for d in docs.values()), 6),
            "max": round(max(d["norm"] for d in docs.values()), 6),
        },
        "korean_long_docs": sum(
            1 for d in docs.values() if d["group"] == "new-ko" and d["chars"] >= SHORT_CHARS
        ),
        "v10_short_docs": sum(
            1 for d in docs.values() if d["group"] == "v1.0-long" and d["chars"] < SHORT_CHARS
        ),
        "by_query_lang": {},
    }

    for qlang, queries in (
        ("ko", {c["id"]: c["query"] for c in cases}),
        ("en", en_queries),
    ):
        ranks: dict[str, list[int]] = {g: [] for g in groups}
        scores: dict[str, list[float]] = {g: [] for g in groups}
        displaced: dict[str, int] = {g: 0 for g in groups}
        short_en_ranks: dict[str, list[int]] = {}
        all_chars: list[float] = []
        all_scores: list[float] = []
        en_chars: list[float] = []
        en_scores: list[float] = []

        for case in cases:
            case_id = case["id"]
            hits = retriever.search(queries[case_id], k=corpus_size)
            ids = [h.doc_id for h in hits]
            for rank, hit in enumerate(hits, start=1):
                info = docs[hit.doc_id]
                ranks[info["group"]].append(rank)
                scores[info["group"]].append(hit.score)
                all_chars.append(info["chars"])
                all_scores.append(hit.score)
                if info["group"].startswith("new-en"):
                    # 언어를 영어로 고정하고 길이만 변수로 둔 상관
                    en_chars.append(info["chars"])
                    en_scores.append(hit.score)
            short_en_ranks[case_id] = sorted(
                ids.index(d) + 1 for d, i in docs.items() if i["group"] == "new-en-short"
            )
            if not case["expect"]["expect_uncovered"]:
                best = min(
                    ids.index(e) for e in case["expect"]["expected_doc_ids"] if e in ids
                )
                for doc_id in ids[:best]:
                    displaced[docs[doc_id]["group"]] += 1

        entry = {
            "per_group": {
                g: {
                    "n_docs": sizes[g],
                    "mean_rank": round(statistics.fmean(ranks[g]), 2),
                    "mean_score": round(statistics.fmean(scores[g]), 4),
                    "max_score": round(max(scores[g]), 4),
                    # positive 7건에서 기대 문서보다 위에 선 횟수
                    "displacements": displaced[g],
                }
                for g in groups
                if sizes[g]
            },
            "pearson_chars_vs_score_all": round(
                float(np.corrcoef(all_chars, all_scores)[0, 1]), 3
            ),
            "pearson_chars_vs_score_english_only": round(
                float(np.corrcoef(en_chars, en_scores)[0, 1]), 3
            ),
            "new_en_short_ranks_by_case": short_en_ranks,
        }
        if qlang == "ko":
            ko_mean = entry["per_group"]["new-ko"]["mean_score"]
            en_short_mean = entry["per_group"]["new-en-short"]["mean_score"]
            entry["length_controlled_contrast"] = {
                "note": "new-ko(166~228자) vs new-en-short(188~203자) — 길이대가 겹친다",
                "new_ko_mean_score": ko_mean,
                "new_en_short_mean_score": en_short_mean,
                "delta_attributable_to_language": round(ko_mean - en_short_mean, 4),
            }
        result["by_query_lang"][qlang] = entry

    OUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n→ {OUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
