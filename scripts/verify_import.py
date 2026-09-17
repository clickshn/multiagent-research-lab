"""출력 계약 v1 반입 검증 — MARA 쪽 정본 판정 (계약 §7, §12.2).

생산자 레포의 `export/conformance.py`는 **같은 명세를 생산자 쪽에서 재구현한 것**이라,
두 구현이 명세를 다르게 읽는 지점을 볼 수 없다 (생산자 session-01 §3). 계약 §7 검증의
정본은 이쪽 로더이고, **§12.2의 최종 판정은 이 스크립트가 내린다.**

여기서 검색 경로는 건드리지 않는다 — 온톨로지 필드를 메타데이터로 싣는 데까지가
Session 0.5의 경계다 (계약 §8.2, ADR-018). `release_type` 필터 배선은 Session 3이다.

실행: `python scripts/verify_import.py [--index]` (레포 루트에서)
  `--index` 를 주면 Chroma 적재까지 하고 `lang="ko"` 조회를 실제로 확인한다 (§12.2-13).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.tools.contract_import import (  # noqa: E402
    TEXT_MAX_CHARS,
    ContractViolation,
    discover_exports,
    iter_export_records,
)
from src.tools.corpus import load_corpus_with_report, load_snapshot, summarize  # noqa: E402

GOLDEN_SET = REPO_ROOT / "docs" / "eval" / "golden-set.json"

# 1단계 표본에 명시적으로 주입된 중복 2건 (계약 §12.1). 자연 중복은 발생하지 않으므로
# 이 2건이 없으면 §4.3의 ID 동일화는 검증되지 않은 채 통과한 것처럼 보인다.
INJECTED_DUPLICATES = ("arXiv:2412.05449v1", "arXiv:2605.21404v1")


class Checklist:
    """§12.2 판정표. 통과/실패를 한 군데서 센다."""

    def __init__(self) -> None:
        self.rows: list[tuple[int, str, str, str]] = []

    def record(self, number: int, name: str, passed: bool | None, detail: str) -> None:
        verdict = "PASS" if passed else ("PENDING" if passed is None else "FAIL")
        self.rows.append((number, name, verdict, detail))

    @property
    def failed(self) -> int:
        return sum(1 for row in self.rows if row[2] == "FAIL")

    def print(self) -> None:
        print("\n=== 계약 §12.2 체크리스트 (MARA 로더 판정) ===")
        for number, name, verdict, detail in self.rows:
            print(f"{number:>2}. [{verdict:>7}] {name} — {detail}")
        passed = sum(1 for row in self.rows if row[2] == "PASS")
        pending = sum(1 for row in self.rows if row[2] == "PENDING")
        print(f"\n{passed} PASS / {self.failed} FAIL / {pending} PENDING (총 {len(self.rows)})")


def main() -> int:
    parser = argparse.ArgumentParser(description="출력 계약 v1 반입 검증 (계약 §7, §12.2)")
    parser.add_argument("--corpus-dir", type=Path, default=None)
    parser.add_argument(
        "--index",
        action="store_true",
        help="Chroma에 적재하고 lang='ko' 조회까지 확인한다 (§12.2-13). 임베딩 모델을 로딩한다",
    )
    parser.add_argument("--reset", action="store_true", help="--index와 함께: 컬렉션을 비우고 다시 만든다")
    args = parser.parse_args()

    corpus_dir = args.corpus_dir or (REPO_ROOT / "data" / "corpus")
    check = Checklist()

    # --- 반입 (§7 검증 전체가 여기서 돈다) ---
    snapshot = load_snapshot(corpus_dir)
    try:
        docs, report = load_corpus_with_report(corpus_dir)
    except ContractViolation as exc:
        print(f"계약 위반으로 반입 중단:\n  {exc}")
        return 1

    print("=== 반입 결과 ===")
    for line in report.summary_lines():
        print(" ", line)
    print("\n코퍼스:", summarize(docs), f"= 총 {len(docs)}건")

    exports = discover_exports(corpus_dir)
    records = [r for export in exports for r in iter_export_records(export)]
    manifests = {e.export_id: e.manifest for e in exports}

    # --- §12.2 판정 -------------------------------------------------------
    check.record(
        1,
        "e2e 흐름",
        len(records) > 0,
        f"JSONL {len(records)}줄 + manifest {len(manifests)}개 → 로더 적재까지 예외 없음",
    )

    check.record(
        2,
        "provenance 5키",
        True,
        f"{len(records)}줄 전부 존재·비어 있지 않음 (누락이면 위에서 이미 중단됐다)",
    )

    prompt_hashes = {r["provenance"]["prompt_sha256"] for r in records}
    check.record(
        3,
        "prompt_sha256 실측 일치",
        None,
        f"값 {len(prompt_hashes)}종 확인({next(iter(prompt_hashes))[:12]}…) — "
        "프롬프트 파일은 생산자 레포에만 있어 MARA에서 실측할 수 없다",
    )

    origins = Counter(r["text_origin"] for r in records)
    check.record(
        4, "text_origin", set(origins) == {"source_text"}, f"{dict(origins)}"
    )

    snapshot_arxiv = sum(1 for d in snapshot if d.source == "arxiv")
    export_arxiv_new = sum(
        1 for r in records if r["source"] == "arxiv" and r["doc_id"] not in {d.doc_id for d in snapshot}
    )
    final_arxiv = sum(1 for d in docs if d.source == "arxiv")
    expected_arxiv = snapshot_arxiv + export_arxiv_new
    merged_ok = set(report.merged_arxiv) >= set(INJECTED_DUPLICATES)
    check.record(
        5,
        "arXiv 중복 병합",
        final_arxiv == expected_arxiv and merged_ok,
        f"기존 {snapshot_arxiv} + 신규 {export_arxiv_new} = {expected_arxiv}, 실제 {final_arxiv}; "
        f"병합 {sorted(report.merged_arxiv)}",
    )

    golden = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
    expected_ids: set[str] = set()
    for case in golden["cases"]:
        expected_ids.update(case["expect"].get("expected_doc_ids", []))
    corpus_ids = {d.doc_id for d in docs}
    missing_golden = sorted(expected_ids - corpus_ids)
    check.record(
        6,
        "골든셋 보존",
        not missing_golden,
        f"expected_doc_ids {len(expected_ids)}건 전부 조회 가능"
        if not missing_golden
        else f"누락: {missing_golden}",
    )

    # §12.2-7(doc_id 결정성)은 같은 입력으로 export를 2회 돌리는 검사라 생산자 쪽에서만
    # 판정할 수 있다. MARA는 받은 파일을 2회 읽어 같은 doc_id 집합이 나오는지만 본다.
    reread = {r["doc_id"] for export in exports for r in iter_export_records(export)}
    check.record(
        7,
        "doc_id 결정성(소비자 측)",
        reread == {r["doc_id"] for r in records},
        f"{len(reread)}건 재읽기 일치 — 2회 export 대조는 생산자 판정 (session-01 §3)",
    )

    over_limit = [r["doc_id"] for r in records if len(r["text"]) > TEXT_MAX_CHARS]
    mismatch = [r["doc_id"] for r in records if r["text_chars"] != len(r["text"])]
    lengths = sorted(r["text_chars"] for r in records)
    check.record(
        8,
        "text 상한·text_chars",
        not over_limit and not mismatch,
        f"최소 {lengths[0]} / 중앙 {lengths[len(lengths) // 2]} / 최대 {lengths[-1]}자, 상한 {TEXT_MAX_CHARS}",
    )

    excluded = [r for r in records if not r["indexable"]]
    excluded_by_name = Counter(r["source_name"] for r in excluded)
    check.record(
        9,
        "indexable 재계산 일치",
        True,
        f"{len(records)}줄 전부 일치 (불일치면 위에서 중단됐다). false {len(excluded)}건 = "
        f"{dict(excluded_by_name)}",
    )

    check.record(
        10,
        "통제어휘 대조",
        True,
        "전 값이 manifest vocab 안 (밖이면 위에서 중단됐다). "
        f"vocab_version={next(iter(manifests.values())).vocab_version[:28]}…",
    )

    empty_published = [r["doc_id"] for r in records if r["published_at"] == ""]
    null_published = [r["doc_id"] for r in records if r["published_at"] is None]
    check.record(
        11,
        "published_at null 처리",
        not empty_published,
        f"빈 문자열 0건 / null {len(null_published)}건"
        + (" — null 경로가 이 표본에서 실행되지 않았다" if not null_published else ""),
    )

    manifest_ok = []
    for export_id, manifest in manifests.items():
        counts = manifest.counts
        total_ok = counts.get("total") == len(
            [r for e in exports if e.export_id == export_id for r in iter_export_records(e)]
        )
        sum_ok = counts.get("indexable", 0) + counts.get("excluded_empty_text", 0) == counts.get(
            "total"
        )
        manifest_ok.append(total_ok and sum_ok)
    check.record(
        12,
        "manifest 합계",
        all(manifest_ok),
        ", ".join(
            f"{eid}: total={m.counts.get('total')}, "
            f"indexable+excluded={m.counts.get('indexable', 0) + m.counts.get('excluded_empty_text', 0)}"
            for eid, m in manifests.items()
        ),
    )

    ko_docs = [d for d in docs if d.extra.get("lang") == "ko"]
    if args.index:
        from src.tools.retrieval import ChromaRetriever  # noqa: PLC0415 (임베딩 로딩 지연)

        retriever = ChromaRetriever()
        if args.reset:
            retriever.reset()
        print(f"\n임베딩 + 인덱싱 중... ({len(docs)}건, 모델 첫 로딩에 수십 초)")
        retriever.index(docs)
        collection = retriever._get_collection(create=True)  # noqa: SLF001 (검증 스크립트)
        got = collection.get(where={"lang": "ko"}, include=["metadatas"])
        indexed_ko = len(got["ids"])
        check.record(
            13,
            "한국어 레코드 색인",
            indexed_ko == len(ko_docs) and indexed_ko > 0,
            f"lang='ko' 조회 {indexed_ko}건 / 코퍼스 한국어 {len(ko_docs)}건, "
            f"컬렉션 총 {retriever.count()}건",
        )
    else:
        check.record(
            13,
            "한국어 레코드 색인",
            None,
            f"코퍼스에 lang='ko' {len(ko_docs)}건 — 실제 조회는 `--index`로 확인한다",
        )

    check.print()

    # --- 코퍼스 규모·언어 비율 (Session 1 baseline / Session 2 크로스링구얼) ---
    langs = Counter(str(d.extra.get("lang", "en(스냅샷 추정)")) for d in docs)
    print("\n=== 코퍼스 규모 ===")
    print(f"  총 {len(docs)}건 · 출처별 {summarize(docs)}")
    print(f"  언어별 {dict(langs)}")
    ko_ratio = len(ko_docs) / len(docs) * 100 if docs else 0.0
    print(f"  한국어 {len(ko_docs)}건 = {ko_ratio:.1f}%")
    print(
        "  ⚠️ 스냅샷 16건에는 `lang`이 없다 — 계약 이전 형식이라 선언값 자체가 존재하지 않는다. "
        "전부 영어 초록이지만 값은 추정이다."
    )

    return 1 if check.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
