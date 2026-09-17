"""샘플 코퍼스 로딩과 반입 범위 정책 (ADR-004).

**이 프로젝트에는 실제 사내 기밀 문서를 넣지 않는다.** 현재 서빙 엔드포인트는
무인증 공개 접근이라(README "보안 / 운영"), 실제 사내 문서를 질의에 태우는 것은
회사 보안 정책 검토가 별도로 필요한 문제이고 이 포트폴리오 프로젝트의 범위를
벗어난다. 따라서 샘플 코퍼스는 공개 자료로만 구성한다.

정책을 문서에만 적어두면 다음 세션에서 무심코 깨진다. 그래서 허용 출처를
`ALLOWED_SOURCES`로 코드에 박고, 인덱싱 경로가 이 목록을 강제한다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

# Chroma 메타데이터가 받는 타입. 리스트·None은 여기 들어올 수 없다 —
# 평탄화는 `contract_import.ontology_metadata()`가 한다.
Metadata = dict[str, "str | int | float | bool"]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS_DIR = REPO_ROOT / "data" / "corpus"

# 인덱싱을 허용하는 출처. 전부 공개 자료다 (ADR-004).
#   arxiv — arXiv 논문 메타데이터·초록 (공개 API)
#   news  — 공개 AI/기술 뉴스 기사 메타데이터·요약
# 여기에 출처를 추가하려면 ADR-004를 먼저 갱신한다. 사내 문서 출처를 추가하는 것은
# 코드 변경이 아니라 보안 정책 결정이다.
ALLOWED_SOURCES: tuple[str, ...] = ("arxiv", "news")


class CorpusScopeError(RuntimeError):
    """허용되지 않은 출처를 반입·검색하려 한 경우 (ADR-004 위반)."""


def assert_allowed_source(source: str) -> str:
    """출처가 공개 자료 화이트리스트 안에 있는지 확인한다.

    조용히 건너뛰지 않고 예외를 던진다 — 범위를 벗어난 문서가 인덱스에 섞이는 것보다
    인덱싱이 실패하는 편이 낫기 때문이다.
    """
    if source not in ALLOWED_SOURCES:
        raise CorpusScopeError(
            f"허용되지 않은 코퍼스 출처: {source!r}. "
            f"허용 목록: {', '.join(ALLOWED_SOURCES)} (ADR-004). "
            "사내 문서 등 비공개 자료는 이 프로젝트에 반입하지 않는다."
        )
    return source


@dataclass(frozen=True)
class CorpusDoc:
    """인덱싱 단위 문서 1건.

    `doc_id`와 `locator`는 출처 제시 요구(problem-statement.md §3 목표 2)를 위해
    항상 함께 저장한다. 검색 결과에 둘 다 실리지 않으면 이 프로젝트의 핵심 목표가
    무너지므로, 선택 필드가 아니라 필수 필드다.
    """

    doc_id: str
    source: str
    title: str
    text: str
    locator: str = "abstract"
    url: str = ""
    published: str = ""
    extra: Metadata = field(default_factory=dict)

    def as_metadata(self) -> Metadata:
        """Chroma 메타데이터로 실을 형태 (스칼라만 허용된다).

        **`extra`를 버리지 않는다.** session-09까지는 위 6키만 싣고 `extra`를 조용히
        떨어뜨렸다 — 그래서 온톨로지 필드(계약 §3.2)를 실을 자리가 없었다. 지금은
        `extra`의 스칼라를 그대로 얹는다.

        인용에 필요한 6키는 **덮어쓸 수 없다.** `extra`가 `doc_id`나 `locator`를
        바꿀 수 있으면 출처 표기가 문서마다 다른 규칙으로 만들어질 수 있고,
        그것은 이 프로젝트의 1순위 목표(출처 제시)를 데이터 쪽에서 무너뜨린다.
        """
        metadata: Metadata = {
            key: value for key, value in self.extra.items() if value is not None
        }
        metadata.update(
            {
                "doc_id": self.doc_id,
                "source": self.source,
                "title": self.title,
                "locator": self.locator,
                "url": self.url,
                "published": self.published,
            }
        )
        return metadata


def _iter_records(path: Path) -> Iterator[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("documents", [])
    if not isinstance(payload, list):
        raise ValueError(f"코퍼스 파일 형식이 잘못됨: {path}")
    yield from payload


def load_snapshot(corpus_dir: Path | None = None) -> list[CorpusDoc]:
    """`data/corpus/<source>/*.json` — `ingest_corpus.py`가 만든 스냅샷만 읽는다.

    디렉터리 이름이 곧 출처이며, 화이트리스트에 없으면 예외를 던진다.
    """
    root = corpus_dir or DEFAULT_CORPUS_DIR
    if not root.exists():
        raise FileNotFoundError(
            f"코퍼스 디렉터리가 없습니다: {root}. "
            "`python scripts/ingest_corpus.py`로 먼저 수집하세요."
        )

    docs: list[CorpusDoc] = []
    for source_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        source = assert_allowed_source(source_dir.name)
        for json_file in sorted(source_dir.glob("*.json")):
            for record in _iter_records(json_file):
                docs.append(
                    CorpusDoc(
                        doc_id=str(record["doc_id"]),
                        source=source,
                        title=str(record.get("title", "")).strip(),
                        text=str(record.get("text", "")).strip(),
                        locator=str(record.get("locator", "abstract")),
                        url=str(record.get("url", "")),
                        published=str(record.get("published", "")),
                    )
                )
    return docs


def load_corpus_with_report(corpus_dir: Path | None = None):
    """스냅샷(JSON) + 출력 계약 export(JSONL)를 합쳐 읽고, 반입 결과도 함께 돌려준다.

    반환: `(docs, report)` — `report`는 `contract_import.ImportReport`.

    `contract_import`를 함수 안에서 import하는 이유는 순환 참조 때문이다:
    저쪽이 `CorpusDoc`을 쓰고 이쪽이 저쪽의 반입 규칙을 쓴다. 계약 검증 규칙을
    이 파일에 섞지 않으려면(여기는 ADR-004의 반입 *정책*이 사는 자리다)
    지연 import가 가장 정직한 분리다.
    """
    from .contract_import import merge_exports_into  # noqa: PLC0415 (순환 참조 회피)

    root = corpus_dir or DEFAULT_CORPUS_DIR
    snapshot = load_snapshot(root)
    return merge_exports_into(snapshot, root)


def load_corpus(corpus_dir: Path | None = None) -> list[CorpusDoc]:
    """인덱싱 대상 문서 전체. 스냅샷과 export를 구분하지 않는다.

    `build_index.py`가 부르는 함수다. 계약 export가 하나도 없으면 스냅샷만 돌려주므로
    session-09 이전과 동작이 같다.
    """
    docs, _ = load_corpus_with_report(corpus_dir)
    return docs


def summarize(docs: Iterable[CorpusDoc]) -> dict[str, int]:
    """출처별 문서 수. 인덱싱 로그와 핸드오프에 쓴다."""
    counts: dict[str, int] = {}
    for doc in docs:
        counts[doc.source] = counts.get(doc.source, 0) + 1
    return counts
