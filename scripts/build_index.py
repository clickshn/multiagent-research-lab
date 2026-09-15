"""공개 코퍼스를 Chroma 인덱스로 만든다 (ADR-005).

`data/corpus/`의 스냅샷을 읽어 로컬 임베딩으로 벡터화하고 `var/chroma/`에 넣는다.
인덱스는 커밋하지 않는다(`var/`는 .gitignore 대상) — 스냅샷에서 언제든 다시 만들 수
있고, 재현의 기준은 인덱스가 아니라 스냅샷 + 임베딩 모델 이름이기 때문이다.

실행: `python scripts/build_index.py [--reset]` (레포 루트에서)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.providers.config import load_embedding_settings, load_vectorstore_settings  # noqa: E402
from src.tools.corpus import load_corpus, summarize  # noqa: E402
from src.tools.retrieval import ChromaRetriever  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Chroma 인덱스 생성 (ADR-005)")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="기존 컬렉션을 지우고 다시 만든다 (임베딩 모델을 바꿨을 때 필요)",
    )
    parser.add_argument("--corpus-dir", type=Path, default=None)
    args = parser.parse_args()

    embedding_settings = load_embedding_settings()
    store_settings = load_vectorstore_settings()

    print("임베딩 모델:", embedding_settings.model_name, f"(device={embedding_settings.device})")
    print("인덱스 경로 :", store_settings.persist_dir)
    print("컬렉션      :", store_settings.collection)

    docs = load_corpus(args.corpus_dir)
    print("\n코퍼스:", summarize(docs), f"= 총 {len(docs)}건")
    if not docs:
        print("코퍼스가 비어 있습니다. `python scripts/ingest_corpus.py`를 먼저 실행하세요.")
        return 1

    retriever = ChromaRetriever()
    if args.reset:
        print("\n기존 컬렉션 삭제 중...")
        retriever.reset()

    print("\n임베딩 + 인덱싱 중... (모델 첫 로딩에 수십 초 걸릴 수 있습니다)")
    started = time.perf_counter()
    indexed = retriever.index(docs)
    elapsed = time.perf_counter() - started

    print(f"완료: {indexed}건 인덱싱, {elapsed:.1f}s")
    print(f"컬렉션 문서 수: {retriever.count()}")
    print(f"임베딩 차원: {retriever.embeddings.dimension}")
    print("\n다음: python scripts/run_research.py \"<리서치 질의>\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
