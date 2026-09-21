"""중복 upsert 방어 (ADR-005 Amendment 5) — session-15 재현을 테스트로 고정한다.

session-15가 규명한 것: `build_index.py`를 `--reset` 없이 두 번 돌리면
`embeddings_queue`에 문서당 2행이 쌓이고, 그 로그를 재생하는 과정에서 문서 1건이
검색되지 않게 된다. **`count()`와 `get()`은 정상값을 낸다** — 그래서 개수로는
알 수 없고, 그래서 테스트로 박아둔다.

여기서 고정하는 것은 두 가지다.

1. **같은 절차를 다시 밟으면 이제는 실패한다** (`test_second_build_without_reset_is_refused`).
   session-15 §6의 "결함 재현 절차"가 그대로 재현되지 않는다는 것을 확인한다.
2. **더러워진 로그를 검사기가 실제로 잡는다** (`test_duplicate_upsert_makes_log_dirty`).
   "0건 검출"은 검사기가 고장 나도 똑같이 나오는 결과이기 때문에, 양성 대조를 먼저
   만들어 둔다 — `scripts/scan_local_secrets.py`와 같은 원칙이다.

임베딩 모델은 띄우지 않는다. 결정적인 가짜 임베딩을 주입한다 — 관측 대상은 벡터의
품질이 아니라 **쓰기 로그에 몇 행이 쌓였느냐**다 (session-15 프로브와 같은 이유).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import build_index  # noqa: E402
from src.tools.corpus import CorpusDoc  # noqa: E402
from src.tools.index_guard import (  # noqa: E402
    IndexIntegrityError,
    assert_one_row_per_doc,
    find_collection_id,
    indexed_ids,
    read_write_log,
)
from src.tools.retrieval import ChromaRetriever  # noqa: E402

_DIM = 8


class FakeEmbeddings:
    """결정적인 가짜 임베딩. 모델을 띄우지 않기 위한 것이다."""

    model_name = "fake/test-embeddings"

    @property
    def dimension(self) -> int:
        return _DIM

    def _vector(self, text: str) -> list[float]:
        seed = sum(ord(c) for c in text) or 1
        return [((seed * (i + 1)) % 97) / 97.0 for i in range(_DIM)]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def _doc(n: int) -> CorpusDoc:
    return CorpusDoc(
        doc_id=f"arXiv:0000.{n:04d}v1",
        source="arxiv",
        title=f"문서 {n}",
        text=f"문서 {n}의 초록 본문.",
        locator="abstract",
        url=f"https://arxiv.org/abs/0000.{n:04d}v1",
        published="2026-01-01",
    )


def _write_corpus(corpus_dir: Path, count: int) -> list[CorpusDoc]:
    docs = [_doc(n) for n in range(1, count + 1)]
    source_dir = corpus_dir / "arxiv"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "mini.json").write_text(
        json.dumps(
            [
                {
                    "doc_id": d.doc_id,
                    "title": d.title,
                    "text": d.text,
                    "locator": d.locator,
                    "url": d.url,
                    "published": d.published,
                }
                for d in docs
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return docs


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """격리된 인덱스 + 코퍼스. `build_index.main()`을 그대로 부를 수 있게 한다."""
    chroma_dir = tmp_path / "chroma"
    corpus_dir = tmp_path / "corpus"
    monkeypatch.setenv("CHROMA_DIR", str(chroma_dir))
    monkeypatch.setenv("CHROMA_COLLECTION", "guard_test")
    monkeypatch.setattr(
        build_index, "ChromaRetriever", lambda: ChromaRetriever(embeddings=FakeEmbeddings())
    )
    return chroma_dir, corpus_dir


def _run(corpus_dir: Path, *flags: str) -> int:
    argv = ["build_index.py", "--corpus-dir", str(corpus_dir), *flags]
    saved, sys.argv = sys.argv, argv
    try:
        return build_index.main()
    finally:
        sys.argv = saved


def _queue_rows(chroma_dir: Path) -> int:
    con = sqlite3.connect(str(chroma_dir / "chroma.sqlite3"))
    try:
        return int(con.execute("SELECT COUNT(*) FROM embeddings_queue").fetchone()[0])
    finally:
        con.close()


# --- 재현: 같은 절차를 다시 밟는다 ------------------------------------------


def test_second_build_without_reset_is_refused(lab) -> None:
    """session-15 §6의 결함 재현 절차가 더 이상 재현되지 않는다.

    핵심은 종료 코드만이 아니다 — **쓰기 로그가 그대로**여야 한다. 거부가 쓰기
    뒤에 일어나면 "감지했지만 이미 망가뜨렸다"가 되고, 그건 방어가 아니다.
    """
    chroma_dir, corpus_dir = lab
    _write_corpus(corpus_dir, 3)

    assert _run(corpus_dir) == 0
    assert _queue_rows(chroma_dir) == 3

    assert _run(corpus_dir) == build_index.EXIT_DUPLICATE_UPSERT
    assert _queue_rows(chroma_dir) == 3, "거부는 쓰기 전에 일어나야 한다"


def test_reset_rebuilds_without_duplicating(lab) -> None:
    """`--reset`은 컬렉션째 지우므로 로그도 함께 사라진다 — 문서당 1행이 유지된다."""
    chroma_dir, corpus_dir = lab
    _write_corpus(corpus_dir, 3)

    assert _run(corpus_dir) == 0
    assert _run(corpus_dir, "--reset") == 0
    assert _queue_rows(chroma_dir) == 3


def test_only_new_adds_without_duplicating(lab) -> None:
    """코퍼스 증분 추가 경로. 거부가 정상적인 성장 경로를 막지 않는지 본다."""
    chroma_dir, corpus_dir = lab
    _write_corpus(corpus_dir, 3)
    assert _run(corpus_dir) == 0

    _write_corpus(corpus_dir, 5)  # 2건 추가
    assert _run(corpus_dir, "--only-new") == 0
    assert _queue_rows(chroma_dir) == 5

    # 새 문서가 없는 --only-new는 아무것도 쓰지 않고 통과한다.
    assert _run(corpus_dir, "--only-new") == 0
    assert _queue_rows(chroma_dir) == 5


def test_reset_and_only_new_are_mutually_exclusive(lab) -> None:
    _chroma_dir, corpus_dir = lab
    _write_corpus(corpus_dir, 2)
    assert _run(corpus_dir, "--reset", "--only-new") == build_index.EXIT_DUPLICATE_UPSERT


# --- 양성 대조: 더러운 로그를 실제로 잡는가 ---------------------------------


def _dirty(docs: list[CorpusDoc]) -> None:
    """방어를 우회해 session-15의 결함 상태(문서당 2행)를 만든다.

    `build_index.py`를 거치지 않고 검색 계층에 직접 두 번 넣는다 — 구 인덱스가
    그렇게 만들어졌다 (ADR-005 Amendment 4).
    """
    for _ in range(2):
        ChromaRetriever(embeddings=FakeEmbeddings()).index(docs)


def test_duplicate_upsert_makes_log_dirty(lab) -> None:
    """검사기의 양성 대조. 문서당 2행이 되면 `clean`이 False여야 한다."""
    chroma_dir, corpus_dir = lab
    docs = _write_corpus(corpus_dir, 3)
    _dirty(docs)

    log = read_write_log(chroma_dir, "guard_test")
    assert log is not None
    assert log.rows == 6 and log.distinct_ids == 3
    assert not log.clean
    assert len(log.duplicated_ids) == 3

    with pytest.raises(IndexIntegrityError, match="문서당 1행"):
        assert_one_row_per_doc(log, documents=3)


def test_dirty_log_blocks_further_builds(lab) -> None:
    """이미 더러워진 인덱스 위에 더 얹지 않는다. `--only-new`여도 멈춘다."""
    chroma_dir, corpus_dir = lab
    docs = _write_corpus(corpus_dir, 3)
    _dirty(docs)

    assert _run(corpus_dir, "--only-new") == build_index.EXIT_INTEGRITY
    assert _queue_rows(chroma_dir) == 6, "실패 경로도 쓰기를 남기지 않는다"

    # 안내한 복구 경로가 실제로 동작하는지까지 확인한다.
    assert _run(corpus_dir, "--reset") == 0
    assert _queue_rows(chroma_dir) == 3


def test_clean_build_passes_the_check(lab) -> None:
    chroma_dir, corpus_dir = lab
    _write_corpus(corpus_dir, 4)
    assert _run(corpus_dir) == 0

    log = read_write_log(chroma_dir, "guard_test")
    assert log is not None and log.clean
    assert assert_one_row_per_doc(log, documents=4) is log


# --- ID 규칙이 검색 계층과 갈리지 않는지 -------------------------------------


def test_index_id_matches_retrieval_layer(lab) -> None:
    """`build_index.index_id()`와 `ChromaRetriever.index()`의 ID 규칙이 같아야 한다.

    갈리면 "겹치는 ID가 하나도 없다"로 보여 거부가 조용히 무력해진다.
    """
    chroma_dir, corpus_dir = lab
    docs = _write_corpus(corpus_dir, 3)
    assert _run(corpus_dir) == 0

    assert indexed_ids(chroma_dir, "guard_test") == {build_index.index_id(d) for d in docs}


# --- 검사 자체가 불가능하면 통과시키지 않는다 --------------------------------


def test_missing_collection_reads_as_absent(lab) -> None:
    """인덱스는 있는데 그 이름의 컬렉션이 없으면 None이다 (중복도 없다)."""
    chroma_dir, corpus_dir = lab
    _write_corpus(corpus_dir, 2)
    assert _run(corpus_dir) == 0

    assert find_collection_id(chroma_dir, "does_not_exist") is None
    assert read_write_log(chroma_dir, "does_not_exist") is None
    assert indexed_ids(chroma_dir, "does_not_exist") == set()


def test_unknown_layout_fails_loudly(tmp_path) -> None:
    """Chroma 내부 레이아웃이 바뀌면 '이상 없음'이 아니라 예외다 (ADR-021의 교훈)."""
    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir()
    sqlite3.connect(str(chroma_dir / "chroma.sqlite3")).close()

    with pytest.raises(IndexIntegrityError, match="레이아웃"):
        find_collection_id(chroma_dir, "guard_test")


def test_missing_index_fails_loudly(tmp_path) -> None:
    with pytest.raises(IndexIntegrityError, match="인덱스 DB가 없습니다"):
        read_write_log(tmp_path / "nope", "guard_test")


def test_absent_log_is_not_a_pass() -> None:
    """빌드 직후인데 컬렉션이 없으면 통과가 아니라 실패다."""
    with pytest.raises(IndexIntegrityError):
        assert_one_row_per_doc(None, documents=0)
