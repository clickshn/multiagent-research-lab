"""`ai-news-ontology` 출력 계약 v1 JSONL 반입 (ADR-018, `docs/contracts/ai-news-ontology-export-v1.md`).

**이 모듈은 계약 §7의 검증을 강제한다. 전부 예외이지 경고가 아니다.** 조용히 통과시키면
코퍼스가 오염되고, 오염된 코퍼스 위에서 잰 수치는 전부 다시 만들어야 한다.

계약 문서를 런타임에 읽지 않는다 — 필요한 규칙을 상수로 **복사해서 고정**한다.
참조였다면 문서 편집이 코드 동작을 조용히 바꾼다. 생산자 레포도 같은 방식이다
(`export/contract.py`).

**통제어휘는 복제하지 않는다** (계약 §5). 값 목록은 매 export의 manifest에서 읽고,
MARA는 대조만 한다. `ALLOWED_SOURCES`와는 성격이 다르다 — 저쪽은 *우리가 지켜야 할
정책*이라 코드에 박고(ADR-004), 이쪽은 *남의 레포가 정하는 사실*이라 받아서 본다.

**파생 텍스트는 `derived_` 접두어로 싣는다.** `summary`/`impact_rationale`은 Opus 5가
쓴 패러프레이즈다. 계약 §6이 색인·인용을 금지하므로, 저장 계층에서 이름만 봐도
"인용하면 안 되는 값"이라는 것이 보이게 한다. 검색 경로(`retrieval.py: _to_chunks`)는
이 키들을 `RetrievedChunk`로 옮기지 않는다 — 옮기는 순간 Writer가 원문 대신 모델
패러프레이즈를 인용할 수 있게 된다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .corpus import CorpusDoc, assert_allowed_source

# --- 계약에서 복사해 고정한 상수 (계약 §3, §6, §7) --------------------------

CONTRACT_MAJOR = 1
"""지원하는 `contract_version` major. 다르면 거부한다 (§7-1, §9)."""

TEXT_MAX_CHARS = 4000
"""`text` 길이 상한 (§6.1). 생산자의 `BODY_MAX_CHARS`를 참조하지 않고 복사했다."""

MIN_TEXT_CHARS = 1
"""v1 = 비어 있지 않음 (§6). 근거 없는 임계값을 또 만들지 않기 위해 측정 전까지 1이다."""

VALID_TEXT_ORIGINS: frozenset[str] = frozenset({"source_text"})
"""v1에서 유효한 값은 하나뿐이다 (§6). 원문 텍스트만 색인한다."""

REQUIRED_TOP_LEVEL: tuple[str, ...] = (
    "contract_version",
    "doc_id",
    "source",
    "source_name",
    "url",
    "title",
    "lang",
    "text",
    "text_origin",
    "text_chars",
    "text_truncated",
    "locator",
    "published_at",
    "collected_at",
    "ontology",
    "provenance",
    "indexable",
)

REQUIRED_PROVENANCE: tuple[str, ...] = (
    "extraction_model",
    "prompt_version",
    "prompt_sha256",
    "extracted_at",
    "vocab_version",
)
"""누락은 경고가 아니라 실패다 (§3.3, §7-6).

코퍼스가 "언제 어느 모델로 만들어졌는지 모르는 것"이 되면 ADR-002(모델 고정)가
코퍼스 쪽에서 조용히 깨진다. `gate_model`/`gate_prompt_version`은 null이 허용된다.
"""

REQUIRED_ONTOLOGY: tuple[str, ...] = (
    "tech_domains",
    "release_type",
    "companies",
    "prior_art",
    "impact_score",
    "impact_rationale",
    "summary",
)

_LIST_JOIN = "|"
"""Chroma 메타데이터는 스칼라만 받는다. 리스트는 이 구분자로 이어 붙인다.

