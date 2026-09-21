"""인용 형식 전/후 대조 (session-16, ADR-024). **LLM 호출 0건.**

Session 4의 산출물은 "보고서가 좋아졌다"가 아니라 **인용이 무엇을 더 말해주는가**다.
그 차이는 모델을 거치지 않으므로, 모델을 부르지 않고 그대로 보여줄 수 있다 —
출처 표는 코드가 검색 결과에서 렌더링하기 때문이다 (ADR-024).

그래서 이 프로브는 파이프라인을 돌리지 않는다. 검색만 하고(로컬 임베딩), 같은
검색 결과로 **전(前) 형식과 후(後) 형식을 나란히 렌더링**한다. 덤으로 두 형식에서
**모델이 받는 입력이 바이트 단위로 같은지**를 해시로 확인한다 — 같아야
`PROMPT_VERSION`이 그대로이고 "인용 형식 하나만 바뀌었다"가 참이 된다.

⚠️ **이것은 파이프라인 품질 측정이 아니다.** 실제 실행에서는 Researcher가 후보 중
일부만 고르지만 여기서는 top-k 전부를 인용으로 취급한다. 형식을 보여주는 것이 목적이고,
**Researcher 프롬프트의 점수 노출(session-13 §6.2)이 그대로**이므로 여기서 나온 어떤
숫자도 baseline이 아니다.

    python scripts/probe_citation_format.py
    python scripts/probe_citation_format.py --cases GS-001,GS-003 --top-k 4

외부 API 호출 0건. 임베딩은 로컬 sentence-transformers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.orchestrator.citations import UNVERIFIED, render_source_table  # noqa: E402
from src.orchestrator.nodes import _format_evidence, _select_citations  # noqa: E402
from src.orchestrator.prompts import PROMPT_VERSION  # noqa: E402
from src.orchestrator.state import Citation  # noqa: E402
from src.providers.config import load_embedding_settings  # noqa: E402
from src.tools.retrieval import ChromaRetriever, RetrievedChunk  # noqa: E402

GOLDEN_SET = REPO_ROOT / "docs" / "eval" / "golden-set.json"
DEFAULT_OUT = REPO_ROOT / "docs" / "eval" / "citation-format-session-16.md"

# 기본 표본: 층 A(앵커에 온톨로지 메타 있음)와 층 B(결측)를 섞어 고른다.
# 결측 표기는 결측 문서가 실제로 검색에 걸려야 확인할 수 있다.
DEFAULT_CASES = ("GS-001", "GS-003", "GS-011")


def _bare(citation: Citation) -> Citation:
    """온톨로지 메타를 뗀 같은 인용. LLM 입력 동일성 대조용이다."""
    return Citation(
        doc_id=citation.doc_id, locator=citation.locator, snippet=citation.snippet
    )


def _before(citations: list[Citation]) -> str:
    """session-15까지의 인용 표면 — 본문에 붙는 `[doc_id]`가 전부였다."""
    seen: list[str] = []
    for citation in citations:
        if citation.doc_id not in seen:
            seen.append(citation.doc_id)
    return " ".join(f"[{doc_id}]" for doc_id in seen)


def _citations_for(chunks: list[RetrievedChunk]) -> list[Citation]:
    """top-k 전부를 인용으로 취급한다 (형식 대조용, §docstring 주의 참조)."""
    supporting = json.dumps({"supporting": list(range(1, len(chunks) + 1))})
    return list(_select_citations(supporting, chunks))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def main() -> int:
    parser = argparse.ArgumentParser(description="인용 형식 전/후 대조 (LLM 호출 0건)")
    parser.add_argument("--cases", default=",".join(DEFAULT_CASES))
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    payload = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
    by_id = {case["id"]: case for case in payload["cases"]}
    wanted = [c.strip() for c in args.cases.split(",") if c.strip()]
    missing = [c for c in wanted if c not in by_id]
    if missing:
        print(f"골든셋에 없는 케이스: {', '.join(missing)}")
        return 1

    embedding_settings = load_embedding_settings()
    retriever = ChromaRetriever()

    started = time.perf_counter()
    corpus_size = retriever.count()
    cold_load_s = round(time.perf_counter() - started, 2)

    lines: list[str] = [
        "# 인용 형식 전/후 대조 — session-16 (ADR-024)",
        "",
        "> **이 문서는 baseline이 아니다.** Researcher 프롬프트의 유사도 점수 노출",
        "> (session-13 §6.2)이 그대로이고, 여기서는 Researcher를 거치지 않고 top-k 전부를",
        "> 인용으로 취급한다. **인용 형식 외의 어떤 수치도 읽지 마라.**",
        "",
        "| 항목 | 값 |",
        "| --- | --- |",
        f"| LLM 호출 | **0건** (검색만 — 임베딩은 로컬) |",
        f"| 임베딩 모델 | `{embedding_settings.model_name}` |",
        f"| 코퍼스 | {corpus_size}건 |",
        f"| top-k | {args.top_k} |",
        f"| `PROMPT_VERSION` | `{PROMPT_VERSION}` — **변경 없음** (ADR-024) |",
        f"| 임베딩 콜드 로딩 | {cold_load_s}s (질의 지연이 아니다) |",
        "",
        "## 무엇이 바뀌었나",
        "",
        "검색 결과의 온톨로지 메타(종류·기술 영역·발행일)를 `RetrievedChunk` → `Citation`으로",
        "실어 Writer 단계까지 보내고, 보고서 끝에 **코드가 렌더링한 출처 표**로 붙인다.",
        "모델의 입력과 본문은 바뀌지 않는다.",
        "",
    ]

    identical = True
    strata_rows: list[tuple[str, int, int]] = []

    for case_id in wanted:
        case = by_id[case_id]
        query = case["query"]
        chunks = retriever.search(query, k=args.top_k)
        citations = _citations_for(chunks)

        rich_input = _format_evidence(citations)
        bare_input = _format_evidence([_bare(c) for c in citations])
        same = rich_input == bare_input
        identical = identical and same

        unverified = sum(1 for c in citations if not c.has_ontology)
        strata_rows.append((case_id, len(citations) - unverified, unverified))

        lines.extend(
            [
                f"## {case_id} — 층 {case.get('stratum', '?')} "
                f"(`anchor_meta={case.get('anchor_meta', '?')}`)",
                "",
                f"질의: {query}",
                "",
                "**전 (session-15까지):** 본문에 붙는 문서 ID가 전부였다.",
                "",
                "```",
                _before(citations) or "(인용 없음)",
                "```",
                "",
                "**후 (session-16):**",
                "",
                render_source_table(citations) or "(인용 없음)",
                "",
                f"모델이 받는 근거 블록: `sha256:{_sha(rich_input)}` "
                f"(메타 제거본 `sha256:{_sha(bare_input)}`) — "
                + ("**동일**" if same else "**다름 ⚠️**"),
                "",
            ]
        )

    lines.extend(
        [
            "## 층 구성 — 무엇이 `" + UNVERIFIED + "`으로 표시됐나",
            "",
            "| 케이스 | 메타 보유 | 미확인(층 B) |",
            "| --- | ---: | ---: |",
        ]
    )
    for case_id, present, unverified in strata_rows:
        lines.append(f"| {case_id} | {present} | {unverified} |")
    total_present = sum(r[1] for r in strata_rows)
    total_unverified = sum(r[2] for r in strata_rows)
    lines.extend(
        [
            f"| **합계** | **{total_present}** | **{total_unverified}** |",
            "",
            "`미확인`은 온톨로지 export를 거치지 않은 문서다 (스냅샷 arXiv 14/37건, ADR-022 층 B).",
            "**빈칸으로 두지 않는 이유**: 빈칸이면 \"등급이 없는 문서\"와 \"등급을 아직 모르는",
            "문서\"가 같아 보이고, 그 구별이 ADR-022가 층을 나눈 이유 그 자체다.",
            "",
            "## LLM 입력 불변 확인",
            "",
            ("**전 케이스에서 동일하다.** 온톨로지 메타는 출처 표에만 쓰이고 모델 입력에는 "
             "들어가지 않는다 — 그래서 `PROMPT_VERSION`을 올리지 않았고, 전/후 비교가 "
             "\"인용 형식 하나만 바뀌었다\"는 조건을 만족한다."
             if identical
             else "⚠️ **일치하지 않는 케이스가 있다. ADR-024의 전제가 깨졌다.**"),
            "",
        ]
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print(f"→ {args.out.relative_to(REPO_ROOT)}")
    print(f"LLM 입력 동일성: {'OK' if identical else 'FAIL'}")
    print(f"메타 보유 {total_present}건 / 미확인 {total_unverified}건")
    return 0 if identical else 1


if __name__ == "__main__":
    raise SystemExit(main())
