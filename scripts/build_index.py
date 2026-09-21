"""공개 코퍼스를 Chroma 인덱스로 만든다 (ADR-005).

`data/corpus/`의 스냅샷을 읽어 로컬 임베딩으로 벡터화하고 `var/chroma/`에 넣는다.
인덱스는 커밋하지 않는다(`var/`는 .gitignore 대상) — 스냅샷에서 언제든 다시 만들 수
있고, 재현의 기준은 인덱스가 아니라 스냅샷 + 임베딩 모델 이름이기 때문이다.

**중복 upsert 방어 (ADR-005 Amendment 5).** session-15가 규명한 문서 유실의 방아쇠는
`--reset` 없이 이 스크립트를 두 번 돌려 `embeddings_queue`에 문서당 2행이 쌓이는 것이다.
그래서 두 겹으로 막는다:

1. **거부(사전)** — 이미 인덱스에 있는 ID를 다시 넣으려 하면 **쓰기 전에** 멈춘다.
   중복 로그가 애초에 쌓이지 않는다. 코퍼스가 자라서 *새 문서만* 넣고 싶으면
   `--only-new`로 명시한다.
2. **사후 검사** — 빌드 직후 쓰기 로그 행 수 == 인덱스 문서 수를 검사한다.
   1이 못 보는 것(이미 더러워진 로그, 이 스크립트를 거치지 않은 쓰기)을 잡는다.
   **인덱싱을 건너뛴 실행에서도 돈다** — 검사는 이번 쓰기가 아니라 인덱스 상태를 본다.

유실은 `count()`에도 `get()`에도 나타나지 않는다. 지금 잡지 않으면 조용히 통과한다.

실행: `python scripts/build_index.py [--reset | --only-new]` (레포 루트에서)

종료 코드: 0 정상 · 1 코퍼스 없음 · 2 중복 upsert 거부 · 3 무결성 검사 실패
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.providers.config import load_embedding_settings, load_vectorstore_settings  # noqa: E402
from src.tools.corpus import CorpusDoc, load_corpus, summarize  # noqa: E402
from src.tools.index_guard import (  # noqa: E402
    IndexIntegrityError,
    assert_one_row_per_doc,
    indexed_ids,
    read_write_log,
    sqlite_path,
)
from src.tools.retrieval import ChromaRetriever  # noqa: E402

EXIT_NO_CORPUS = 1
EXIT_DUPLICATE_UPSERT = 2
EXIT_INTEGRITY = 3


def index_id(doc: CorpusDoc) -> str:
    """인덱스에 실리는 ID. `ChromaRetriever.index()`가 만드는 것과 같은 형태여야 한다.

    ⚠️ **두 곳에 같은 규칙이 있다.** 여기서 만든 ID로 "이미 들어 있는가"를 판정하므로,
    검색 계층이 ID 형태를 바꾸면 이 방어가 조용히 무력해진다(겹치는 ID가 하나도 없는
    것처럼 보인다). 그 사고를 막으려고 `tests/test_index_guard.py`가 실제 인덱싱 후
    양쪽이 일치하는지 대조한다 — 규칙이 갈리면 테스트가 깨진다.
    """
    return f"{doc.source}:{doc.doc_id}:{doc.locator}"


def _plan(
    docs: Sequence[CorpusDoc], existing: set[str], *, only_new: bool
) -> tuple[list[CorpusDoc], list[str]]:
    """넣을 문서와 겹치는 ID를 가른다."""
    overlap = [index_id(d) for d in docs if index_id(d) in existing]
    if not only_new:
        return list(docs), overlap
    return [d for d in docs if index_id(d) not in existing], overlap


def main() -> int:
    parser = argparse.ArgumentParser(description="Chroma 인덱스 생성 (ADR-005)")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="기존 컬렉션을 지우고 다시 만든다 (임베딩 모델을 바꿨을 때 필요)",
    )
    parser.add_argument(
        "--only-new",
        action="store_true",
        help="이미 인덱스에 있는 ID는 건너뛰고 새 문서만 넣는다 (코퍼스 증분 추가)",
    )
    parser.add_argument("--corpus-dir", type=Path, default=None)
    args = parser.parse_args()

    if args.reset and args.only_new:
        print("--reset과 --only-new는 함께 쓸 수 없습니다 (전체 재생성 vs 증분 추가).")
        return EXIT_DUPLICATE_UPSERT

    embedding_settings = load_embedding_settings()
    store_settings = load_vectorstore_settings()
    persist_dir = store_settings.persist_dir
    collection = store_settings.collection

    print("임베딩 모델:", embedding_settings.model_name, f"(device={embedding_settings.device})")
    print("인덱스 경로 :", persist_dir)
    print("컬렉션      :", collection)

    docs = load_corpus(args.corpus_dir)
    print("\n코퍼스:", summarize(docs), f"= 총 {len(docs)}건")
    if not docs:
        print("코퍼스가 비어 있습니다. `python scripts/ingest_corpus.py`를 먼저 실행하세요.")
        return EXIT_NO_CORPUS

    # --- 사전 검사: 중복 upsert를 쓰기 전에 막는다 -------------------------
    fresh = not sqlite_path(persist_dir).exists()
    try:
        existing = set() if fresh else indexed_ids(persist_dir, collection)
        before = None if fresh else read_write_log(persist_dir, collection)
    except IndexIntegrityError as exc:
        print(f"\n인덱스 상태를 읽을 수 없습니다: {exc}")
        return EXIT_INTEGRITY

    if before is not None and not before.clean and not args.reset:
        # 이번 실행이 만든 것이 아니라 이미 들어 있던 중복이다. 여기에 더 얹지 않는다.
        # `--reset`은 예외다 — 컬렉션째 지우므로 더러운 로그도 함께 사라진다.
        # 이것이 우리가 안내하는 유일한 복구 경로이므로 여기서 막으면 막다른 골목이 된다.
        print(f"\n기존 쓰기 로그에 이미 중복이 있습니다 [{before.describe()}].")
        print("`--reset`으로 인덱스를 다시 만드세요 (ADR-005 Amendment 4).")
        return EXIT_INTEGRITY

    to_index, overlap = _plan(docs, existing, only_new=args.only_new)

    if overlap and not args.reset and not args.only_new:
        print(f"\n이미 인덱스에 있는 ID {len(overlap)}건을 다시 넣으려 합니다.")
        print(f"  예: {', '.join(overlap[:3])}")
        print(
            "중복 upsert는 `embeddings_queue`에 문서당 2행을 만들고, 그 로그를 재생하는\n"
            "과정에서 문서가 검색되지 않게 된다 (ADR-005 Amendment 4). `count()`에는\n"
            "나타나지 않으므로 조용히 통과시키지 않는다."
        )
        print("\n  전체 재생성: python scripts/build_index.py --reset")
        print("  새 문서만  : python scripts/build_index.py --only-new")
        return EXIT_DUPLICATE_UPSERT

    retriever = ChromaRetriever()
    if args.reset:
        print("\n기존 컬렉션 삭제 중...")
        retriever.reset()
        to_index = list(docs)

    if to_index:
        print("\n임베딩 + 인덱싱 중... (모델 첫 로딩에 수십 초 걸릴 수 있습니다)")
        started = time.perf_counter()
        indexed = retriever.index(to_index)
        elapsed = time.perf_counter() - started
        print(f"완료: {indexed}건 인덱싱, {elapsed:.1f}s")
    else:
        # --only-new인데 새 문서가 없다. 인덱싱은 건너뛰지만 검사는 돈다.
        print("\n새 문서가 없습니다 — 인덱싱을 건너뜁니다 (--only-new).")

    documents = retriever.count()
    print(f"컬렉션 문서 수: {documents}")
    print(f"임베딩 차원: {retriever.embeddings.dimension}")

    # --- 사후 검사: 문서당 1행 -------------------------------------------
    try:
        after = assert_one_row_per_doc(read_write_log(persist_dir, collection), documents=documents)
    except IndexIntegrityError as exc:
        print(f"\n무결성 검사 실패: {exc}")
        return EXIT_INTEGRITY
    print(f"쓰기 로그 검사: OK ({after.describe()})")

    print("\n다음: python scripts/run_research.py \"<리서치 질의>\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
