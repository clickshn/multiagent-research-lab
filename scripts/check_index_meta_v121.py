"""v1.2.1 동일성 관문 I2·I3 — 재인덱싱 뒤 인덱스 메타를 읽기만 해서 확인한다 (사전 등록 `9454c23` §5).

- **I2 (계약 §12.2-13, 생산자 자체검사 13번):** GeekNews 레코드 10건이 전부 인덱스에 있고 `lang="ko"`로 조회된다.
  조회는 Chroma `where={"lang": "ko"}` — session-10과 같은 기준.
- **I3:** 37건 전부 `has_ontology`(= `export_id`가 있다), `extraction_model = gemma-4-31B-it`, `prompt_sha256` 1종.

    python scripts/check_index_meta_v121.py [--out <결과 JSON>]

종료 코드: 0 = 통과 · 1 = 실패. LLM 호출 0건. 인덱스는 읽기만 한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

EXPECTED_MODEL = "gemma-4-31B-it"
EXPECTED_PROMPT_SHA = "25b17f445bec91a2fa4336b20dbac214b09c87fcb1c8493c17fcdd5b65e57bc8"
EXPECTED_DOCS = 37
EXPECTED_KO = 10


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    from src.tools.corpus import load_corpus  # noqa: PLC0415
    from src.tools.retrieval import ChromaRetriever  # noqa: PLC0415

    collection = ChromaRetriever()._get_collection(create=False)
    metas = collection.get(include=["metadatas"])["metadatas"] or []
    by_doc = {str(m.get("doc_id")): m for m in metas}

    geeknews = sorted(d.doc_id for d in load_corpus() if d.extra.get("source_name") == "GeekNews")
    ko_rows = collection.get(where={"lang": "ko"}, include=["metadatas"])["metadatas"] or []
    ko_ids = sorted({str(m.get("doc_id")) for m in ko_rows})

    i2 = {
        "geeknews_in_corpus": len(geeknews),
        "lang_ko_query": len(ko_ids),
        "geeknews_all_indexed_as_ko": set(geeknews) <= set(ko_ids),
        "ko_ids_equal_geeknews": ko_ids == geeknews,
    }
    i2["passed"] = (len(geeknews) == EXPECTED_KO and i2["ko_ids_equal_geeknews"])

    no_meta = sorted(d for d, m in by_doc.items() if not str(m.get("export_id", "")).strip())
    empty_domains = sorted(d for d, m in by_doc.items() if not str(m.get("tech_domains", "")).strip())
    models = Counter(str(m.get("extraction_model")) for m in by_doc.values())
    shas = Counter(str(m.get("prompt_sha256")) for m in by_doc.values())
    i3 = {
        "indexed_docs": len(by_doc),
        "rows": len(metas),
        "without_ontology": no_meta,
        "empty_tech_domains": empty_domains,
        "extraction_model": dict(models),
        "prompt_sha256": dict(shas),
    }
    i3["passed"] = (len(by_doc) == EXPECTED_DOCS == len(metas) and not no_meta and not empty_domains
                    and dict(models) == {EXPECTED_MODEL: EXPECTED_DOCS}
                    and dict(shas) == {EXPECTED_PROMPT_SHA: EXPECTED_DOCS})

    report = {"preregistration": "9454c23", "I2": i2, "I3": i3, "passed": i2["passed"] and i3["passed"]}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
