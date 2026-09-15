"""공개 코퍼스 수집 — arXiv 논문 메타데이터·초록 (ADR-004).

**이 스크립트는 공개 자료만 수집한다.** 사내 문서는 이 프로젝트에 반입하지 않는다.
이유는 ADR-004: 현재 서빙 엔드포인트가 무인증 공개 접근이라 실제 사내 문서를 질의에
태우는 것은 회사 보안 정책 검토가 별도로 필요한 문제이고, 이 포트폴리오 프로젝트의
범위를 벗어난다.

수집 결과는 `data/corpus/arxiv/*.json`에 커밋된다. 인덱스(`var/chroma/`)가 아니라
원본 스냅샷을 커밋하는 이유는 재현성(problem-statement.md §2) 때문이다 — 같은
스냅샷에서 인덱스를 다시 만들면 같은 검색 결과가 나와야 한다.

실행: `python scripts/ingest_corpus.py` (레포 루트에서)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.tools.corpus import assert_allowed_source  # noqa: E402

ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"

# arXiv는 봇 트래픽에 민감하다. User-Agent를 밝히고 요청 간격을 둔다
# (arXiv API 이용 약관의 권고 사항).
USER_AGENT = "multiagent-research-lab/0.1 (portfolio project; arXiv API terms respected)"
# 실측(session-03): 3초 간격에서는 두 번째 질의부터 429가 돌아왔다. arXiv는 IP 단위로
# 짧은 창의 쿼터를 적용하는 것으로 보여 간격을 넉넉히 둔다. 수집은 1회성 작업이라
# 느려도 문제가 없다.
REQUEST_INTERVAL_S = 20.0

# 수집 주제. 이 프로젝트가 스스로를 조사 대상으로 삼는 구성이라, 골든셋 질의도
# 같은 영역에서 만들 수 있다.
# arXiv 검색 문법 주의: OR/AND를 괄호 없이 섞으면 503을 돌려준다. 질의 1건당
# 연산자를 하나만 쓴다.
QUERIES: tuple[tuple[str, str], ...] = (
    ("multiagent-llm", 'all:"multi-agent" AND all:"large language model"'),
    ("verification-loop", 'all:"self-correction" AND all:"language model"'),
    ("rag-attribution", 'all:"retrieval-augmented generation" AND all:"attribution"'),
    ("agent-orchestration", 'all:"LLM agent" AND all:"orchestration"'),
    ("agent-evaluation", 'all:"LLM agent" AND all:"benchmark"'),
)


def _clean(text: str) -> str:
    """Atom 본문의 줄바꿈·연속 공백을 정리한다."""
    return re.sub(r"\s+", " ", (text or "")).strip()


def _fetch(query: str, *, max_results: int, retries: int = 4) -> str:
    params = urllib.parse.urlencode(
        {
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
    )
    url = f"{ARXIV_API}?{params}"
    last_error: Exception | None = None

    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            last_error = exc
            # 429(rate limit)는 기다리면 풀린다. 그 외 4xx는 재시도해도 같다.
            if exc.code not in {429, 503}:
                raise
            time.sleep(REQUEST_INTERVAL_S * (attempt + 1))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(REQUEST_INTERVAL_S * (attempt + 1))

    raise RuntimeError(f"arXiv 요청 실패 ({query!r}): {last_error}")


def _parse(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    records: list[dict] = []

    for entry in root.findall(f"{ATOM}entry"):
        raw_id = _clean(entry.findtext(f"{ATOM}id") or "")
        if not raw_id:
            continue
        # https://arxiv.org/abs/2308.08155v1 -> 2308.08155v1
        arxiv_id = raw_id.rsplit("/", 1)[-1]

        summary = _clean(entry.findtext(f"{ATOM}summary") or "")
        if not summary:
            continue

        authors = [
            _clean(a.findtext(f"{ATOM}name") or "")
            for a in entry.findall(f"{ATOM}author")
        ]

        records.append(
            {
                "doc_id": f"arXiv:{arxiv_id}",
                "title": _clean(entry.findtext(f"{ATOM}title") or ""),
                "text": summary,
                # 초록 전체가 한 청크다. 초록은 이미 요약된 단위라 더 쪼개면
                # 문맥이 끊기고, 인용 위치도 "초록"보다 세분화할 실익이 없다.
                "locator": "abstract",
                "url": raw_id,
                "published": _clean(entry.findtext(f"{ATOM}published") or "")[:10],
                "authors": ", ".join(a for a in authors if a)[:300],
            }
        )
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="공개 arXiv 코퍼스 수집 (ADR-004)")
    parser.add_argument(
        "--per-query", type=int, default=8, help="주제별 수집 논문 수 (기본 8)"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "data" / "corpus" / "arxiv",
        help="출력 디렉터리",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="이미 받아둔 주제도 다시 요청한다 (기본은 건너뛴다)",
    )
    args = parser.parse_args()

    # 출력 경로의 디렉터리 이름이 곧 출처다. 화이트리스트를 먼저 강제한다.
    assert_allowed_source(args.out.name)
    args.out.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    total = 0
    failed: list[str] = []

    # 이미 받아둔 파일의 doc_id를 먼저 읽는다. arXiv가 IP 단위로 공격적으로
    # 429를 돌려주기 때문에(실측), 한 번에 전부 받지 못하는 것을 정상으로 보고
    # 여러 번 나눠 받을 수 있게 한다. 중복 제거는 파일 경계를 넘어 동작해야 한다.
    for existing in sorted(args.out.glob("*.json")):
        try:
            payload = json.loads(existing.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        seen.update(doc["doc_id"] for doc in payload.get("documents", []))

    for index, (slug, query) in enumerate(QUERIES):
        out_file = args.out / f"{slug}.json"
        if out_file.exists() and not args.refresh:
            print(f"[{slug}] 이미 있음 — 건너뜀 (--refresh로 다시 받는다)")
            continue

        if index:
            time.sleep(REQUEST_INTERVAL_S)
        print(f"[{slug}] 요청 중...", flush=True)

        try:
            records = _parse(_fetch(query, max_results=args.per_query))
        except RuntimeError as exc:
            # 한 질의가 막혀도 나머지를 포기하지 않는다. 남은 것은 다음 실행에서 받는다.
            print(f"  !! 실패: {exc}")
            failed.append(slug)
            continue

        # 주제 간 중복 제거 — 같은 논문이 여러 질의에 걸린다.
        unique = [r for r in records if r["doc_id"] not in seen]
        seen.update(r["doc_id"] for r in unique)

        payload = {
            "source": "arxiv",
            "query": query,
            "license_note": (
                "arXiv 메타데이터·초록은 공개 자료다. 본 프로젝트는 arXiv API로 "
                "수집한 메타데이터와 초록만 저장하며 논문 전문(PDF)은 보관하지 않는다."
            ),
            "documents": unique,
        }
        out_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        total += len(unique)
        print(f"  -> {len(unique)}건 저장 ({out_file.relative_to(REPO_ROOT)})")

    print(f"\n이번 실행에서 {total}건 수집 (중복 제거 후), 출처=arxiv")
    if failed:
        print(
            f"실패한 주제: {', '.join(failed)} — arXiv rate limit. "
            "잠시 후 같은 명령을 다시 실행하면 이어서 받는다."
        )
    print("다음: python scripts/build_index.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
