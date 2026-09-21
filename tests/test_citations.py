"""인용 온톨로지 메타 부착 (ADR-024, session-16).

여기서 붙드는 불변은 셋이다.

1. **결측이 빈칸으로 뭉개지지 않는다.** `미확인`(온톨로지 export를 거치지 않음)과
   `없음`(거쳤는데 값이 비었음)은 다른 사실이고, 표기도 달라야 한다 (ADR-022 층 B).
2. **메타가 LLM 입력에 새지 않는다.** 출처 표는 코드가 렌더링한다. 모델이 옮겨 적으면
   감사 가능성이 모델 정확도에 걸리고, `release_type`은 근거 신뢰도 신호로도 읽힌다.
3. **메타가 붙어도 모델이 받는 입력은 바이트 단위로 같다.** 그래야 `PROMPT_VERSION`이
   그대로이고 "인용 형식 하나만 바뀌었다"가 정확히 참이 된다.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.orchestrator import prompts  # noqa: E402
from src.orchestrator.citations import (  # noqa: E402
    ABSENT,
    UNVERIFIED,
    attach_source_table,
    dedupe,
    render_source_table,
)
from src.orchestrator.nodes import _format_evidence, _select_citations  # noqa: E402
from src.orchestrator.state import Citation  # noqa: E402
from src.tools.retrieval import RetrievedChunk, _to_chunks  # noqa: E402


def _cite(doc_id: str, **kwargs) -> Citation:
    base = {"locator": "abstract", "snippet": "본문 스니펫"}
    base.update(kwargs)
    return Citation(doc_id=doc_id, **base)


# --- 결측 표기 ---------------------------------------------------------------


def test_three_states_are_distinguishable() -> None:
    """확인된 값 / 확인했고 없음 / 확인 안 됨 — 셋이 서로 다르게 보여야 한다."""
    table = render_source_table(
        [
            _cite(
                "arXiv:A",
                published="2026-05-20",
                release_type="Paper",
                tech_domains=("Agent", "Eval/Governance"),
                has_ontology=True,
            ),
            # export는 거쳤는데 값이 비어 있다.
            _cite("arXiv:B", published="2025-01-02", has_ontology=True),
            # export 자체를 거치지 않았다 (층 B).
            _cite("arXiv:C", published="2024-01-14", has_ontology=False),
        ]
    )
    rows = [line for line in table.splitlines() if line.startswith("| ")]
    header, sep, row_a, row_b, row_c = rows

    assert "Paper" in row_a and "Agent, Eval/Governance" in row_a
    assert UNVERIFIED not in row_a

    assert ABSENT in row_b and UNVERIFIED not in row_b
    assert UNVERIFIED in row_c and ABSENT not in row_c

    # 발행일은 층과 무관하게 스냅샷에 있다 — 결측 표기가 발행일까지 먹지 않는다.
    assert "2024-01-14" in row_c


def test_footnote_explains_the_distinction() -> None:
    """표기만 다르고 뜻을 적어두지 않으면 읽는 사람이 구별하지 못한다."""
    table = render_source_table([_cite("arXiv:C", has_ontology=False)])
    assert UNVERIFIED in table
    assert "ADR-022" in table and "ADR-024" in table


def test_missing_published_is_absent_not_unverified() -> None:
    """발행일이 비면 그건 '없음'이다 — 온톨로지 결측과 다른 사실이다."""
    table = render_source_table([_cite("arXiv:D", published="", has_ontology=True)])
    row = [line for line in table.splitlines() if line.startswith("| 1 ")][0]
    assert row.count(ABSENT) == 3  # 종류·기술영역·발행일 전부


# --- 표 자체 ----------------------------------------------------------------


def test_same_document_appears_once() -> None:
    """한 문서가 여러 항목의 근거여도 출처 표에는 한 줄이다."""
    citations = [_cite("arXiv:A"), _cite("arXiv:A"), _cite("arXiv:A", locator="p2")]
    assert [c.locator for c in dedupe(citations)] == ["abstract", "p2"]


def test_no_citations_means_no_table() -> None:
    """근거가 없는 보고서에 빈 출처 절을 붙이지 않는다."""
    assert render_source_table([]) == ""
    assert attach_source_table("# 보고서", []) == "# 보고서"


def test_attach_does_not_touch_the_body() -> None:
    body = "# 보고서\n\n본문 문장 [arXiv:A]."
    attached = attach_source_table(body, [_cite("arXiv:A", has_ontology=True)])
    assert attached.startswith(body)
    assert "## 출처" in attached


# --- 메타가 LLM 입력에 새지 않는다 -------------------------------------------


def test_evidence_block_carries_no_ontology_metadata() -> None:
    """Writer/Verifier가 보는 근거 블록에 등급·기술영역이 들어가면 안 된다 (ADR-024)."""
    citation = _cite(
        "arXiv:A",
        published="2026-05-20",
        release_type="Community",
        tech_domains=("Agent",),
        has_ontology=True,
    )
    evidence = _format_evidence([citation])

    assert "arXiv:A" in evidence and "본문 스니펫" in evidence
    for leaked in ("Community", "Agent", "2026-05-20", UNVERIFIED):
        assert leaked not in evidence, f"{leaked!r}가 LLM 입력에 새어 들어갔다"


def test_llm_input_is_byte_identical_with_and_without_metadata() -> None:
    """메타를 실어도 모델이 받는 문자열은 그대로다.

    이것이 성립해야 `PROMPT_VERSION`을 올리지 않아도 되고, 전/후 비교가
    "인용 형식 하나만 바뀌었다"는 조건을 만족한다.
    """
    bare = _cite("arXiv:A")
    rich = _cite(
        "arXiv:A",
        published="2026-05-20",
        release_type="Paper",
        tech_domains=("Agent", "LLM"),
        has_ontology=True,
    )
    assert _format_evidence([bare]) == _format_evidence([rich])
    assert prompts.WRITER_USER.format(
        query="질의", findings=_format_evidence([bare])
    ) == prompts.WRITER_USER.format(query="질의", findings=_format_evidence([rich]))


# --- 검색 결과 → 인용 ---------------------------------------------------------


def test_chunk_metadata_reaches_the_citation() -> None:
    """Researcher가 고른 후보의 메타가 그대로 Citation에 실린다 (모델을 거치지 않는다)."""
    chunk = RetrievedChunk(
        doc_id="arXiv:A",
        locator="abstract",
        text="본문",
        source="arxiv",
        published="2026-05-20",
        release_type="Paper",
        tech_domains=("Agent", "LLM"),
        has_ontology=True,
    )
    (citation,) = _select_citations('{"supporting": [1]}', [chunk])

    assert citation.release_type == "Paper"
    assert citation.tech_domains == ("Agent", "LLM")
    assert citation.published == "2026-05-20"
    assert citation.has_ontology is True


def _raw(meta: dict) -> dict:
    return {
        "documents": [["본문"]],
        "metadatas": [[meta]],
        "distances": [[0.1]],
    }


def test_to_chunks_maps_ontology_fields() -> None:
    chunks = _to_chunks(
        _raw(
            {
                "doc_id": "arXiv:A",
                "locator": "abstract",
                "source": "arxiv",
                "published": "2026-05-20",
                "release_type": "Paper",
                "tech_domains": "Agent|Eval/Governance",
                "export_id": "export-2026-09-17",
            }
        )
    )
    (chunk,) = chunks
    assert chunk.release_type == "Paper"
    assert chunk.tech_domains == ("Agent", "Eval/Governance")
    assert chunk.has_ontology is True
    # 점수 계산은 건드리지 않았다.
    assert chunk.score == 0.9


def test_to_chunks_marks_snapshot_only_documents() -> None:
    """export를 거치지 않은 문서는 `has_ontology=False`다 — 층 B의 근거."""
    (chunk,) = _to_chunks(
        _raw(
            {
                "doc_id": "arXiv:C",
                "locator": "abstract",
                "source": "arxiv",
                "published": "2024-01-14",
            }
        )
    )
    assert chunk.has_ontology is False
    assert chunk.release_type == "" and chunk.tech_domains == ()
    assert chunk.published == "2024-01-14"


def test_to_chunks_never_carries_derived_text() -> None:
    """모델 패러프레이즈(`derived_*`)는 계약 §6대로 검색 결과에 실리지 않는다."""
    (chunk,) = _to_chunks(
        _raw(
            {
                "doc_id": "arXiv:A",
                "locator": "abstract",
                "source": "arxiv",
                "derived_summary": "모델이 쓴 요약",
                "derived_impact_rationale": "모델이 쓴 근거",
            }
        )
    )
    dumped = repr(chunk)
    assert "모델이 쓴 요약" not in dumped
    assert "모델이 쓴 근거" not in dumped
