"""인덱스 쓰기 로그 무결성 검사 (ADR-005 Amendment 4 → Amendment 5).

session-15가 규명한 것: Chroma 문서 유실의 **방아쇠는 중복 upsert**다.
`build_index.py`를 `--reset` 없이 두 번 돌리면 `embeddings_queue`에 코퍼스 전건이
다시 쌓이고(문서당 2행), 벡터 세그먼트는 프로세스를 열 때마다 그 로그를 처음부터
재생하다가 가끔 한 노드를 도달 불가로 만든다. `count()`에도 `get()`에도 나타나지
않으므로 **개수로는 알 수 없다.**

그래서 이 모듈은 **검색 경로가 아니라 쓰기 경로**를 본다. 방어의 위치가 증상
(`retrieval.py`)이 아니라 방아쇠(`build_index.py`)인 이유는 ADR-005 Amendment 4에 있다.
요약하면 운영 검색은 `top_k=4`라 "몇 건이 와야 정상인가"를 말할 수 없지만,
**문서 수와 로그 행 수는 k와 무관하게 결정적으로 비교 가능**하다.

⚠️ **이 모듈은 Chroma의 sqlite 내부 레이아웃에 의존한다.** 공개 API에는 쓰기 로그를
볼 수단이 없다. 의존하는 것은 두 가지다.

- `embeddings_queue(topic, id, operation)` — 쓰기 로그. `topic`은
  `persistent://<tenant>/<database>/<collection_id>` 형태라 **컬렉션 id로 접미사 일치**를
  건다 (tenant/database 이름을 가정하지 않기 위해서다).
- `embeddings(segment_id, embedding_id)` + `segments(collection, scope)` — 현재 인덱싱된 ID.

레이아웃이 바뀌면 **조용히 통과시키지 않고 예외를 던진다.** 검사기가 고장 났는데
"이상 없음"이 나오는 것은 검사가 없는 것보다 나쁘다 — Session 0.5a에서 게이트가
통과했지만 게이트가 확인해야 할 것을 확인하지 않았던 것과 같은 실패 형태다 (ADR-021).
`scripts/scan_local_secrets.py`가 양성 대조를 먼저 도는 것도 같은 이유다.

고정 버전은 `requirements.txt`의 `chromadb` 핀이다.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

SQLITE_NAME = "chroma.sqlite3"
"""Chroma 임베디드 모드의 메타데이터 DB 파일 이름."""


class IndexIntegrityError(RuntimeError):
    """인덱스 쓰기 로그가 "문서당 1행" 불변식을 어겼거나, 검사 자체가 불가능한 경우.

    두 경우를 같은 예외로 묶는 이유: 호출부가 해야 할 일이 같기 때문이다 —
    **인덱싱을 진행시키지 않는다.** 검사 불가를 통과로 처리하면 방어가 사라진 것을
    아무도 모른다.
    """


@dataclass(frozen=True)
class WriteLog:
    """컬렉션 하나에 대한 `embeddings_queue` 요약."""

    collection_id: str
    topics: tuple[str, ...]
    rows: int
    distinct_ids: int
    duplicated_ids: tuple[str, ...]
    operations: dict[int, int]

    @property
    def clean(self) -> bool:
        """문서당 1행인가. 행 수와 서로 다른 ID 수가 같으면 중복이 없다는 뜻이다."""
        return self.rows == self.distinct_ids

    def describe(self) -> str:
        ops = ", ".join(f"op{op}={n}" for op, n in sorted(self.operations.items()))
        return f"rows={self.rows} distinct_ids={self.distinct_ids} ({ops or '비어 있음'})"


def sqlite_path(persist_dir: Path) -> Path:
    return persist_dir / SQLITE_NAME


def _connect(persist_dir: Path) -> sqlite3.Connection:
    """읽기 전용으로 연다.

    검사가 인덱스를 변형시키면 안 된다. session-15 §4.3에서 **질의가 인덱스 파일을
    수정한다**는 것이 확인됐으므로(측정이 측정 대상을 바꾼다), 검사 경로만큼은
    쓰기 가능성을 URI 수준에서 제거한다.
    """
    path = sqlite_path(persist_dir)
    if not path.exists():
        raise IndexIntegrityError(
            f"인덱스 DB가 없습니다: {path}. "
            "`python scripts/build_index.py`로 먼저 인덱스를 만드세요."
        )
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _assert_table(con: sqlite3.Connection, name: str) -> None:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    if row is None:
        raise IndexIntegrityError(
            f"Chroma 내부 테이블 {name!r}이 없습니다 — 저장 레이아웃이 바뀌었습니다. "
            "쓰기 로그 검사(ADR-005 Amendment 5)를 수행할 수 없으므로 진행하지 않습니다. "
            "`src/tools/index_guard.py`를 현재 chromadb 버전에 맞게 갱신하세요."
        )


def find_collection_id(persist_dir: Path, collection: str) -> str | None:
    """컬렉션 이름 → id. 없으면 None (아직 인덱스를 만들지 않은 상태)."""
    with closing(_connect(persist_dir)) as con:
        _assert_table(con, "collections")
        rows = con.execute("SELECT id FROM collections WHERE name=?", (collection,)).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        # 같은 이름이 둘이면 어느 쪽을 검사해야 하는지 결정할 수 없다.
        raise IndexIntegrityError(
            f"같은 이름의 컬렉션이 {len(rows)}개입니다: {collection!r}. "
            "어느 쪽을 검사할지 결정할 수 없으므로 진행하지 않습니다."
        )
    return str(rows[0][0])


def indexed_ids(persist_dir: Path, collection: str) -> set[str]:
    """현재 인덱스에 들어 있는 문서 ID 집합.

    ID는 `build_index`가 만드는 `"{source}:{doc_id}:{locator}"` 형태다.
    컬렉션이 없으면 빈 집합이다 — 아직 아무것도 넣지 않았다는 뜻이므로 중복도 없다.
    """
    collection_id = find_collection_id(persist_dir, collection)
    if collection_id is None:
        return set()
    with closing(_connect(persist_dir)) as con:
        _assert_table(con, "segments")
        _assert_table(con, "embeddings")
        rows = con.execute(
            """
            SELECT e.embedding_id
              FROM embeddings AS e
              JOIN segments AS s ON s.id = e.segment_id
             WHERE s.collection = ? AND s.scope = 'METADATA'
            """,
            (collection_id,),
        ).fetchall()
    return {str(r[0]) for r in rows}


def read_write_log(persist_dir: Path, collection: str) -> WriteLog | None:
    """컬렉션의 `embeddings_queue` 행을 요약한다. 컬렉션이 없으면 None."""
    collection_id = find_collection_id(persist_dir, collection)
    if collection_id is None:
        return None

    with closing(_connect(persist_dir)) as con:
        _assert_table(con, "embeddings_queue")
        # topic = persistent://<tenant>/<database>/<collection_id>.
        # tenant/database 이름을 가정하지 않으려고 접미사로만 맞춘다.
        rows = con.execute(
            "SELECT topic, id, operation FROM embeddings_queue WHERE topic LIKE ?",
            (f"%/{collection_id}",),
        ).fetchall()

    ids = Counter(str(r[1]) for r in rows)
    return WriteLog(
        collection_id=collection_id,
        topics=tuple(sorted({str(r[0]) for r in rows})),
        rows=len(rows),
        distinct_ids=len(ids),
        duplicated_ids=tuple(sorted(doc_id for doc_id, n in ids.items() if n > 1)),
        operations=dict(Counter(int(r[2]) for r in rows)),
    )


def assert_one_row_per_doc(log: WriteLog | None, *, documents: int) -> WriteLog:
    """**빌드 직후 검사.** 쓰기 로그 행 수 == 인덱스 문서 수여야 한다.

    `documents`는 `collection.count()` 값을 넣는다. `len(docs)`가 아니라 컬렉션의
    실제 문서 수인 이유: 증분 추가(`--only-new`)를 하면 이번에 넣은 건수와 컬렉션
    전체 건수가 다르고, 지켜야 할 불변식은 **컬렉션 전체에 대해 문서당 1행**이다.

    어긋나면 실패시킨다. 여기서 통과시키면 다음 프로세스부터 문서 1건이 조용히
    검색되지 않기 시작하고, 그 위에서 잰 수치는 전부 다시 만들어야 한다.
    """
    if log is None:
        raise IndexIntegrityError(
            "빌드 직후인데 컬렉션을 찾을 수 없습니다. 인덱싱이 실제로 일어나지 "
            "않았거나 저장 레이아웃이 바뀌었습니다."
        )
    if log.rows == documents and log.clean:
        return log

    reason = (
        f"쓰기 로그 {log.rows}행 ≠ 문서 {documents}건"
        if log.rows != documents
        else f"중복 ID {len(log.duplicated_ids)}종"
    )
    sample = ", ".join(log.duplicated_ids[:3])
    raise IndexIntegrityError(
        f"인덱스 쓰기 로그가 '문서당 1행'을 어겼습니다: {reason} "
        f"[{log.describe()}]"
        + (f" 중복 예: {sample}" if sample else "")
        + ". 중복 upsert는 문서 유실의 방아쇠다 (ADR-005 Amendment 4) — "
        "`count()`나 `get()`에는 나타나지 않으므로 지금 잡지 않으면 조용히 통과한다. "
        "`python scripts/build_index.py --reset`으로 인덱스를 다시 만드세요."
    )
