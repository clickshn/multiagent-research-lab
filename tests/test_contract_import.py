"""출력 계약 v1 반입 검증 테스트 (계약 §5·§6·§7, ADR-018, ADR-019).

**방어 전 / 방어 후** (`.claude/rules/security.md`와 같은 기록 방식).

방어 전에는 `load_corpus()`가 `*.json` 스냅샷만 읽었고, `CorpusDoc.as_metadata()`가
스칼라 6키만 실어 `extra`를 조용히 버렸다. 그 상태에서 계약 JSONL을 넣으면 **파일이
아예 읽히지 않고**, 읽히게 고쳐도 온톨로지 필드는 메타데이터에 남지 않는다 — 둘 다
에러가 아니라 "정상 동작"으로 보인다.

방어 후에는 계약 §7 위반이 전부 `ContractViolation`으로 멈추고, 온톨로지 필드가
메타데이터에 실린다. 아래 테스트가 그 경계를 고정한다.

각 테스트는 **계약 위반을 1개만** 심는다. 한 레코드에 여러 개를 심으면 어느 검사가
잡았는지 알 수 없어, 검사기가 반쯤 고장 나도 테스트는 초록으로 남는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.tools.contract_import import (
    TEXT_MAX_CHARS,
    ContractViolation,
    ImportReport,
    merge_exports_into,
    ontology_metadata,
    recompute_indexable,
)
from src.tools.corpus import CorpusDoc, CorpusScopeError, load_corpus_with_report

TECH_VOCAB = ["LLM", "Agent", "Eval/Governance"]
RELEASE_VOCAB = ["Paper", "ProductLaunch", "Community/Discussion"]


def make_record(**overrides: Any) -> dict:
    """계약을 만족하는 최소 레코드. 테스트는 여기에 위반 1개만 심는다."""
    record: dict[str, Any] = {
        "contract_version": "1.0",
        "doc_id": "news:0000000000000001",
        "source": "news",
        "source_name": "GeekNews",
        "url": "https://news.hada.io/topic?id=1",
        "title": "제목",
        "lang": "ko",
        "text": "본문",
        "text_origin": "source_text",
        "text_chars": 2,
        "text_truncated": False,
        "locator": "feed_excerpt",
        "published_at": "2026-09-17",
        "collected_at": "2026-09-17",
        "indexable": True,
        "ontology": {
            "tech_domains": ["Agent"],
            "release_type": "Paper",
            "companies": [
                {"canonical": "OpenAI", "raw": "오픈AI", "resolved": True, "role": "발표 주체"}
            ],
            "prior_art": ["LLM Agent"],
            "impact_score": 3,
            "impact_rationale": "근거",
            "summary": "요약",
        },
        "provenance": {
            "extraction_model": "claude-opus-5",
            "prompt_version": "extract_ontology.v4.md",
            "prompt_sha256": "9ff5a9d3" * 8,
            "extracted_at": "2026-09-17T14:03:11+09:00",
            "gate_model": None,
            "gate_prompt_version": None,
            "vocab_version": "config=1;schema_sha256=abc",
        },
    }
    record.update(overrides)
    return record


def write_export(
    corpus_dir: Path,
    records: list[dict],
    *,
    export_id: str = "ontology-20260917-120733",
    source: str = "news",
    tech_vocab: list[str] | None = None,
    release_vocab: list[str] | None = None,
    manifest_overrides: dict | None = None,
) -> Path:
    source_dir = corpus_dir / source
    source_dir.mkdir(parents=True, exist_ok=True)
    jsonl = source_dir / f"{export_id}.jsonl"
    jsonl.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8"
    )
    indexable = sum(1 for r in records if r.get("indexable"))
    manifest = {
        "contract_version": "1.0",
        "export_id": export_id,
        "counts": {
            "total": len(records),
            "indexable": indexable,
            "excluded_empty_text": len(records) - indexable,
        },
        "vocab": {
            "vocab_version": "config=1;schema_sha256=abc",
            "tech_domain": tech_vocab if tech_vocab is not None else TECH_VOCAB,
            "release_type": release_vocab if release_vocab is not None else RELEASE_VOCAB,
        },
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    (corpus_dir / f"{export_id}.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return jsonl


def load(corpus_dir: Path, snapshot: list[CorpusDoc] | None = None):
    return merge_exports_into(snapshot or [], corpus_dir)


# --- §7 검증: 전부 실패해야 한다 --------------------------------------------


def test_valid_record_loads(tmp_path: Path) -> None:
    """기준선 — 위반이 없으면 통과한다. 이게 깨지면 아래 실패 테스트가 무의미하다."""
    write_export(tmp_path, [make_record()])
    docs, report = load(tmp_path)
    assert [d.doc_id for d in docs] == ["news:0000000000000001"]
    assert report.records_read == 1
    assert report.new_docs == 1


def test_contract_major_mismatch_rejected(tmp_path: Path) -> None:
    """§7-1. major가 다르면 거부한다 — 형식이 달라졌다는 뜻이다."""
    write_export(tmp_path, [make_record(contract_version="2.0")])
    with pytest.raises(ContractViolation, match="major 불일치"):
        load(tmp_path)


def test_minor_version_ahead_is_accepted(tmp_path: Path) -> None:
    """§9. minor가 높으면 모르는 필드를 무시하고 진행한다 — 거부가 아니다."""
    write_export(tmp_path, [make_record(contract_version="1.7", unknown_future_field="x")])
    docs, _ = load(tmp_path)
    assert len(docs) == 1


@pytest.mark.parametrize("key", ["extraction_model", "prompt_version", "prompt_sha256", "extracted_at", "vocab_version"])
def test_missing_provenance_key_fails(tmp_path: Path, key: str) -> None:
    """§7-6. provenance 5키 누락은 **경고가 아니라 실패**다.

    코퍼스가 외부 모델 버전의 함수라는 사실이 기록되지 않으면 ADR-002(모델 고정)가
    코퍼스 쪽에서 조용히 깨진다.
    """
    record = make_record()
    del record["provenance"][key]
    write_export(tmp_path, [record])
    with pytest.raises(ContractViolation, match="provenance 필수 키"):
        load(tmp_path)


@pytest.mark.parametrize("value", ["", "   ", None])
def test_empty_provenance_value_fails(tmp_path: Path, value: str | None) -> None:
    """키가 있어도 빈 값이면 누락과 같다 — 있는 척하는 쪽이 더 위험하다."""
    record = make_record()
    record["provenance"]["prompt_sha256"] = value
    write_export(tmp_path, [record])
    with pytest.raises(ContractViolation, match="provenance 필수 키"):
        load(tmp_path)


def test_gate_provenance_may_be_null(tmp_path: Path) -> None:
    """§3.3. `gate_model`/`gate_prompt_version`은 null이 허용된다 (게이트 미경유)."""
    write_export(tmp_path, [make_record()])
    docs, _ = load(tmp_path)
    assert "gate_model" not in docs[0].extra  # null은 키째 버린다 (Chroma가 None을 못 받는다)


def test_text_over_limit_fails(tmp_path: Path) -> None:
    """§7-7. 4,000자 초과는 생산자가 자르지 않았다는 뜻이다."""
    long_text = "가" * (TEXT_MAX_CHARS + 1)
    write_export(tmp_path, [make_record(text=long_text, text_chars=len(long_text))])
    with pytest.raises(ContractViolation, match="계약 상한을 초과"):
        load(tmp_path)


def test_text_at_limit_passes(tmp_path: Path) -> None:
    """경계값 4,000자는 통과한다 — 상한은 `<=`다."""
    text = "가" * TEXT_MAX_CHARS
    write_export(tmp_path, [make_record(text=text, text_chars=len(text))])
    docs, _ = load(tmp_path)
    assert len(docs) == 1


def test_text_chars_mismatch_fails(tmp_path: Path) -> None:
    """§7-7. `text_chars` 불일치는 임계값을 정할 근거 자체가 틀렸다는 뜻이다."""
    write_export(tmp_path, [make_record(text="본문", text_chars=999)])
    with pytest.raises(ContractViolation, match="text_chars 불일치"):
        load(tmp_path)


def test_indexable_recomputed_and_compared(tmp_path: Path) -> None:
    """§7 말미. 생산자 값을 믿지 않고 재계산해 대조한다.

    여기서는 빈 본문인데 생산자가 `indexable=true`로 보낸 경우 — 두 레포의 색인 규칙이
    갈렸다는 신호다.
    """
    write_export(tmp_path, [make_record(text="", text_chars=0, indexable=True)])
    with pytest.raises(ContractViolation, match="indexable 재계산 불일치"):
        load(tmp_path)


def test_indexable_false_record_is_excluded_not_indexed(tmp_path: Path) -> None:
    """빈 본문은 실패가 아니라 **색인 제외**다 (HF Blog 경로). 세는 것은 report가 한다."""
    write_export(
        tmp_path,
        [make_record(), make_record(doc_id="news:02", text="", text_chars=0, indexable=False)],
    )
    docs, report = load(tmp_path)
    assert [d.doc_id for d in docs] == ["news:0000000000000001"]
    assert report.excluded_not_indexable == 1


def test_recompute_indexable_rules() -> None:
    """§6의 규칙 자체 — 파생 텍스트는 색인 대상이 아니다."""
    assert recompute_indexable(make_record()) is True
    assert recompute_indexable(make_record(text="", text_chars=0)) is False
    assert recompute_indexable(make_record(text_origin="summary")) is False


def test_indexable_true_with_non_source_text_fails(tmp_path: Path) -> None:
    """§7-4. 파생 텍스트를 색인하면 인용이 원문이 아니라 모델 패러프레이즈를 가리킨다."""
    write_export(tmp_path, [make_record(text_origin="summary")])
    with pytest.raises(ContractViolation):
        load(tmp_path)


def test_duplicate_doc_id_in_export_fails(tmp_path: Path) -> None:
    """§7-2. 마지막 값으로 덮어쓰지 않는다."""
    write_export(tmp_path, [make_record(), make_record()])
    with pytest.raises(ContractViolation, match="doc_id가 중복"):
        load(tmp_path)


def test_disallowed_source_raises_corpus_scope_error(tmp_path: Path) -> None:
    """§7-5. 출처 화이트리스트는 ADR-004의 기존 경로를 그대로 쓴다."""
    internal = tmp_path / "internal-hr"
    internal.mkdir()
    (internal / "ontology-20260917-120733.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(CorpusScopeError):
        load(tmp_path)


def test_record_source_must_match_directory(tmp_path: Path) -> None:
    """디렉터리와 `source`가 갈리면 화이트리스트가 무의미해진다."""
    write_export(tmp_path, [make_record(source="arxiv")], source="news")
    with pytest.raises(ContractViolation, match="디렉터리"):
        load(tmp_path)


def test_empty_published_at_fails(tmp_path: Path) -> None:
    """§3.1. 누락은 빈 문자열이 아니라 null이다."""
    write_export(tmp_path, [make_record(published_at="")])
    with pytest.raises(ContractViolation, match="published_at"):
        load(tmp_path)


def test_null_published_at_passes(tmp_path: Path) -> None:
    write_export(tmp_path, [make_record(published_at=None)])
    docs, _ = load(tmp_path)
    assert docs[0].published == ""


def test_missing_manifest_fails(tmp_path: Path) -> None:
    """manifest가 없으면 어휘 대조(§5)를 할 수 없다 — 반입하지 않는다."""
    write_export(tmp_path, [make_record()])
    (tmp_path / "ontology-20260917-120733.manifest.json").unlink()
    with pytest.raises(ContractViolation, match="manifest가 없습니다"):
        load(tmp_path)


# --- §5 통제어휘 — 복제하지 않고 대조만 한다 ---------------------------------


def test_value_outside_manifest_vocab_fails(tmp_path: Path) -> None:
    """§7-3. 생산자 쪽에서 스키마와 export가 갈렸다는 신호다."""
    record = make_record()
    record["ontology"]["release_type"] = "없는유형"
    write_export(tmp_path, [record])
    with pytest.raises(ContractViolation, match="release_type이 manifest 어휘 밖"):
        load(tmp_path)


def test_tech_domain_outside_vocab_fails(tmp_path: Path) -> None:
    record = make_record()
    record["ontology"]["tech_domains"] = ["Agent", "없는도메인"]
    write_export(tmp_path, [record])
    with pytest.raises(ContractViolation, match="tech_domains가 manifest 어휘 밖"):
        load(tmp_path)


def test_vocab_addition_passes(tmp_path: Path) -> None:
    """§5. 값 추가는 통과 — 과거 라벨이 여전히 유효하다. 로그만 남는다."""
    write_export(tmp_path, [make_record()], export_id="ontology-20260101-000000")
    write_export(
        tmp_path,
        [make_record(doc_id="news:02")],
        export_id="ontology-20260201-000000",
        release_vocab=[*RELEASE_VOCAB, "Partnership/Contract"],
    )
    docs, report = load(tmp_path)
    assert len(docs) == 2
    assert any("어휘 추가" in note for note in report.notes)


def test_vocab_removal_fails(tmp_path: Path) -> None:
    """§5. 값 삭제는 실패 — 이미 인덱싱된 라벨이 소급해서 틀린 것이 된다."""
    write_export(tmp_path, [make_record()], export_id="ontology-20260101-000000")
    write_export(
        tmp_path,
        [make_record(doc_id="news:02")],
        export_id="ontology-20260201-000000",
        release_vocab=["Paper", "ProductLaunch"],
    )
    with pytest.raises(ContractViolation, match="값이 사라졌습니다"):
        load(tmp_path)


def test_vocab_rename_fails(tmp_path: Path) -> None:
    """개명은 삭제 + 추가로 나타난다. 추가 쪽에 가려지지 않아야 한다."""
    write_export(tmp_path, [make_record()], export_id="ontology-20260101-000000")
    write_export(
        tmp_path,
        [make_record(doc_id="news:02")],
        export_id="ontology-20260201-000000",
        tech_vocab=["LLM", "Agentic", "Eval/Governance"],  # Agent → Agentic
    )
    with pytest.raises(ContractViolation, match="Agent"):
        load(tmp_path)


def test_vocab_split_fails(tmp_path: Path) -> None:
    """쪼개짐(`Agent` → `Agent/Tool`, `Agent/Multi`)도 실패다 — 재인덱싱 없이 복구되지 않는다."""
    write_export(tmp_path, [make_record()], export_id="ontology-20260101-000000")
    write_export(
        tmp_path,
        [make_record(doc_id="news:02")],
        export_id="ontology-20260201-000000",
        tech_vocab=["LLM", "Agent/Tool", "Agent/Multi", "Eval/Governance"],
    )
    with pytest.raises(ContractViolation, match="사라졌습니다"):
        load(tmp_path)


# --- §4.3 arXiv 병합 ---------------------------------------------------------


def arxiv_record(**overrides: Any) -> dict:
    base = make_record(
        doc_id="arXiv:2412.05449v1",
        source="arxiv",
        source_name="arXiv cs.CL (Atom API)",
        url="https://arxiv.org/abs/2412.05449v1",
        lang="en",
        locator="abstract",
        text="피드 발췌",
        text_chars=len("피드 발췌"),
        text_truncated=True,
    )
    base.update(overrides)
    return base


def snapshot_doc() -> CorpusDoc:
    return CorpusDoc(
        doc_id="arXiv:2412.05449v1",
        source="arxiv",
        title="Towards Effective GenAI Multi-Agent Collaboration",
        text="스냅샷 초록 " * 20,
        locator="abstract",
        url="http://arxiv.org/abs/2412.05449v1",
        published="2024-12-06",
    )


def test_arxiv_duplicate_merges_without_new_doc_id(tmp_path: Path) -> None:
    """§4.3. 중복은 "탐지"가 아니라 **같은 키로 겹치게** 만든 것이다.

    새 `doc_id`가 생기면 골든셋 `expected_doc_ids`와 `min_citations` 판정이 조용히
    깨진다 — 그것이 이 규칙이 예방하려는 실패 모드다 (ADR-018 Amendment).
    """
    write_export(tmp_path, [arxiv_record()], source="arxiv")
    docs, report = load(tmp_path, [snapshot_doc()])
    assert len(docs) == 1
    assert report.merged_arxiv == ["arXiv:2412.05449v1"]
    assert report.new_docs == 0


def test_merge_keeps_snapshot_text(tmp_path: Path) -> None:
    """§4.3. arXiv API 초록이 피드 발췌보다 항상 길거나 같다 — 스냅샷 쪽을 유지한다."""
    write_export(tmp_path, [arxiv_record()], source="arxiv")
    docs, _ = load(tmp_path, [snapshot_doc()])
    assert docs[0].text == snapshot_doc().text
    # 길이 메타데이터는 **실제로 색인되는 텍스트**를 가리켜야 한다. 버려지는 발췌의
    # 값이 남으면 `MIN_TEXT_CHARS`를 정할 분포가 틀어진다.
    assert docs[0].extra["text_chars"] == len(docs[0].text)
    assert docs[0].extra["text_truncated"] is False


def test_merge_updates_url_to_normalized_value(tmp_path: Path) -> None:
    """§4.3. 스냅샷은 `http://`다. 갱신하는 것은 표기뿐 — `doc_id`는 스킴과 무관하다."""
    write_export(tmp_path, [arxiv_record()], source="arxiv")
    docs, _ = load(tmp_path, [snapshot_doc()])
    assert docs[0].url == "https://arxiv.org/abs/2412.05449v1"
    assert docs[0].doc_id == "arXiv:2412.05449v1"


def test_merge_carries_ontology_metadata(tmp_path: Path) -> None:
    write_export(tmp_path, [arxiv_record()], source="arxiv")
    docs, _ = load(tmp_path, [snapshot_doc()])
    assert docs[0].extra["release_type"] == "Paper"
    assert docs[0].extra["merged_with_export"] is True


def test_news_duplicate_with_snapshot_is_not_merged(tmp_path: Path) -> None:
    """병합은 arXiv 특례에만 허용된다. 그 외 중복은 §7-2대로 실패다."""
    write_export(tmp_path, [make_record()])
    snapshot = CorpusDoc(
        doc_id="news:0000000000000001", source="news", title="t", text="x", locator="feed_excerpt"
    )
    with pytest.raises(ContractViolation, match="arXiv 특례"):
        load(tmp_path, [snapshot])


# --- 메타데이터 적재 (`as_metadata()`가 `extra`를 버리지 않는다) ---------------


def test_as_metadata_carries_ontology_fields() -> None:
    """방어 전에는 여기서 온톨로지 필드가 전부 사라졌다 (스칼라 6키만 실렸다)."""
    doc = CorpusDoc(
        doc_id="news:01",
        source="news",
        title="t",
        text="x",
        extra=ontology_metadata(make_record(), export_id="ontology-20260917-120733"),
    )
    metadata = doc.as_metadata()
    assert metadata["release_type"] == "Paper"
    assert metadata["tech_domains"] == "Agent"
    assert metadata["lang"] == "ko"
    assert metadata["impact_score"] == 3
    assert metadata["extraction_model"] == "claude-opus-5"
    assert metadata["companies"] == "OpenAI"


def test_as_metadata_values_are_chroma_scalars() -> None:
    """Chroma는 스칼라만 받는다. 리스트·None이 새어 나가면 인덱싱이 런타임에 터진다."""
    doc = CorpusDoc(
        doc_id="news:01",
        source="news",
        title="t",
        text="x",
        extra=ontology_metadata(make_record(), export_id="e"),
    )
    for key, value in doc.as_metadata().items():
        assert isinstance(value, str | int | float | bool), f"{key}={value!r}"


def test_derived_text_is_prefixed() -> None:
    """§6. 파생 텍스트는 이름으로 인용 금지를 표시한다 — 검색 경로가 옮기지 않는다."""
    metadata = ontology_metadata(make_record(), export_id="e")
    assert metadata["derived_summary"] == "요약"
    assert "summary" not in metadata
    assert "impact_rationale" not in metadata


def test_citation_keys_cannot_be_overwritten_by_extra() -> None:
    """`extra`가 `doc_id`·`locator`를 바꿀 수 있으면 출처 표기 규칙이 문서마다 달라진다."""
    doc = CorpusDoc(
        doc_id="news:real",
        source="news",
        title="t",
        text="x",
        locator="feed_excerpt",
        extra={"doc_id": "news:spoofed", "locator": "spoofed"},
    )
    metadata = doc.as_metadata()
    assert metadata["doc_id"] == "news:real"
    assert metadata["locator"] == "feed_excerpt"


# --- 회귀: export가 없으면 session-09 이전과 동작이 같다 ----------------------


def test_snapshot_only_corpus_is_unchanged(tmp_path: Path) -> None:
    arxiv = tmp_path / "arxiv"
    arxiv.mkdir()
    (arxiv / "topic.json").write_text(
        json.dumps(
            {
                "source": "arxiv",
                "documents": [
                    {
                        "doc_id": "arXiv:1706.03762v1",
                        "title": "Attention Is All You Need",
                        "text": "초록",
                        "locator": "abstract",
                        "url": "http://arxiv.org/abs/1706.03762v1",
                        "published": "2017-06-12",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    docs, report = load_corpus_with_report(tmp_path)
    assert len(docs) == 1
    assert docs[0].as_metadata() == {
        "doc_id": "arXiv:1706.03762v1",
        "source": "arxiv",
        "title": "Attention Is All You Need",
        "locator": "abstract",
        "url": "http://arxiv.org/abs/1706.03762v1",
        "published": "2017-06-12",
    }
    assert isinstance(report, ImportReport)
    assert report.records_read == 0


def test_vocab_history_survives_export_replacement(tmp_path: Path) -> None:
    """v1은 전량 스냅샷이라 새 export가 오면 이전 JSONL을 교체한다 (ADR-019).

    그때 manifest까지 지우면 "값이 사라졌다"를 말할 근거가 없어진다. manifest만
    남아 있으면 삭제 검사는 계속 동작해야 한다.
    """
    write_export(tmp_path, [make_record()], export_id="ontology-20260101-000000")
    write_export(
        tmp_path,
        [make_record(doc_id="news:02")],
        export_id="ontology-20260201-000000",
        release_vocab=["Paper", "ProductLaunch"],
    )
    # 이전 export의 JSONL만 교체(삭제)하고 manifest는 남긴다.
    (tmp_path / "news" / "ontology-20260101-000000.jsonl").unlink()
    with pytest.raises(ContractViolation, match="값이 사라졌습니다"):
        load(tmp_path)