⚠️ 이어 붙인 문자열에는 **부분 일치 필터를 걸 수 없다** (Chroma에 LIKE가 없다).
`tech_domains`로 필터를 걸어야 할 일이 생기면 저장 형태부터 다시 정해야 한다.
`release_type`은 단일 값(스칼라)이라 이 제약을 받지 않는다 — Session 3이 그쪽을
쓰기로 한 이유 중 하나다 (계약 §8.2).
"""


class ContractViolation(RuntimeError):
    """계약 위반 — import를 중단시킨다 (계약 §7).

    경고로 낮추지 않는다. 형식 결함이 섞인 코퍼스로 측정을 돌리면, 나중에 그것을
    알아챘을 때 그 위에서 잰 수치를 전부 다시 만들어야 한다.
    """


# --- manifest ---------------------------------------------------------------


@dataclass(frozen=True)
class ExportManifest:
    """export 1회분의 manifest (계약 §11.3). 어휘 스냅샷의 출처다."""

    path: Path
    export_id: str
    contract_version: str
    tech_domain: frozenset[str]
    release_type: frozenset[str]
    vocab_version: str
    counts: dict

    @property
    def vocab(self) -> dict[str, frozenset[str]]:
        return {"tech_domain": self.tech_domain, "release_type": self.release_type}


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractViolation(f"manifest를 읽을 수 없습니다: {path} ({exc})") from exc
    if not isinstance(payload, dict):
        raise ContractViolation(f"manifest 형식이 잘못됨(객체가 아님): {path}")
    return payload


def _assert_major(version: object, where: str) -> str:
    """§7-1. major가 다르면 거부, minor가 높으면 모르는 필드를 무시하고 진행 (§9)."""
    text = str(version or "").strip()
    major = text.split(".", 1)[0]
    if not major.isdigit() or int(major) != CONTRACT_MAJOR:
        raise ContractViolation(
            f"contract_version major 불일치: {text!r} (지원: {CONTRACT_MAJOR}.x) — {where}"
        )
    return text


def load_manifest(corpus_dir: Path, source_dir: Path, export_id: str) -> ExportManifest:
    """export의 manifest를 찾아 읽는다.

    ⚠️ **위치가 두 군데일 수 있다.** 계약 §2는 `<source_dir>/<export_id>.manifest.json`을
    적었지만, 생산자의 실제 출력은 `<corpus_dir>/<export_id>.manifest.json` 하나가
    `arxiv`/`news` 두 JSONL을 함께 덮는다 (생산자 session-01 §2). 후자가 실제로
    맞는 형태다 — manifest의 `counts`가 두 출처의 합계이기 때문이다. 둘 다 찾아보고,
    없으면 실패한다. manifest 없이 반입하면 어휘 대조(§5)를 할 수 없다.
    """
    candidates = [
        corpus_dir / f"{export_id}.manifest.json",
        source_dir / f"{export_id}.manifest.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            payload = _read_json(candidate)
            break
    else:
        shown = " 또는 ".join(str(c) for c in candidates)
        raise ContractViolation(
            f"export {export_id!r}의 manifest가 없습니다: {shown}. "
            "manifest 없이는 통제어휘 대조(계약 §5)를 할 수 없어 반입하지 않는다."
        )

    version = _assert_major(payload.get("contract_version"), f"manifest {candidate.name}")
    vocab = payload.get("vocab") or {}
    tech = vocab.get("tech_domain") or []
    release = vocab.get("release_type") or []
    if not tech or not release:
        raise ContractViolation(
            f"manifest에 어휘 스냅샷이 없습니다: {candidate}. "
            "MARA는 어휘를 복제하지 않으므로(계약 §5) 대조할 대상이 사라진다."
        )
    return ExportManifest(
        path=candidate,
        export_id=str(payload.get("export_id") or export_id),
        contract_version=version,
        tech_domain=frozenset(str(v) for v in tech),
        release_type=frozenset(str(v) for v in release),
        vocab_version=str(vocab.get("vocab_version", "")),
        counts=dict(payload.get("counts") or {}),
    )


# --- 어휘 대조 (계약 §5) -----------------------------------------------------


@dataclass(frozen=True)
class VocabDiff:
    """두 export manifest 사이의 어휘 변화."""

    field: str
    added: tuple[str, ...]
    removed: tuple[str, ...]


def diff_vocab(older: ExportManifest, newer: ExportManifest) -> list[VocabDiff]:
    diffs: list[VocabDiff] = []
    for field_name in ("tech_domain", "release_type"):
        old_values = older.vocab[field_name]
        new_values = newer.vocab[field_name]
        diffs.append(
            VocabDiff(
                field=field_name,
                added=tuple(sorted(new_values - old_values)),
                removed=tuple(sorted(old_values - new_values)),
            )
        )
    return diffs


def assert_vocab_compatible(manifests: list[ExportManifest]) -> list[str]:
    """어휘 변화의 **방향**을 본다 (계약 §5).

    - 값 추가 → 통과 (과거 라벨이 여전히 유효하다). 로그만 남긴다.
    - 값 삭제/개명/쪼개짐 → **실패.** 이미 인덱싱된 문서의 라벨이 소급해서 틀린 것이
      된다. 재인덱싱 없이는 복구되지 않는다.

    쪼개짐(`Agent` → `Agent/Tool`, `Agent/Multi`)은 삭제 + 추가로 나타나므로
    삭제 검사에 함께 걸린다. 개명도 마찬가지다.

    ⚠️ **이 검사는 manifest가 2개 이상 있을 때부터 실제로 동작한다.** 비교 대상이 될
    이전 어휘 스냅샷이 있어야 "사라졌다"를 말할 수 있기 때문이다. 어휘를 MARA 쪽
    파일로 복사해 두지 않는 이유는 계약 §5가 금지한 어휘 복제에 한 걸음 다가가기
    때문이다 — 대신 **생산자가 보낸 manifest 자체가 이력**이다.

    그래서 `discover_manifests()`는 JSONL이 없는 manifest도 읽는다 (ADR-019):
    v1은 전량 스냅샷이라 새 export가 오면 이전 JSONL을 **교체**하는데, 그때 manifest도
    같이 지우면 삭제 검사의 근거가 사라진다. manifest는 남긴다.
    """
    notes: list[str] = []
    ordered = sorted(manifests, key=lambda m: m.export_id)
    for older, newer in zip(ordered, ordered[1:], strict=False):
        for diff in diff_vocab(older, newer):
            if diff.removed:
                raise ContractViolation(
                    f"통제어휘에서 값이 사라졌습니다 ({diff.field}): "
                    f"{', '.join(diff.removed)} — {older.export_id} → {newer.export_id}. "
                    "이미 인덱싱된 문서의 라벨이 소급해서 틀린 것이 되므로 import를 "
                    "중단한다 (계약 §5). 값 추가는 통과하지만 삭제·개명·쪼개짐은 아니다."
                )
            if diff.added:
                notes.append(
                    f"어휘 추가({diff.field}): {', '.join(diff.added)} "
                    f"[{older.export_id} → {newer.export_id}] — 통과 (계약 §5)"
                )
    return notes


# --- 레코드 검증 (계약 §7) ---------------------------------------------------


def recompute_indexable(record: dict) -> bool:
    """계약 §6의 색인 규칙을 **MARA가 직접 다시 계산한다.**

    생산자 계산값(`record["indexable"]`)을 보지 않는다. 그대로 믿으면 두 레포의 규칙이
    갈렸을 때 알아챌 방법이 없다 (계약 §7 말미).
    """
    text_origin = str(record.get("text_origin", ""))
    text_chars = record.get("text_chars")
    if not isinstance(text_chars, int) or isinstance(text_chars, bool):
        return False
    return text_origin in VALID_TEXT_ORIGINS and text_chars >= MIN_TEXT_CHARS


def validate_record(record: dict, manifest: ExportManifest, *, where: str) -> None:
    """계약 §7의 검증 전부. 위반은 예외다."""
    missing = [key for key in REQUIRED_TOP_LEVEL if key not in record]
    if missing:
        raise ContractViolation(f"필수 최상위 필드 누락: {', '.join(missing)} — {where}")

    _assert_major(record.get("contract_version"), where)

    # §7-5: 출처 화이트리스트 (ADR-004의 기존 경로를 그대로 쓴다 — CorpusScopeError)
    assert_allowed_source(str(record.get("source", "")))

    # §7-6: provenance 5키. 없거나 빈 문자열이면 실패한다.
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        raise ContractViolation(f"provenance가 객체가 아닙니다 — {where}")
    for key in REQUIRED_PROVENANCE:
        value = provenance.get(key)
        if value is None or not str(value).strip():
            raise ContractViolation(
                f"provenance 필수 키 누락/빈 값: {key!r} — {where}. "
                "코퍼스가 어느 모델·프롬프트의 출력인지 기록되지 않으면 "
                "ADR-002(모델 고정)가 코퍼스 쪽에서 조용히 깨진다 (계약 §3.3)."
            )

    ontology = record.get("ontology")
    if not isinstance(ontology, dict):
        raise ContractViolation(f"ontology가 객체가 아닙니다 — {where}")
    missing_onto = [key for key in REQUIRED_ONTOLOGY if key not in ontology]
    if missing_onto:
        raise ContractViolation(f"ontology 필드 누락: {', '.join(missing_onto)} — {where}")

    # §7-7: text 길이 상한과 text_chars 일치.
    text = record.get("text")
    if not isinstance(text, str):
        raise ContractViolation(f"text가 문자열이 아닙니다 — {where}")
    if len(text) > TEXT_MAX_CHARS:
        raise ContractViolation(
            f"text가 계약 상한을 초과했습니다: {len(text)} > {TEXT_MAX_CHARS} — {where}. "
            "생산자가 자르지 않았다는 뜻이다 (계약 §6.1)."
        )
    text_chars = record.get("text_chars")
    if not isinstance(text_chars, int) or isinstance(text_chars, bool) or text_chars != len(text):
        raise ContractViolation(
            f"text_chars 불일치: {text_chars!r} != len(text)={len(text)} — {where}. "
            "임계값을 정할 근거 자체가 틀렸다는 뜻이라 조용히 통과시키지 않는다 (계약 §6.1)."
        )

    # §7-3: 통제어휘 대조. 값은 manifest에서만 온다 (복제하지 않는다).
    release_type = str(ontology.get("release_type", ""))
    if release_type not in manifest.release_type:
        raise ContractViolation(
            f"release_type이 manifest 어휘 밖입니다: {release_type!r} — {where} "
            f"(manifest={manifest.path.name}). 생산자 쪽에서 스키마와 export가 "
            "갈렸다는 신호다 (계약 §5)."
        )
    tech_domains = ontology.get("tech_domains")
    if not isinstance(tech_domains, list) or not tech_domains:
        raise ContractViolation(f"tech_domains가 비어 있거나 리스트가 아닙니다 — {where}")
    outside = [str(v) for v in tech_domains if str(v) not in manifest.tech_domain]
    if outside:
        raise ContractViolation(
            f"tech_domains가 manifest 어휘 밖입니다: {', '.join(outside)} — {where} "
            f"(manifest={manifest.path.name}). (계약 §5)"
        )

    # §7-4 + indexable 재계산 대조.
    text_origin = str(record.get("text_origin", ""))
    declared = record.get("indexable")
    if not isinstance(declared, bool):
        raise ContractViolation(f"indexable이 bool이 아닙니다: {declared!r} — {where}")
    if declared and text_origin not in VALID_TEXT_ORIGINS:
        raise ContractViolation(
            f"indexable=true인데 text_origin={text_origin!r} — {where}. "
            "원문 텍스트만 색인한다 (계약 §6)."
        )
    recomputed = recompute_indexable(record)
    if recomputed != declared:
        raise ContractViolation(
            f"indexable 재계산 불일치: 생산자={declared} / MARA 재계산={recomputed} — {where}. "
            "두 레포의 색인 규칙이 갈렸다는 뜻이다 (계약 §7)."
        )

    # §3.1: published_at 누락은 빈 문자열이 아니라 null이다.
    published_at = record.get("published_at")
    if published_at is not None and not str(published_at).strip():
        raise ContractViolation(
            f"published_at이 빈 문자열입니다 — {where}. 누락은 null로 표현한다 (계약 §3.1)."
        )


# --- 레코드 → CorpusDoc ------------------------------------------------------


def _scalar(value: object) -> str | int | float | bool | None:
    """Chroma 메타데이터에 실을 수 있는 형태로 좁힌다. None은 키째 버린다."""
    if value is None:
        return None
    if isinstance(value, bool | int | float | str):
        return value
    return str(value)


def _join(values: Iterable[object]) -> str:
    return _LIST_JOIN.join(str(v) for v in values)


def ontology_metadata(record: dict, *, export_id: str) -> dict[str, str | int | float | bool]:
    """온톨로지·출처·provenance를 스칼라 메타데이터로 평탄화한다.

    `CorpusDoc.extra`에 그대로 들어가고 `as_metadata()`가 Chroma로 넘긴다.
    **검색 조건은 여기서 만들지 않는다** — 싣기만 하고 `where` 배선은 Session 3이다
    (계약 §8.2, ADR-018).
    """
    ontology = record.get("ontology") or {}
    provenance = record.get("provenance") or {}
    companies = ontology.get("companies") or []

    raw: dict[str, object] = {
        "export_id": export_id,
        "contract_version": record.get("contract_version"),
        "source_name": record.get("source_name"),
        "lang": record.get("lang"),
        "collected_at": record.get("collected_at"),
        "text_origin": record.get("text_origin"),
        "text_chars": record.get("text_chars"),
        "text_truncated": record.get("text_truncated"),
        # --- 온톨로지 5필드 ---
        "release_type": ontology.get("release_type"),
        "tech_domains": _join(ontology.get("tech_domains") or []),
        "companies": _join(
            c.get("canonical") or c.get("raw") for c in companies if isinstance(c, dict)
        ),
        "companies_raw": _join(
            c.get("raw") for c in companies if isinstance(c, dict) and c.get("raw")
        ),
        "prior_art": _join(ontology.get("prior_art") or []),
        "impact_score": ontology.get("impact_score"),
        # --- 파생 텍스트: 이름으로 인용 금지를 표시한다 (계약 §6) ---
        "derived_summary": ontology.get("summary"),
        "derived_impact_rationale": ontology.get("impact_rationale"),
        # --- provenance (계약 §3.3) ---
        "extraction_model": provenance.get("extraction_model"),
        "prompt_version": provenance.get("prompt_version"),
        "prompt_sha256": provenance.get("prompt_sha256"),
        "extracted_at": provenance.get("extracted_at"),
        "vocab_version": provenance.get("vocab_version"),
        "gate_model": provenance.get("gate_model"),
        "gate_prompt_version": provenance.get("gate_prompt_version"),
    }

    metadata: dict[str, str | int | float | bool] = {}
    for key, value in raw.items():
        narrowed = _scalar(value)
        # Chroma는 None을 받지 않는다. 빈 값은 키를 만들지 않는다 —
        # "없음"과 "빈 문자열"을 메타데이터에서 구분할 필요가 없다.
        if narrowed is None or narrowed == "":
            continue
        metadata[key] = narrowed
    return metadata


def record_to_doc(record: dict, *, export_id: str) -> CorpusDoc:
    """검증을 통과한 레코드 1건을 `CorpusDoc`으로 만든다."""
    published_at = record.get("published_at")
    return CorpusDoc(
        doc_id=str(record["doc_id"]),
        source=str(record["source"]),
        title=str(record.get("title", "")).strip(),
        text=str(record.get("text", "")).strip(),
        locator=str(record.get("locator", "abstract")),
        url=str(record.get("url", "")),
        published=str(published_at or ""),
        extra=ontology_metadata(record, export_id=export_id),
    )


# --- export 파일 읽기 --------------------------------------------------------


@dataclass
class ImportReport:
    """반입 결과. 조용히 합치거나 버리지 않기 위해 전부 센다 (계약 §4.3)."""

    exports: list[str] = field(default_factory=list)
    records_read: int = 0
    excluded_not_indexable: int = 0
    merged_arxiv: list[str] = field(default_factory=list)
    new_docs: int = 0
    snapshot_docs: int = 0
    notes: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines = [
            f"export {len(self.exports)}건: {', '.join(self.exports) or '(없음)'}",
            f"레코드 {self.records_read}건 읽음 "
            f"→ 신규 {self.new_docs}건 · arXiv 병합 {len(self.merged_arxiv)}건 "
            f"· 색인 제외 {self.excluded_not_indexable}건",
            f"기존 스냅샷(JSON) 문서 {self.snapshot_docs}건",
        ]
        lines.extend(self.notes)
        return lines


@dataclass(frozen=True)
class ExportFile:
    path: Path
    source: str
    export_id: str
    manifest: ExportManifest


def discover_manifests(corpus_dir: Path) -> list[ExportManifest]:
    """코퍼스 디렉터리의 **모든** 어휘 스냅샷. JSONL이 없는 것도 읽는다.

    어휘 변화 방향(계약 §5)을 보려면 과거 스냅샷이 필요하고, 그 유일한 보관처가
    지나간 export의 manifest다 (ADR-019).
    """
    paths = sorted(corpus_dir.glob("*.manifest.json"))
    for source_dir in sorted(p for p in corpus_dir.iterdir() if p.is_dir()):
        assert_allowed_source(source_dir.name)
        paths.extend(sorted(source_dir.glob("*.manifest.json")))

    manifests: dict[str, ExportManifest] = {}
    for path in paths:
        export_id = path.name.removesuffix(".manifest.json")
        manifest = load_manifest(corpus_dir, path.parent, export_id)
        # 같은 export_id가 두 위치에 있으면 같은 것으로 본다 (계약 §2 vs 생산자 실제 출력).
        manifests.setdefault(manifest.export_id, manifest)
    return list(manifests.values())


def discover_exports(corpus_dir: Path) -> list[ExportFile]:
    """`<corpus_dir>/<source>/<export_id>.jsonl`을 찾는다. 출처 화이트리스트를 강제한다."""
    found: list[ExportFile] = []
    for source_dir in sorted(p for p in corpus_dir.iterdir() if p.is_dir()):
        source = assert_allowed_source(source_dir.name)
        for jsonl in sorted(source_dir.glob("*.jsonl")):
            export_id = jsonl.stem
            manifest = load_manifest(corpus_dir, source_dir, export_id)
            found.append(
                ExportFile(path=jsonl, source=source, export_id=export_id, manifest=manifest)
            )
    return found


def iter_export_records(export: ExportFile) -> list[dict]:
    """JSONL 1파일을 읽어 검증까지 끝낸 레코드 목록을 돌려준다.

    stdlib `json`만 쓴다 — 파서 의존성을 늘리지 않는다 (session-09 주의사항).
    """
    records: list[dict] = []
    for line_no, line in enumerate(export.path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        where = f"{export.path.name}:{line_no}"
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ContractViolation(f"JSONL 파싱 실패 — {where}: {exc}") from exc
        if not isinstance(record, dict):
            raise ContractViolation(f"레코드가 객체가 아닙니다 — {where}")
        validate_record(record, export.manifest, where=where)
        if str(record.get("source")) != export.source:
            raise ContractViolation(
                f"레코드의 source={record.get('source')!r}가 디렉터리 "
                f"{export.source!r}와 다릅니다 — {where}. "
                "출처 분류가 디렉터리와 갈리면 화이트리스트(ADR-004)가 무의미해진다."
            )
        records.append(record)
    return records


def merge_exports_into(
    snapshot_docs: list[CorpusDoc], corpus_dir: Path
) -> tuple[list[CorpusDoc], ImportReport]:
    """JSON 스냅샷 문서 위에 JSONL export를 얹는다.

    계약 §4.3 — arXiv 중복은 "탐지해서 거르는 것"이 아니라 **같은 키로 겹치게** 만든 것이다.
    같은 `doc_id`가 양쪽에 있으면:

    - `text`는 **기존 스냅샷 쪽을 유지한다** (arXiv API 초록이 피드 발췌보다 항상 길거나 같다)
    - `url`은 **정규화된 export 값으로 갱신한다** (스냅샷은 `http://`로 저장돼 있다)
    - 온톨로지 메타데이터만 병합한다
    - 병합 사실을 로그에 남긴다 — 조용히 합치면 코퍼스 규모 수치가 거짓이 된다

    그 외의 `doc_id` 중복은 §7-2대로 **실패**다. 마지막 값으로 덮어쓰지 않는다.
    """
    report = ImportReport(snapshot_docs=len(snapshot_docs))
    by_id: dict[str, CorpusDoc] = {}
    for doc in snapshot_docs:
        if doc.doc_id in by_id:
            raise ContractViolation(
                f"스냅샷 안에 doc_id가 중복됩니다: {doc.doc_id!r} (계약 §7-2)"
            )
        by_id[doc.doc_id] = doc

    exports = discover_exports(corpus_dir)
    if not exports:
        return list(snapshot_docs), report

    report.exports = sorted({e.export_id for e in exports})
    # 어휘 대조는 **지나간 manifest까지** 포함해서 본다 — JSONL이 교체돼도
    # 삭제·개명·쪼개짐을 볼 수 있어야 한다 (계약 §5, ADR-019).
    report.notes.extend(assert_vocab_compatible(discover_manifests(corpus_dir)))

    seen_in_exports: dict[str, str] = {}
    for export in exports:
        for record in iter_export_records(export):
            doc_id = str(record["doc_id"])
            where = f"{export.path.name} / {doc_id}"
            report.records_read += 1

            if doc_id in seen_in_exports:
                raise ContractViolation(
                    f"export 안에서 doc_id가 중복됩니다: {doc_id!r} "
                    f"({seen_in_exports[doc_id]} ↔ {export.path.name}) — 계약 §7-2. "
                    "마지막 값으로 덮어쓰지 않는다."
                )
            seen_in_exports[doc_id] = export.path.name

            if not record["indexable"]:
                # 색인 규칙에서 떨어진 레코드는 인덱스에 넣지 않는다 (계약 §6).
                # 파일에는 남아 있고, 세는 것은 report가 한다.
                report.excluded_not_indexable += 1
                continue

            existing = by_id.get(doc_id)
            if existing is None:
                by_id[doc_id] = record_to_doc(record, export_id=export.export_id)
                report.new_docs += 1
                continue

            # --- §4.3 병합: 이 경로만이 §7-2 중복 금지의 예외다 ---
            if existing.source != "arxiv" or record["source"] != "arxiv":
                raise ContractViolation(
                    f"doc_id가 스냅샷과 중복됩니다: {doc_id!r} — {where}. "
                    "병합은 arXiv 특례(계약 §4.3)에만 허용된다."
                )
            merged_extra = dict(existing.extra)
            merged_extra.update(ontology_metadata(record, export_id=export.export_id))
            merged_extra["merged_with_export"] = True
            # 길이 메타데이터는 **실제로 색인되는 텍스트**를 가리켜야 한다. export의
            # `text_chars`/`text_truncated`는 버려지는 쪽(피드 발췌)을 설명하는 값이라
            # 그대로 두면 `MIN_TEXT_CHARS`를 정할 분포가 틀어진다 (계약 §6).
            # 1단계 표본에서는 두 값이 우연히 같다 — 주입 2건의 원문을 MARA 스냅샷에서
            # 가져왔기 때문이다(생산자 session-01 §3의 5번 한계). 자연 중복에서는 다르다.
            merged_extra["text_chars"] = len(existing.text)
            merged_extra["text_truncated"] = False
            by_id[doc_id] = CorpusDoc(
                doc_id=existing.doc_id,
                source=existing.source,
                title=existing.title,
                # text는 스냅샷 쪽을 유지한다 (계약 §4.3).
                text=existing.text,
                locator=existing.locator,
                # url만 정규화된 값으로 갱신한다. doc_id는 스킴과 무관하므로
                # 병합 자체는 영향받지 않는다 — 갱신하는 것은 표기뿐이다.
                url=str(record.get("url") or existing.url),
                published=existing.published,
                extra=merged_extra,
            )
            report.merged_arxiv.append(doc_id)

    return list(by_id.values()), report
