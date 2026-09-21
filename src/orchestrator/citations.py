"""인용 출처 표 렌더링 (ADR-024).

보고서에 붙는 출처 표를 **코드가 만든다.** 모델에게 메타데이터를 보여주고 옮겨 적게
하지 않는다. 이유는 셋이고 전부 ADR-024에 있다. 요약하면:

1. **옮겨 적기는 틀릴 수 있다.** 모델이 `Community`를 `Paper`로 쓰면 감사 가능성이
   모델 정확도에 걸린다. ADR-006의 "셀 수 있는 것은 모델에게 묻지 않는다"와 같은 원칙이다.
2. **`release_type`을 Writer가 보면 근거 신뢰도 신호로 읽는다.** Researcher 프롬프트의
   유사도 점수 노출과 같은 함정이다 (session-13 §6.2, 아직 열려 있는 결정 항목).
3. **LLM 입력이 그대로면 `PROMPT_VERSION`이 바뀌지 않는다.** 그래야 "인용 형식 하나만
   바뀌었다"가 정확히 참이 되고, 전/후 비교가 성립한다.

## 결측을 빈칸으로 두지 않는다

`release_type`이 비어 있는 이유는 **두 가지**이고 둘은 다른 사실이다.

| 상태 | 표기 | 뜻 |
|---|---|---|
| export를 거쳤고 값이 있다 | `Paper` | 확인된 값 |
| export를 거쳤는데 값이 비었다 | `없음` | 확인했고, 해당 값이 없다 |
| **export를 거치지 않았다** | **`미확인`** | **확인 자체가 되지 않았다** |

셋째 줄이 ADR-022의 **층 B**다 — 스냅샷으로만 들어온 arXiv 14건(코퍼스 37건 중 37.8%).
빈칸 하나로 합치면 독자가 "이 문서는 등급이 없는 문서"와 "등급을 아직 모르는 문서"를
구별할 수 없고, 그 구별이 정확히 ADR-022가 층을 나눈 이유다.

**발행일은 두 층 모두 있다.** 스냅샷에도 `published`가 실려 있기 때문이다. 즉 결측은
"이 문서에 대해 아는 게 없다"가 아니라 **"온톨로지 라벨링을 거치지 않았다"**는 뜻이며,
표기도 그 범위에서만 `미확인`이 된다.
"""

from __future__ import annotations

from collections.abc import Sequence

from .state import Citation

UNVERIFIED = "미확인"
"""온톨로지 export를 거치지 않은 문서 (ADR-022 층 B). '값 없음'과 다르다."""

ABSENT = "없음"
"""export는 거쳤는데 값이 비어 있는 경우. 확인한 결과 해당 없음."""

SOURCE_TABLE_HEADING = "## 출처"

_FOOTNOTE = (
    f"> `{UNVERIFIED}`은 **온톨로지 export를 거치지 않은 문서**라는 뜻이다 "
    f"(ADR-022 층 B — 스냅샷 arXiv). `{ABSENT}`(값이 실제로 비어 있음)과 다르다.\n"
    "> 발행일은 스냅샷에도 있으므로 두 경우 모두 표시된다.\n"
    "> 이 표는 코드가 검색 결과에서 그대로 렌더링한 것이며 모델을 거치지 않는다 (ADR-024)."
)


def _field(value: str, *, has_ontology: bool) -> str:
    if not has_ontology:
        return UNVERIFIED
    return value.strip() or ABSENT


def _domains(citation: Citation) -> str:
    if not citation.has_ontology:
        return UNVERIFIED
    return ", ".join(citation.tech_domains) or ABSENT


def dedupe(citations: Sequence[Citation]) -> list[Citation]:
    """같은 (doc_id, locator)는 한 줄로 모은다. 등장 순서는 유지한다.

    한 문서가 여러 조사 항목의 근거가 되는 일이 흔하다. 출처 표는 "무엇을 근거로
    썼는가"의 목록이지 근거가 몇 번 쓰였는지의 집계가 아니다.
    """
    seen: set[tuple[str, str]] = set()
    unique: list[Citation] = []
    for citation in citations:
        key = (citation.doc_id, citation.locator)
        if key in seen:
            continue
        seen.add(key)
        unique.append(citation)
    return unique


def render_source_table(citations: Sequence[Citation]) -> str:
    """출처 표를 마크다운으로 만든다. 인용이 없으면 빈 문자열이다.

    빈 표를 붙이지 않는 이유: 근거가 하나도 없는 보고서에 출처 절이 붙어 있으면
    "출처가 있는 보고서"처럼 보인다. 없으면 없는 대로 둔다.
    """
    rows = dedupe(citations)
    if not rows:
        return ""

    lines = [
        SOURCE_TABLE_HEADING,
        "",
        "| # | doc_id | 위치 | 종류 | 기술 영역 | 발행일 |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    for index, citation in enumerate(rows, start=1):
        lines.append(
            f"| {index} | {citation.doc_id} | {citation.locator} | "
            f"{_field(citation.release_type, has_ontology=citation.has_ontology)} | "
            f"{_domains(citation)} | "
            # 발행일은 층과 무관하게 스냅샷에 있다. 비어 있으면 그것만 '없음'이다.
            f"{citation.published.strip() or ABSENT} |"
        )
    lines.extend(["", _FOOTNOTE])
    return "\n".join(lines)


def attach_source_table(draft: str, citations: Sequence[Citation]) -> str:
    """초안 뒤에 출처 표를 붙인다. 본문은 한 글자도 바꾸지 않는다."""
    table = render_source_table(citations)
    if not table:
        return draft
    return f"{draft.rstrip()}\n\n{table}\n"
