"""골든셋 전체를 한 프로세스에서 돌려 지연·토큰·근거 커버리지를 측정한다.

**왜 별도 스크립트인가.** `run_research.py`는 질의 1건마다 프로세스를 새로 띄운다.
그 방식에서는 임베딩 모델 첫 로딩(CPU, 수십 초)이 매 실행의 벽시계에 섞여 들어가
"파이프라인이 느리다"와 "모델을 매번 새로 읽는다"를 구분할 수 없다 (session-03 관찰:
벽시계 33.93s 중 LLM은 5.76s뿐). 이 스크립트는

1. 임베딩 모델·Chroma 첫 질의·`import litellm`을 **먼저 워밍업**해 두고 그 시간을
   따로 보고한 뒤 (session-17 §1: 임베딩만 워밍업하면 첫 케이스 `wall_clock_s`에
   HNSW 로딩과 litellm 임포트가 섞였다),
2. 같은 프로세스에서 골든셋 N건을 연속 실행한다 (`--repeat`면 N건 × 회차).

그래서 여기 나오는 "파이프라인 지연"은 상주 서비스로 배포했을 때의 지연에 해당한다.
콜드 로딩 시간은 측정값이 아니라 **기동 1회 비용**으로 따로 적힌다.

**세 가지 지연을 나눠 적는다.**

- `llm_latency_s` — 모델 호출 지연의 합. 캐시 적중은 0에 가깝다.
- `retrieval_latency_s` — 검색(임베딩 인코딩 + Chroma 질의) 지연의 합. 모델이
  워밍업된 뒤의 값이므로 로딩 시간이 섞이지 않는다.
- `wall_clock_s` — 파이프라인 전체. 위 둘에 파싱·그래프 오버헤드가 더해진 값이다.

**측정 조건을 코드로 강제한다 (session-17 §1·§2, v1.2-S0b).** 사람의 기억에만 있던
조건을 기동 시 검사로 옮겼다. 하나라도 어긋나면 **LLM을 한 번도 부르기 전에** 멈춘다.

- 인덱스 쓰기 로그 == 문서 수 (`index_guard.read_write_log`, ADR-005 Amendment 5)
- 같은 인덱스를 쓰는 다른 bench 실행이 없을 것 (잠금 파일)
- 같은 라벨의 결과 파일이 이미 있으면 덮어쓰지 않는다
- pass/fail은 **층별로만** 집계한다 — 합산 하나만 내면 ADR-022 위반 형태다

실행:
    python scripts/bench_golden.py --label v1.2-s0b --repeat 2   # -> ...-run1 / -run2
    python scripts/bench_golden.py --label smoke --only GS-013 GS-006
    python scripts/bench_golden.py --label cached --cache
    python scripts/compare_bench_runs.py <run1.json> <run2.json>  # 회차 간 대조
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.obs import get_tracer  # noqa: E402
from src.obs.tracer import NullTracer  # noqa: E402
from src.orchestrator import compile_graph, initial_state  # noqa: E402
from src.orchestrator.nodes import TechDomainSelectionError, _format_candidates  # noqa: E402
from src.orchestrator.prompts import PROMPT_VERSION  # noqa: E402
from src.providers import get_provider  # noqa: E402
from src.providers.cache import clear_cache  # noqa: E402
from src.providers.config import (  # noqa: E402
    load_cache_settings,
    load_concurrency_settings,
    load_embedding_settings,
    load_settings,
    load_tool_settings,
    load_tracing_settings,
    load_vectorstore_settings,
)
from src.providers.embeddings import get_embedding_provider  # noqa: E402
from src.tools.index_guard import (  # noqa: E402
    IndexIntegrityError,
    indexed_ids,
    read_write_log,
)
from src.tools.retrieval import ChromaRetriever, FilterValueError, RetrievedChunk  # noqa: E402

GOLDEN_SET = REPO_ROOT / "docs" / "eval" / "golden-set.json"
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "eval"

# ADR-025 효과 지표의 위험군: 골든셋 질의 기준 정답이 top-4 안에 있는데 점수가
# 최난도 negative(0.8303)보다 낮은 6건. 판정 기준은 ADR-025 "판정 기준"에 사전 등록돼 있다.
RISK_CASES: tuple[str, ...] = ("GS-011", "GS-013", "GS-015", "GS-017", "GS-018", "GS-021")

# 논리 호출 1건이 엔드포인트 요청 몇 건이 될 수 있는가: 최초 1회 + HTTP 재시도
# `max_retries`(config.py 기본 2)회. 재시도는 트레이스에 잡히지 않으므로(session-17 §3.1)
# 실측하지 않고 상한만 적는다.
REQUESTS_PER_CALL_UPPER = 1 + 2

EXIT_OK = 0
EXIT_EXISTS = 3
EXIT_INDEX = 4
EXIT_LOCKED = 5
EXIT_TRACE = 6
# 사전 등록 §4 정지 규칙 (v1.2-T2): 스키마 위반·어휘 밖 값·selection_error 누적.
EXIT_SELECTION = 7
# T3 본측정에서 이 건수에 닿으면 멈춘다 (사전 등록 §4, 사용자 결정 2026-10-01).
SELECTION_ERROR_STOP = 3


class BenchRefused(RuntimeError):
    """측정 조건이 성립하지 않아 LLM을 부르기 전에 멈춘다."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _percentile(values: list[float], pct: float) -> float:
    """가장 가까운 순위(nearest-rank) 백분위.

    보간법을 쓰면 실제로 관측되지 않은 값이 지표가 된다. nearest-rank는 항상
    **실제 관측값**을 돌려준다. n=30이면 p95는 29번째 값이고 p50은 15번째 값이다
    — 표본이 커져도 방법은 유효하며, 비교선(session-03·08)과 같은 방법을 유지한다.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(1, min(len(ordered), int(-(-pct * len(ordered) // 100))))
    return ordered[index - 1]


# ---------------------------------------------------------------------------
# 기동 검사 — LLM 호출 전에 끝난다
# ---------------------------------------------------------------------------


def run_labels(label: str, repeat: int) -> list[str]:
    """회차 라벨. `--repeat 1`이면 라벨 그대로, 2 이상이면 `-run1`, `-run2`, ..."""
    if repeat < 1:
        raise ValueError("repeat은 1 이상이어야 한다")
    if repeat == 1:
        return [label]
    return [f"{label}-run{i}" for i in range(1, repeat + 1)]


def output_path(out_dir: Path, label: str) -> Path:
    return out_dir / f"bench-{label}.json"


def refuse_existing_outputs(out_dir: Path, labels: Sequence[str]) -> list[Path]:
    """같은 라벨의 결과가 있으면 거부한다 (session-17 §2 #7).

    예전 동작은 덮어쓰기였다 — `--label no-cache`로 돌리면 session-03 기준선이 조용히
    사라진다. **모든 회차 경로를 측정 전에** 검사한다: 2회차에서야 거부되면 1회차 호출은
    이미 쓴 뒤다.
    """
    paths = [output_path(out_dir, label) for label in labels]
    existing = [p for p in paths if p.exists()]
    if existing:
        names = ", ".join(p.name for p in existing)
        raise BenchRefused(
            f"같은 라벨의 결과가 이미 있습니다: {names}. 덮어쓰지 않습니다 — "
            "다른 --label을 쓰거나, 정말 버릴 파일이면 사람이 직접 지우세요.",
            EXIT_EXISTS,
        )
    return paths


def check_index(persist_dir: Path, collection: str) -> dict:
    """쓰기 로그 행 수 == 인덱스 문서 수인가 (session-17 §2 #8, ADR-005 Amendment 5).

    Chroma 클라이언트를 열기 **전에** 읽기 전용 sqlite로만 본다. 중복 upsert가 있는
    인덱스는 `count()`·`get()`이 정상값을 내면서 문서 1건을 조용히 검색하지 못한다
    — 그 위에서 잰 수치는 전부 다시 재야 한다. 검사 불가도 통과로 치지 않는다.
    """
    try:
        log = read_write_log(persist_dir, collection)
        ids = indexed_ids(persist_dir, collection)
    except IndexIntegrityError as exc:
        raise BenchRefused(f"인덱스 검사를 할 수 없습니다: {exc}", EXIT_INDEX) from exc

    if log is None:
        raise BenchRefused(
            f"컬렉션 {collection!r}이 없습니다. `python scripts/build_index.py`를 먼저 실행하세요.",
            EXIT_INDEX,
        )
    if not (log.clean and log.rows == len(ids)):
        raise BenchRefused(
            f"인덱스 쓰기 로그가 '문서당 1행'을 어겼습니다: 로그 {log.describe()} / "
            f"문서 {len(ids)}건. 이 인덱스 위의 측정은 무효다 (ADR-005 Amendment 4). "
            "`python scripts/build_index.py --reset`으로 다시 만드세요.",
            EXIT_INDEX,
        )
    return {
        "queue_rows": log.rows,
        "distinct_ids": log.distinct_ids,
        "indexed_docs": len(ids),
        "operations": {str(k): v for k, v in sorted(log.operations.items())},
        "clean": True,
    }


def lock_path(persist_dir: Path) -> Path:
    """인덱스 디렉터리 **옆**에 둔다 — 인덱스 디렉터리 안에 파일을 만들지 않는다."""
    return persist_dir.parent / f".{persist_dir.name}.bench.lock"


@contextmanager
def index_lock(persist_dir: Path) -> Iterator[Path]:
    """같은 인덱스를 쓰는 bench 실행을 하나로 제한한다 (session-15 §7.2, ADR-015 Risks).

    `O_EXCL` 생성으로 원자적으로 잡는다. 오래된 잠금(프로세스가 죽어 남은 것)을 자동으로
    치우지 않는다 — 살아 있는 실행을 죽은 것으로 오판하면 동시 실행 금지가 사라진다.
    남은 잠금은 사람이 확인하고 지운다.
    """
    path = lock_path(persist_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        holder = path.read_text(encoding="utf-8", errors="replace").strip()
        raise BenchRefused(
            f"같은 인덱스를 쓰는 다른 bench 실행이 있습니다 (잠금: {path.name}, {holder}). "
            "실행 중이 아니라면 확인 후 잠금 파일을 지우세요.",
            EXIT_LOCKED,
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()} started={time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n")
        yield path
    finally:
        path.unlink(missing_ok=True)


def candidate_score_exposed() -> bool:
    """Researcher 후보 블록에 유사도 점수가 실리는가 (ADR-025).

    결과에 "점수 노출 상태"를 사람이 적어 넣는 대신, 실제 포맷터 출력에서 판정한다.
    S0c에서 점수를 빼면 이 값이 스스로 False가 된다.
    """
    probe = RetrievedChunk(doc_id="probe", locator="0", text="x", source="probe", score=0.123456)
    return "0.123" in _format_candidates([probe])


# ---------------------------------------------------------------------------
# 워밍업 — 측정 대상 아님, 기동 1회 비용
# ---------------------------------------------------------------------------


def _warm_up_embeddings() -> tuple[object, float, float]:
    """임베딩 모델을 상주시키고 (콜드 로딩 시간, 웜 인코딩 시간)을 잰다."""
    embeddings = get_embedding_provider()

    started = time.perf_counter()
    embeddings.embed_query("워밍업")  # 이 호출이 모델을 실제로 읽어 들인다
    cold_s = time.perf_counter() - started

    started = time.perf_counter()
    embeddings.embed_query("워밍업 2회차")
    warm_s = time.perf_counter() - started

    return embeddings, cold_s, warm_s


def _warm_up_chroma(retriever: ChromaRetriever, top_k: int) -> float:
    """Chroma 첫 질의(HNSW 세그먼트 로딩)를 측정 전에 끝낸다. 결과는 버린다."""
    started = time.perf_counter()
    retriever.search("워밍업 질의", k=top_k)
    return time.perf_counter() - started


def _warm_up_litellm() -> float:
    """`llm.py`는 litellm을 호출 시점에 임포트한다. 첫 케이스에 그 비용이 섞이지 않게 한다.

    임포트만 한다 — 엔드포인트 호출은 없다.
    """
    started = time.perf_counter()
    importlib.import_module("litellm")
    return time.perf_counter() - started


# ---------------------------------------------------------------------------
# 트레이스 되읽기
# ---------------------------------------------------------------------------


def _read_trace(trace_path: Path) -> list[dict] | None:
    """JSONL 트레이스를 읽는다. 파일이 없으면 None — 0건과 구분한다 (session-17 §2 #11)."""
    if not trace_path.exists():
        return None
    records = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _retrieval_latency(records: list[dict] | None) -> tuple[float, int]:
    """트레이스에서 검색 span의 지연 합과 건수를 읽는다.

    검색 지연을 노드가 따로 세지 않고 트레이스에서 되읽는 이유: 계측 로그가
    운영 중에도 같은 질문에 답할 수 있어야 하기 때문이다 (ADR-007). 측정
    스크립트만 아는 별도 경로를 만들면 그 경로는 운영에서 검증되지 않는다.
    """
    total = 0.0
    count = 0
    for record in records or []:
        if record.get("name") == "researcher_retrieve":
            total += float(record.get("latency_s") or 0.0)
            count += 1
    return total, count


def item_records(findings: Sequence, trace_records: list[dict] | None) -> list[dict] | None:
    """Researcher 호출(항목 × 회차)별로 후보와 `supporting`을 짝짓는다 (ADR-025 before 값).

    후보는 State에 남지 않으므로 `researcher_retrieve` span에서 되읽는다 — 파이프라인
    코드를 바꾸지 않기 위해서다. Researcher는 항목마다 검색 1회 → Finding 1건을 같은
    순서로 만들므로 두 목록은 1:1이다. **항목 이름이 어긋나면 짝짓지 않고 None을 돌려준다**
    — 추정으로 맞추면 효과 지표가 틀린 짝 위에서 계산된다.

    **동시 실행(ADR-026)에서는 span이 완료 순서로 쓰인다.** 그래서 span에
    `revision`·`item_index`가 있으면 그 키로 정렬한 뒤 짝짓는다(순차 실행에서는 이미 그
    순서라 정렬이 아무것도 바꾸지 않는다). 키가 없는 옛 트레이스는 파일 순서 그대로다.
    같은 키로 Researcher 호출 span을 찾아 `input_hash`를 붙인다 — 후보가 없어 호출하지
    않은 항목은 None이다.
    """
    if trace_records is None:
        return None
    spans = [r for r in trace_records if r.get("name") == "researcher_retrieve"]
    if len(spans) != len(findings):
        return None
    keys = [_item_key(span.get("input")) for span in spans]
    if all(key is not None for key in keys):
        if len(set(keys)) != len(keys):
            return None  # 같은 항목 키가 두 번 — 짝을 정할 수 없다
        spans = [span for _, span in sorted(zip(keys, spans, strict=True), key=lambda p: p[0])]
    call_hashes = {
        _item_key(r.get("metadata")): (r.get("metadata") or {}).get("input_hash")
        for r in trace_records
        if r.get("name") == "researcher_call" and _item_key(r.get("metadata")) is not None
    }
    # tech_domain 선택 도구 (v1.2-T2). 키가 겹치면 귀속 검사가 잡는다 — 여기서는 마지막 것을 쓴다.
    selects = {
        _item_key(r.get("metadata")): r
        for r in trace_records
        if r.get("name") == "researcher_select" and _item_key(r.get("metadata")) is not None
    }
    filters = {
        _item_key(r.get("input")): r
        for r in trace_records
        if r.get("name") == "retrieval_filter" and _item_key(r.get("input")) is not None
    }

    items = []
    for finding, span in zip(findings, spans, strict=True):
        topic = (span.get("input") or {}).get("topic")
        if topic != finding.topic:
            return None
        output = span.get("output") or []
        candidates = [
            {"doc_id": c.get("doc_id"), "rank": rank, "score": c.get("score")}
            for rank, c in enumerate(output, start=1)
            if isinstance(c, dict)
        ]
        supporting: list[str] = []
        for citation in finding.citations:
            if citation.doc_id not in supporting:
                supporting.append(citation.doc_id)
        item = {
            "topic": finding.topic,
            "revision": finding.revision,
            "item_index": (span.get("input") or {}).get("item_index"),
            "retrieval_error": span.get("output") is None,
            "candidates": candidates,
            "supporting": supporting,
            "input_hash": call_hashes.get(_item_key(span.get("input"))),
        }
        span_input = span.get("input") or {}
        if "tech_domain_source" in span_input:
            # 도구가 켜진 행에만 붙인다 — off 행의 항목 기록은 v1.2-P1과 같은 모양이어야 한다.
            key = _item_key(span_input)
            select = selects.get(key) or {}
            select_meta = select.get("metadata") or {}
            filt = filters.get(key) or {}
            filt_meta = filt.get("metadata") or {}
            item["tech_domain"] = {
                "value": finding.tech_domain,
                "outcome": finding.tech_domain_outcome,
                "source": span_input.get("tech_domain_source"),
                # 검색 span이 실제로 받은 필터 값. `value`(Finding)와 같아야 한다 — 귀속 검사가 본다.
                "searched_with": span_input.get("tech_domain"),
                # 1회차만 선택 호출이 있다. 재시도는 그 값을 재사용한다(사전 등록 D1).
                "selected": select_meta.get("selected"),
                "raw_response": select.get("output"),
                "select_input_hash": select_meta.get("input_hash"),
                # 필터 후 결과 수 — 검색 계층 `retrieval_filter` span (ADR-027).
                "filter_survivors": filt_meta.get("survivors"),
                "filter_returned": filt_meta.get("returned"),
                "filter_expected": filt_meta.get("expected"),
            }
        items.append(item)
    return items


def tech_domain_selection_summary(rows: Sequence[dict]) -> dict | None:
    """선택 도구 집계 (v1.2-T2) — **1회차 항목만**, 층별. 도구가 꺼진 회차는 None.

    사전 등록의 판정 지표(정확·호환 일치, 해악)는 이 집계가 아니라 T3 분석 스크립트가 낸다.
    여기는 경로 기록용 건수다. `selection_error`는 선택 지표 분모에서 빼므로 따로 센다.
    """
    by_stratum: dict[str, Counter] = defaultdict(Counter)
    seen = False
    for row in rows:
        for item in row.get("items") or []:
            choice = item.get("tech_domain")
            if choice is None:
                continue
            seen = True
            if item.get("revision") != 0:
                by_stratum[str(row.get("stratum"))]["reused"] += 1
                continue
            bucket = by_stratum[str(row.get("stratum"))]
            bucket["first_pass_items"] += 1
            bucket[choice.get("outcome") or "unknown"] += 1
    if not seen:
        return None
    return {
        stratum: dict(sorted(counts.items())) for stratum, counts in sorted(by_stratum.items())
    }


def selection_errors(findings: Sequence) -> int:
    """1회차 `selection_error` 항목 수 (사전 등록 §4 정지 규칙).

    span이 아니라 Finding에서 센다 — 트레이스가 꺼져 있어도 정지 규칙은 걸려야 한다.
    """
    return sum(
        1 for f in findings
        if f.revision == 0 and getattr(f, "tech_domain_outcome", None) == "selection_error"
    )


def _item_key(fields: dict | None) -> tuple[int, int] | None:
    """span의 (revision, item_index). 둘 중 하나라도 없으면 None (ADR-026 이전 트레이스)."""
    if not isinstance(fields, dict):
        return None
    revision, index = fields.get("revision"), fields.get("item_index")
    if not isinstance(revision, int) or not isinstance(index, int):
        return None
    return revision, index


_TRUNCATION_MARK = re.compile(r"\.\.\.<\d+chars>$")


def attribution_problems(items: Sequence[dict], trace_records: list[dict] | None) -> list[str]:
    """span-항목 귀속 검사 (ADR-026). 빈 목록이면 불일치 없음.

    `item_records`는 검색 span에서 후보를, 같은 키의 Researcher 호출 span에서 `input_hash`를
    가져온다. 동시 실행에서는 span이 완료 순서로 쓰이므로, 그 짝이 **다른 항목의 것**이 아닌지
    호출 span 쪽에서 거꾸로 확인한다. T3의 도구 인자 평가도 span에서 되읽으므로 같은 검사가 필요하다.

    - 호출 span의 `(revision, item_index)`가 중복되지 않는다
    - 후보가 있는 항목마다 호출 span이 정확히 하나 있고, 없는 항목에는 없다
    - 그 span의 `topic`·`input_hash`가 항목과 같다
    - 항목의 후보 doc_id가 그 span 프롬프트에 **후보 순서대로** 있다. 프롬프트가 `_safe()`에서
      잘렸으면 뒤쪽 후보만 빠지는 것은 허용한다(잘림 표식이 있을 때만)
    - supporting ⊆ 후보
    """
    problems: list[str] = []
    calls: dict[tuple[int, int], dict] = {}
    for record in trace_records or []:
        if record.get("name") != "researcher_call":
            continue
        key = _item_key(record.get("metadata"))
        if key is None:
            problems.append("호출 span에 revision·item_index 없음")
            continue
        if key in calls:
            problems.append(f"호출 span 키 중복 {key}")
        calls[key] = record

    expected_keys: set[tuple[int, int]] = set()
    for item in items:
        label = f"{item.get('topic')!r} (rev {item.get('revision')}, #{item.get('item_index')})"
        key = (item.get("revision"), item.get("item_index"))
        candidates = [c["doc_id"] for c in item.get("candidates") or []]
        if not set(item.get("supporting") or []) <= set(candidates):
            problems.append(f"{label}: supporting이 후보 밖")
        if not candidates:
            continue
        expected_keys.add(key)
        call = calls.get(key)
        if call is None:
            problems.append(f"{label}: 호출 span 없음")
            continue
        meta = call.get("metadata") or {}
        if meta.get("topic") != item.get("topic"):
            problems.append(f"{label}: 호출 span topic {meta.get('topic')!r}")
        if meta.get("input_hash") != item.get("input_hash"):
            problems.append(f"{label}: input_hash 불일치")
        user = str((call.get("input") or {}).get("user") or "")
        positions = [user.find(f"doc_id={doc_id}\n") for doc_id in candidates]
        found = [p for p in positions if p != -1]
        missing_tail_only = all(p == -1 for p in positions[len(found):])
        if found != sorted(found) or not missing_tail_only:
            problems.append(f"{label}: 후보가 호출 프롬프트에 순서대로 없음")
        elif len(found) < len(candidates) and not _TRUNCATION_MARK.search(user):
            problems.append(f"{label}: 후보 {len(candidates) - len(found)}건이 호출 프롬프트에 없음")

    extra = set(calls) - expected_keys
    if extra:
        problems.append(f"후보 없는 항목에 호출 span {sorted(extra)}")
    problems.extend(selection_attribution_problems(items, trace_records))
    return problems


ABSTAIN = "없음"  # src.orchestrator.nodes.TECH_DOMAIN_ABSTAIN — 트레이스 값과 대조하므로 문자열로 고정


def selection_attribution_problems(
    items: Sequence[dict], trace_records: list[dict] | None
) -> list[str]:
    """선택 호출·필터 span의 귀속 검사 (v1.2-T2). `attribution_problems`가 부른다.

    T3의 선택 지표(선택값·필터 후 결과 수)는 전부 span에서 되읽는다. 동시 실행에서 span이
    다른 항목의 것과 짝지어지면 지표가 틀린 짝 위에서 계산되므로 `researcher_call`과 같은 검사를 한다.

    - `researcher_select`·`retrieval_filter` span의 `(revision, item_index)`가 중복되지 않는다
    - 도구가 켜진 **1회차** 항목마다 선택 span이 정확히 하나, 재시도 항목에는 없다
    - 선택 span의 topic이 항목과 같고, 선택값·결과가 Finding·검색 span 입력과 맞는다
    - 재시도 항목은 `reused`이고 값·결과가 같은 topic의 1회차와 같다
    - 필터 값이 있고 검색이 성공한 항목마다 필터 span이 정확히 하나, 값이 같고 `returned` == 후보 수.
      값이 없는 항목에는 필터 span이 없다
    - 도구가 꺼진 행에는 선택·필터 span이 하나도 없다
    """
    problems: list[str] = []
    selects: dict[tuple[int, int], dict] = {}
    filters: dict[tuple[int, int], dict] = {}
    for record in trace_records or []:
        name = record.get("name")
        if name == "researcher_select":
            key, bucket, label = _item_key(record.get("metadata")), selects, "선택"
        elif name == "retrieval_filter":
            key, bucket, label = _item_key(record.get("input")), filters, "필터"
        else:
            continue
        if key is None:
            problems.append(f"{label} span에 revision·item_index 없음")
            continue
        if key in bucket:
            problems.append(f"{label} span 키 중복 {key}")
        bucket[key] = record

    tool_items = [item for item in items if item.get("tech_domain") is not None]
    if not tool_items:
        if selects or filters:
            problems.append(f"도구 off 행에 선택 span {len(selects)}개 · 필터 span {len(filters)}개")
        return problems
    if len(tool_items) != len(items):
        problems.append("같은 행에 도구 기록이 있는 항목과 없는 항목이 섞여 있다")

    first_pass = {item["topic"]: item for item in tool_items if item.get("revision") == 0}
    expected_selects: set[tuple[int, int]] = set()
    expected_filters: set[tuple[int, int]] = set()
    for item in tool_items:
        choice = item["tech_domain"]
        key = (item.get("revision"), item.get("item_index"))
        label = f"{item.get('topic')!r} (rev {item.get('revision')}, #{item.get('item_index')})"
        value, outcome = choice.get("value"), choice.get("outcome")
        if choice.get("searched_with") != value:
            problems.append(f"{label}: 검색 span 필터 값 {choice.get('searched_with')!r} ≠ {value!r}")
        if (value is not None) != (outcome == "chosen"):
            problems.append(f"{label}: 결과 {outcome!r}와 필터 값 {value!r}이 맞지 않는다")

        if item.get("revision") == 0:
            expected_selects.add(key)
            if choice.get("source") != "selected":
                problems.append(f"{label}: 1회차인데 source={choice.get('source')!r}")
            select = selects.get(key)
            if select is None:
                problems.append(f"{label}: 선택 span 없음")
            else:
                meta = select.get("metadata") or {}
                if meta.get("topic") != item.get("topic"):
                    problems.append(f"{label}: 선택 span topic {meta.get('topic')!r}")
                if outcome == "selection_error":
                    if not meta.get("error"):
                        problems.append(f"{label}: selection_error인데 선택 span에 오류가 없다")
                elif outcome == "abstain":
                    if meta.get("selected") != ABSTAIN:
                        problems.append(f"{label}: 기권인데 선택 span 값 {meta.get('selected')!r}")
                elif meta.get("selected") != value:
                    problems.append(f"{label}: 선택 span 값 {meta.get('selected')!r} ≠ {value!r}")
        else:
            origin = first_pass.get(item.get("topic"))
            if choice.get("source") != "reused":
                problems.append(f"{label}: 재시도인데 source={choice.get('source')!r}")
            if origin is None:
                problems.append(f"{label}: 재사용할 1회차 항목이 없다")
            elif (origin["tech_domain"].get("value"), origin["tech_domain"].get("outcome")) != (
                value, outcome
            ):
                problems.append(f"{label}: 재사용 값이 1회차와 다르다")

        if value is not None and not item.get("retrieval_error"):
            expected_filters.add(key)
            filt = filters.get(key)
            if filt is None:
                problems.append(f"{label}: 필터 span 없음")
                continue
            if (filt.get("input") or {}).get("tech_domain") != value:
                problems.append(f"{label}: 필터 span 값 {(filt.get('input') or {}).get('tech_domain')!r}")
            returned = (filt.get("metadata") or {}).get("returned")
            if returned != len(item.get("candidates") or []):
                problems.append(f"{label}: 필터 returned={returned} ≠ 후보 {len(item.get('candidates') or [])}")

    if set(selects) - expected_selects:
        problems.append(f"1회차 항목이 아닌 곳에 선택 span {sorted(set(selects) - expected_selects)}")
    if set(filters) - expected_filters:
        stray = set(filters) - expected_filters
        # 필터 검색이 실패한 항목(retrieval_error)도 span을 남긴다 — 그건 귀속 불일치가 아니다.
        failed = {
            (item.get("revision"), item.get("item_index"))
            for item in tool_items
            if item.get("retrieval_error") and item["tech_domain"].get("value") is not None
        }
        if stray - failed:
            problems.append(f"필터 값이 없는 항목에 필터 span {sorted(stray - failed)}")
    return problems


def endpoint_errors(trace_records: list[dict] | None) -> dict:
    """LLM 호출 span 중 실패로 끝난 것을 센다 (ADR-026 대조표).

    **최종 실패만 보인다.** LiteLLM이 내부에서 재시도(`num_retries`)해 성공한 429·타임아웃은
    span에 남지 않는다 — 그 경우는 지연으로만 드러난다. 분류는 오류 문자열로 한다.
    """
    counts = {"total": 0, "rate_limit": 0, "timeout": 0, "other": 0}
    for record in trace_records or []:
        if record.get("type") != "generation":
            continue
        error = (record.get("metadata") or {}).get("error")
        if not error:
            continue
        text = str(error).lower()
        counts["total"] += 1
        if "429" in text or "ratelimit" in text or "rate limit" in text:
            counts["rate_limit"] += 1
        elif "timeout" in text or "timed out" in text:
            counts["timeout"] += 1
        else:
            counts["other"] += 1
    return counts


# ---------------------------------------------------------------------------
# 채점 · 집계
# ---------------------------------------------------------------------------


def _new_evidence_from_retry(findings) -> dict:
    """재검색 루프가 **새 근거**를 실제로 가져왔는지 판정한다.

    session-03의 열린 질문이다. "재검색을 돌았다"가 아니라 "재검색 회차에서만
    등장한 인용이 있는가"를 본다. 같은 문서를 다시 물어온 것은 새 근거가 아니다.
    """
    first_pass: set[tuple[str, str]] = set()
    retry_only: set[tuple[str, str]] = set()
    topics_gained: set[str] = set()

    for finding in findings:
        if finding.revision == 0:
            first_pass.update((c.doc_id, c.locator) for c in finding.citations)

    for finding in findings:
        if finding.revision == 0:
            continue
        for citation in finding.citations:
            key = (citation.doc_id, citation.locator)
            if key not in first_pass:
                retry_only.add(key)
                topics_gained.add(finding.topic)

    return {
        "retry_citations_total": sum(
            len(f.citations) for f in findings if f.revision > 0
        ),
        "new_citations": len(retry_only),
        "topics_gained": sorted(topics_gained),
    }


def _score(case: dict, result: dict) -> dict:
    """골든셋 기대와 실행 결과를 대조한다.

    자동으로 채점하는 것은 **기계적으로 셀 수 있는 것만**이다 (ADR-006과 같은
    원칙): 근거 없음 여부, 기대 문서 ID 적중. `must_mention`은 의미 판정이라
    자동화하지 않고 사람이 보도록 남긴다 — LLM 심판을 쓰면 판정 모델이 또 하나의
    변수가 되어 모델 고정 원칙(ADR-002)과 충돌한다.

    ⚠️ `min_citations`는 **판정에 넣지 않는다** (v1.2-S0b 결정). 넣으면 판정 기준이
    바뀌어 session-08 비교선이 끊긴다. 충족 여부는 `min_citations_met`에 따로 적는다.
    """
    expect = case.get("expect", {})
    topic_count = len(result["outline"])
    uncovered_count = len(result["uncovered"])
    all_uncovered = topic_count > 0 and uncovered_count == topic_count

    cited_docs = {c.doc_id for f in result["findings"] for c in f.citations}
    expected_docs = set(expect.get("expected_doc_ids") or [])

    checks = {}
    if expect.get("expect_uncovered"):
        # 음성 케이스: 모든 항목이 "근거 없음"으로 끝나야 통과다.
        checks["negative_case_held"] = all_uncovered
    else:
        checks["some_topic_covered"] = uncovered_count < topic_count
        if expected_docs:
            checks["expected_doc_cited"] = bool(expected_docs & cited_docs)

    min_citations = expect.get("min_citations")
    return {
        "checks": checks,
        "passed": all(checks.values()) if checks else None,
        "topic_count": topic_count,
        "uncovered_count": uncovered_count,
        "cited_doc_ids": sorted(cited_docs),
        "expected_doc_ids": sorted(expected_docs),
        # 기록만 한다. 판정(`passed`)에 영향 없음.
        "min_citations": min_citations,
        "min_citations_met": (
            None if min_citations is None else len(cited_docs) >= int(min_citations)
        ),
        "must_mention_manual": expect.get("must_mention") or [],
    }


def scoring_by_stratum(rows: Sequence[dict]) -> dict:
    """층별 pass/fail + n (ADR-022). **합산 pass/fail은 만들지 않는다.**

    층 A·B·C는 난이도도 판정 규칙도 다르다(C는 음성). 합치면 층 구성비가 바뀔 때
    숫자가 움직여 효과로 오독된다.
    """
    out: dict[str, dict] = {}
    for row in rows:
        bucket = out.setdefault(
            row.get("stratum") or "?", {"n": 0, "passed": 0, "failed": 0, "unscored": 0}
        )
        bucket["n"] += 1
        verdict = row["score"]["passed"]
        key = "passed" if verdict is True else ("failed" if verdict is False else "unscored")
        bucket[key] += 1
    return dict(sorted(out.items()))


def risk_selection(rows: Sequence[dict], risk_cases: Sequence[str] = RISK_CASES) -> dict:
    """ADR-025 효과 지표 — 위험군 조건부 선택률.

    분모: 위험군 케이스의 Researcher 호출(항목 × 회차) 중 정답 doc_id가 그 호출의 후보에
    있었던 것. 분자: 그중 정답이 `supporting`에 들어간 것. 위험군 순위는 골든셋 질의 기준이고
    파이프라인은 항목별로 검색하므로 조건부로 센다 (ADR-025 판정 기준).

    검색 오류 항목은 후보가 없으므로 자연히 분모에서 빠진다. 항목 기록이 없는 케이스
    (트레이스 없음·짝짓기 실패)는 `missing_items`에 따로 적는다 — 0으로 세지 않는다.
    """
    wanted = set(risk_cases)
    per_case: dict[str, dict] = {}
    missing: list[str] = []
    totals = {"all": [0, 0], "first_pass": [0, 0]}  # [분자, 분모]

    for row in rows:
        if row["case_id"] not in wanted:
            continue
        items = row.get("items")
        if items is None:
            missing.append(row["case_id"])
            continue
        expected = set(row["score"]["expected_doc_ids"])
        eligible = selected = fp_eligible = fp_selected = 0
        for item in items:
            candidate_ids = {c["doc_id"] for c in item["candidates"]}
            if not expected & candidate_ids:
                continue
            hit = bool(expected & set(item["supporting"]))
            eligible += 1
            selected += int(hit)
            if item["revision"] == 0:
                fp_eligible += 1
                fp_selected += int(hit)
        per_case[row["case_id"]] = {
            "calls": len(items),
            "eligible": eligible,
            "selected": selected,
            "first_pass_eligible": fp_eligible,
            "first_pass_selected": fp_selected,
        }
        totals["all"][0] += selected
        totals["all"][1] += eligible
        totals["first_pass"][0] += fp_selected
        totals["first_pass"][1] += fp_eligible

    def rate(pair: list[int]) -> float | None:
        return round(pair[0] / pair[1], 4) if pair[1] else None

    return {
        "risk_cases": list(risk_cases),
        "selected": totals["all"][0],
        "eligible": totals["all"][1],
        "rate": rate(totals["all"]),
        "first_pass_selected": totals["first_pass"][0],
        "first_pass_eligible": totals["first_pass"][1],
        "first_pass_rate": rate(totals["first_pass"]),
        "per_case": per_case,
        "missing_items": missing,
    }


def _dist(values: Sequence[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 3),
        "p50": round(_percentile(list(values), 50), 3),
        "p95": round(_percentile(list(values), 95), 3),
        "max": round(max(values), 3),
    }


def summarize(rows: Sequence[dict]) -> dict:
    """회차 1건의 요약. 행(row)만으로 계산한다 — 테스트가 이 함수를 직접 부른다."""
    walls = [r["wall_clock_s"] for r in rows]
    llms = [r["llm_latency_s"] for r in rows]
    others = [r["other_latency_s"] for r in rows]
    retrs = [r["retrieval_latency_s"] for r in rows]
    per_call = [r["llm_latency_s"] / r["llm_calls"] for r in rows if r["llm_calls"]]
    per_item = [r["llm_calls"] / r["topic_count"] for r in rows if r["topic_count"]]

    total_calls = sum(r["llm_calls"] for r in rows)
    node_calls: Counter = Counter()
    for r in rows:
        for node, bucket in r["by_node"].items():
            node_calls[node] += bucket["calls"]

    return {
        "latency": {
            "wall": _dist(walls),
            "llm": _dist(llms),
            "other": _dist(others),
            "retrieval": _dist(retrs),
            "llm_per_call_mean_s": round(statistics.fmean(per_call), 3) if per_call else 0.0,
            "percentile_method": "nearest-rank (관측값만 돌려준다)",
            "note": "워밍업(임베딩·Chroma·litellm 임포트)은 측정 전에 끝나 여기 포함되지 않는다.",
        },
        "tokens": {
            "prompt_total": sum(r["prompt_tokens"] for r in rows),
            "completion_total": sum(r["completion_tokens"] for r in rows),
            "billed_total": sum(r["billed_tokens"] for r in rows),
            "per_request": _dist([r["prompt_tokens"] + r["completion_tokens"] for r in rows]),
        },
        "calls": {
            "total": total_calls,
            "cache_hits": sum(r["cache_hits"] for r in rows),
            "by_node": dict(sorted(node_calls.items())),
            "per_request": _dist([r["llm_calls"] for r in rows]),
            "per_item": _dist(per_item),
            # HTTP 재시도는 트레이스에 잡히지 않는다. 실측이 아니라 상한이다.
            "endpoint_requests_upper_bound": total_calls * REQUESTS_PER_CALL_UPPER,
        },
        "topic_count_distribution": {
            str(k): v for k, v in sorted(Counter(r["topic_count"] for r in rows).items())
        },
        "revision_distribution": {
            str(k): v for k, v in sorted(Counter(r["revisions"] for r in rows).items())
        },
        "retry_loop": {
            "runs_with_retry": sum(1 for r in rows if r["revisions"] > 1),
            "retry_citations_total": sum(
                r["retry_evidence"]["retry_citations_total"] for r in rows
            ),
            "new_citations_total": sum(r["retry_evidence"]["new_citations"] for r in rows),
        },
        "scoring_by_stratum": scoring_by_stratum(rows),
        "min_citations_unmet": sorted(
            r["case_id"] for r in rows if r["score"]["min_citations_met"] is False
        ),
        "risk_selection": risk_selection(rows),
        "rows_without_local_trace": sorted(r["case_id"] for r in rows if not r["local_trace"]),
    }


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


def effective_concurrency(args) -> int:
    """노드에 넘기는 상한. off면 1 — 순차 경로(v1.2-S0c와 같은 코드 경로)다."""
    return args.max_concurrency if args.parallel == "on" else 1


def _run_case(case, *, args, label, run_index, provider, retriever, tracer, tracing_settings,
              llm_settings, embedding_settings) -> dict:
    trace = tracer.trace(
        "research_run",
        input={"query": case["query"]},
        metadata={
            "bench_label": label,
            "run_index": run_index,
            "case_id": case["id"],
            "prompt_version": PROMPT_VERSION,
            "model": llm_settings.model,
            "embedding_model": embedding_settings.model_name,
            "cache": args.cache,
            "max_revisions": args.max_revisions,
            "top_k": args.top_k,
            "parallel": args.parallel == "on",
            "max_concurrency": args.max_concurrency,
            "tech_domain_tool": args.tech_domain_tool == "on",
        },
    )
    app = compile_graph(
        provider, retriever=retriever, trace=trace, top_k=args.top_k,
        max_concurrency=effective_concurrency(args),
        tech_domain_vocab=(
            retriever.tech_domain_vocab() if args.tech_domain_tool == "on" else None
        ),
    )

    started = time.perf_counter()
    state = app.invoke(
        initial_state(case["query"], max_revisions=args.max_revisions),
        config={"recursion_limit": 50},
    )
    wall_s = time.perf_counter() - started

    records = state.get("trace") or []
    findings = state.get("findings") or []
    result = {
        "outline": state.get("outline") or [],
        "uncovered": state.get("uncovered") or [],
        "findings": findings,
    }

    llm_latency = sum(r.latency_s for r in records)
    by_node: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "prompt": 0, "completion": 0, "latency_s": 0.0, "cached": 0}
    )
    for record in records:
        bucket = by_node[record.node]
        bucket["calls"] += 1
        bucket["prompt"] += record.prompt_tokens
        bucket["completion"] += record.completion_tokens
        bucket["latency_s"] = round(bucket["latency_s"] + record.latency_s, 4)
        bucket["cached"] += int(record.cached)

    billed = sum(r.prompt_tokens + r.completion_tokens for r in records if not r.cached)

    trace.end(
        output={"draft": state.get("draft", "")},
        metadata={
            "bench_label": label,
            "wall_clock_s": round(wall_s, 3),
            "llm_latency_s": round(llm_latency, 3),
            "uncovered": result["uncovered"],
            "revisions": state.get("revision", 0),
        },
    )
    tracer.flush()

    trace_records = _read_trace(tracing_settings.local_trace_dir / f"{trace.run_id}.jsonl")
    if trace_records is None:
        print(
            f"  ⚠️ {case['id']}: 로컬 트레이스가 없습니다 — 검색 지연은 0으로, 항목별 후보는 "
            "없음으로 기록됩니다 (DISABLE_TRACING 또는 트레이스 경로 확인)."
        )
    retrieval_s, retrieval_calls = _retrieval_latency(trace_records)
    items = item_records(findings, trace_records)
    if trace_records is not None and items is None:
        print(f"  ⚠️ {case['id']}: 검색 span과 Finding을 짝지을 수 없어 항목별 기록을 비웁니다.")

    score = _score(case, result)
    return {
        "case_id": case["id"],
        "stratum": case.get("stratum"),
        "query": case["query"],
        "run_id": trace.run_id,
        "topic_count": score["topic_count"],
        "revisions": state.get("revision", 0),
        "wall_clock_s": round(wall_s, 3),
        "llm_latency_s": round(llm_latency, 3),
        "retrieval_latency_s": round(retrieval_s, 3),
        # 위 셋 중 어디에도 안 잡히는 시간 (JSON 파싱, 그래프 오버헤드 등).
        "other_latency_s": round(max(0.0, wall_s - llm_latency - retrieval_s), 3),
        "llm_calls": len(records),
        "cache_hits": sum(1 for r in records if r.cached),
        "retrieval_calls": retrieval_calls,
        "prompt_tokens": sum(r.prompt_tokens for r in records),
        "completion_tokens": sum(r.completion_tokens for r in records),
        "billed_tokens": billed,
        "by_node": {k: dict(v) for k, v in by_node.items()},
        "retry_evidence": _new_evidence_from_retry(findings),
        "score": score,
        "items": items,
        # None = 검사할 수 없음(트레이스·항목 기록 없음), [] = 불일치 없음 (ADR-026)
        "attribution_problems": (
            attribution_problems(items, trace_records) if items is not None else None
        ),
        "endpoint_errors": endpoint_errors(trace_records),
        "selection_errors": selection_errors(findings),
        "local_trace": trace_records is not None,
        "draft_chars": len(state.get("draft", "")),
    }


def _print_row(row: dict) -> None:
    verdict = row["score"]["passed"]
    mark = "PASS" if verdict else ("FAIL" if verdict is False else "----")
    nodes = " ".join(
        f"{node[:3]}{b['calls']}/{b['prompt'] + b['completion']}"
        for node, b in sorted(row["by_node"].items())
    )
    print(
        f"  [{mark}] {row['case_id']} 층{row['stratum']} T={row['topic_count']} "
        f"rev={row['revisions']} | wall {row['wall_clock_s']:>6.2f}s "
        f"llm {row['llm_latency_s']:>6.2f}s other {row['other_latency_s']:>5.2f}s | "
        f"calls {row['llm_calls']:>2} (hit {row['cache_hits']}) | "
        f"tok {row['prompt_tokens'] + row['completion_tokens']:>6} | {nodes}"
    )


def _print_summary(label: str, summary: dict) -> None:
    lat = summary["latency"]
    print("\n" + "-" * 72)
    print(f"요약 — {label} (n={summary['n_cases']})")
    print("-" * 72)
    for key, name in (("wall", "파이프라인"), ("llm", "LLM"), ("other", "기타")):
        d = lat[key]
        if d["n"]:
            print(f"  {name:<10}: p50 {d['p50']:.2f}s / p95 {d['p95']:.2f}s (n={d['n']})")
    calls = summary["calls"]
    print(
        f"  호출        : 총 {calls['total']} (요청 상한 ×{REQUESTS_PER_CALL_UPPER} = "
        f"{calls['endpoint_requests_upper_bound']}) / 항목당 평균 "
        f"{calls['per_item'].get('mean', 0)} / 노드별 {calls['by_node']}"
    )
    print(f"  토큰        : 총 {summary['tokens']['prompt_total'] + summary['tokens']['completion_total']}")
    print(f"  T 분포      : {summary['topic_count_distribution']}")
    print("  채점 (층별, 합산하지 않는다 — ADR-022):")
    for stratum, b in summary["scoring_by_stratum"].items():
        print(f"    층 {stratum}: {b['passed']} pass / {b['failed']} fail (n={b['n']})")
    risk = summary["risk_selection"]
    print(
        f"  위험군 조건부 선택률 (ADR-025): {risk['selected']}/{risk['eligible']} "
        f"(1회차 {risk['first_pass_selected']}/{risk['first_pass_eligible']})"
        + (f" ⚠️ 항목 기록 없음: {risk['missing_items']}" if risk["missing_items"] else "")
    )
    if summary["rows_without_local_trace"]:
        print(f"  ⚠️ 로컬 트레이스 없음: {summary['rows_without_local_trace']}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="골든셋 벤치마크 (지연·토큰·커버리지)")
    parser.add_argument("--label", required=True, help="측정 조건의 이름 (결과 파일명)")
    parser.add_argument("--repeat", type=int, default=1, help="회차 수. 2 이상이면 라벨에 -runN")
    parser.add_argument("--cache", action="store_true", help="LLM 응답 캐시를 켠다")
    parser.add_argument("--clear-cache", action="store_true", help="시작 전에 캐시를 비운다")
    parser.add_argument("--max-revisions", type=int, default=2)
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--only", nargs="*", default=None, help="특정 케이스 ID만")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--golden-set", type=Path, default=GOLDEN_SET)
    parser.add_argument(
        "--require-trace",
        action="store_true",
        help="로컬 트레이스·항목 기록이 없는 행이 생기면 경고가 아니라 즉시 멈춘다 (ADR-025 before 값 보호)",
    )
    parser.add_argument(
        "--parallel", choices=("on", "off"), default=None,
        help="노드 안 동시 호출 (ADR-026). 생략하면 RESEARCH_PARALLEL (비워두면 on)",
    )
    parser.add_argument(
        "--max-concurrency", type=int, default=None,
        help="동시 호출 상한. 생략하면 RESEARCH_MAX_CONCURRENCY (기본 4)",
    )
    parser.add_argument(
        "--tech-domain-tool", choices=("on", "off"), default=None,
        help="Researcher tech_domain 선택 도구 (v1.2-T2, ADR-028). 생략하면 RESEARCH_TECH_DOMAIN_TOOL (비워두면 off)",
    )
    args = parser.parse_args(argv)
    if args.tech_domain_tool is None:
        args.tech_domain_tool = "on" if load_tool_settings().tech_domain_tool else "off"
    concurrency = load_concurrency_settings()
    if args.parallel is None:
        args.parallel = "on" if concurrency.parallel else "off"
    if args.max_concurrency is None:
        args.max_concurrency = concurrency.max_concurrency
    if args.max_concurrency < 1:
        print(f"--max-concurrency는 1 이상이어야 한다: {args.max_concurrency}")
        return 2

    golden = json.loads(args.golden_set.read_text(encoding="utf-8"))
    cases = golden["cases"]
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {c["id"] for c in cases}
        if unknown:
            print(f"골든셋에 없는 케이스: {sorted(unknown)}")
            return 2
        cases = [c for c in cases if c["id"] in wanted]

    llm_settings = load_settings()
    embedding_settings = load_embedding_settings()
    tracing_settings = load_tracing_settings()
    store_settings = load_vectorstore_settings()
    labels = run_labels(args.label, args.repeat)

    print("=" * 72)
    print(f"골든셋 벤치마크 — 조건: {args.label} × {args.repeat}회")
    print("=" * 72)
    print("LLM        :", llm_settings.redacted())  # URL은 시크릿이다 (governance.md)
    print("임베딩     :", embedding_settings.model_name)
    print("계측       :", tracing_settings.redacted())
    print("프롬프트   :", PROMPT_VERSION)
    print("캐시       :", "ON" if args.cache else "OFF")
    print(
        "동시 호출  :",
        f"{args.parallel.upper()} (상한 {args.max_concurrency}, 실효 {effective_concurrency(args)})",
    )
    print("도구       :", f"tech_domain 선택 {args.tech_domain_tool.upper()}")
    print("골든셋     :", golden.get("version"), f"({len(cases)}건)")
    score_exposed = candidate_score_exposed()
    print("후보 점수  :", "노출 (ADR-025 적용 전)" if score_exposed else "미노출")

    try:
        out_paths = refuse_existing_outputs(args.out_dir, labels)
        index_check = check_index(store_settings.persist_dir, store_settings.collection)
        print(
            f"인덱스     : queue {index_check['queue_rows']} == 문서 {index_check['indexed_docs']} (clean)"
        )
        with index_lock(store_settings.persist_dir):
            return _bench(
                args, cases, golden, labels, out_paths, index_check, score_exposed,
                llm_settings=llm_settings,
                embedding_settings=embedding_settings,
                tracing_settings=tracing_settings,
                store_settings=store_settings,
            )
    except BenchRefused as exc:
        print(f"\n[거부] {exc}")
        return exc.exit_code


def _bench(args, cases, golden, labels, out_paths, index_check, score_exposed, *,
           llm_settings, embedding_settings, tracing_settings, store_settings) -> int:
    if args.clear_cache:
        removed = clear_cache(load_cache_settings())
        print(f"캐시 비움  : {removed}개 항목 삭제")

    # --- 워밍업 (측정 대상 아님, 기동 1회 비용) -------------------------------
    print("\n[1/3] 워밍업 (임베딩 · Chroma 첫 질의 · litellm 임포트)")
    embeddings, cold_s, warm_s = _warm_up_embeddings()
    # 워밍업한 인스턴스를 그대로 넘긴다. 새로 만들면 로딩을 다시 한다.
    retriever = ChromaRetriever(embeddings=embeddings, settings=store_settings)
    chroma_s = _warm_up_chroma(retriever, args.top_k)
    litellm_s = _warm_up_litellm()
    print(
        f"      임베딩 콜드 {cold_s:.2f}s -> 웜 {warm_s * 1000:.1f}ms | "
        f"Chroma 첫 질의 {chroma_s:.2f}s | litellm 임포트 {litellm_s:.2f}s"
    )

    corpus_docs = retriever.count()
    if corpus_docs != index_check["indexed_docs"]:
        raise BenchRefused(
            f"Chroma count()={corpus_docs} ≠ 인덱스 문서 {index_check['indexed_docs']}건.",
            EXIT_INDEX,
        )
    print(f"[2/3] 인덱스 문서 수: {corpus_docs}")

    tracer = get_tracer()
    if isinstance(tracer, NullTracer) and args.require_trace:
        raise BenchRefused(
            "트레이싱이 꺼져 있는데 --require-trace입니다. 항목별 후보를 기록할 수 없습니다.",
            EXIT_TRACE,
        )
    if isinstance(tracer, NullTracer):
        print(
            "      ⚠️ 트레이싱이 꺼져 있습니다(DISABLE_TRACING). 검색 지연과 항목별 후보가 "
            "기록되지 않습니다 — ADR-025 효과 지표를 계산할 수 없습니다."
        )
    provider = get_provider(cache=args.cache)

    warmup = {
        "embedding_cold_load_s": round(cold_s, 3),
        "embedding_warm_encode_ms": round(warm_s * 1000, 2),
        "chroma_first_query_s": round(chroma_s, 3),
        "litellm_import_s": round(litellm_s, 3),
        "note": "기동 1회 비용이며 아래 지연 수치에 포함되지 않는다.",
    }

    for run_index, (label, out_path) in enumerate(zip(labels, out_paths, strict=True), start=1):
        print(f"\n[3/3] {label}: 케이스 {len(cases)}건 실행\n")
        bench_started = time.perf_counter()
        rows: list[dict] = []
        selection_error_total = 0
        for case in cases:
            try:
                row = _run_case(
                    case, args=args, label=label, run_index=run_index, provider=provider,
                    retriever=retriever, tracer=tracer, tracing_settings=tracing_settings,
                    llm_settings=llm_settings, embedding_settings=embedding_settings,
                )
            except (TechDomainSelectionError, FilterValueError) as exc:
                # 사전 등록 §4: 강제가 깨졌다. "근거 없음"으로 삼키지 않고, 부분 결과도 남기지 않는다.
                tracer.flush()
                raise BenchRefused(
                    f"{case['id']}: {type(exc).__name__} — {exc}. 사전 등록 §4 정지 규칙.",
                    EXIT_SELECTION,
                ) from exc
            rows.append(row)
            _print_row(row)
            selection_error_total += row["selection_errors"]
            if selection_error_total >= SELECTION_ERROR_STOP:
                raise BenchRefused(
                    f"{row['case_id']}까지 selection_error 누적 {selection_error_total}건 "
                    f"(상한 {SELECTION_ERROR_STOP}). 사전 등록 §4 정지 규칙.",
                    EXIT_SELECTION,
                )
            if args.require_trace and (not row["local_trace"] or row["items"] is None):
                # 부분 결과는 저장하지 않는다 — before 값이 빠진 회차를 기준선으로 남기지 않는다.
                raise BenchRefused(
                    f"{row['case_id']}: 로컬 트레이스 또는 항목별 기록이 없습니다 "
                    f"(run_id={row['run_id']}). --require-trace라 여기서 멈춥니다.",
                    EXIT_TRACE,
                )
            if args.require_trace and row["attribution_problems"]:
                # span-항목 귀속이 어긋난 행은 후보·supporting·input_hash가 다른 항목의 것일 수
                # 있다. 그 위에서 계산한 지표는 틀린 짝 위의 지표다 — 부분 결과 없이 멈춘다 (ADR-026).
                raise BenchRefused(
                    f"{row['case_id']}: span-항목 귀속 불일치 {len(row['attribution_problems'])}건 "
                    f"(run_id={row['run_id']}): {row['attribution_problems'][:3]}. "
                    "--require-trace라 여기서 멈춥니다.",
                    EXIT_TRACE,
                )
        bench_s = time.perf_counter() - bench_started

        summary = {
            "label": label,
            "run_index": run_index,
            "runs_in_process": len(labels),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "n_cases": len(rows),
            "golden_set_version": golden.get("version"),
            "cache_enabled": args.cache,
            "candidate_score_exposed": score_exposed,
            "prompt_version": PROMPT_VERSION,
            "model": llm_settings.model,
            "embedding_model": embedding_settings.model_name,
            "corpus_docs": corpus_docs,
            "index_check": index_check,
            "max_revisions": args.max_revisions,
            "top_k": args.top_k,
            # 노드 안 동시 호출 (ADR-026). 지연 비교는 같은 세션의 off/on 짝으로만 한다.
            "parallel": args.parallel == "on",
            "max_concurrency": args.max_concurrency,
            "effective_concurrency": effective_concurrency(args),
            # tech_domain 선택 도구 (v1.2-T2). off면 v1.2-P1과 같은 경로다.
            "tech_domain_tool": args.tech_domain_tool == "on",
            "tech_domain_selection": tech_domain_selection_summary(rows),
            "endpoint_errors": {
                key: sum(r["endpoint_errors"][key] for r in rows)
                for key in ("total", "rate_limit", "timeout", "other")
            },
            "warmup": warmup if run_index == 1 else {"note": "1회차에서 이미 워밍업됨"},
            **summarize(rows),
            "bench_wall_clock_s": round(bench_s, 2),
            "rows": rows,
        }
        # 생성 직전에 한 번 더 확인한다 — 기동 검사 이후 다른 경로로 파일이 생겼을 수 있다.
        if out_path.exists():
            raise BenchRefused(f"결과 파일이 측정 중에 생겼습니다: {out_path.name}", EXIT_EXISTS)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _print_summary(label, summary)
        # --out-dir이 레포 밖일 수 있으므로(임시 디렉터리 등) 상대 경로를 강요하지 않는다.
        try:
            shown = out_path.relative_to(REPO_ROOT)
        except ValueError:
            shown = out_path
        print(f"\n저장: {shown}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
