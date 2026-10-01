"""Sub-agent 노드 구현.

컨벤션(`.claude/rules/orchestrator.md`): 노드는 State를 받아 State를 반환하는
순수 함수다. 모델 호출·검색 같은 부작용은 노드가 직접 만들지 않고, 팩토리가 주입한
프로바이더와 검색 툴을 통해서만 일어난다 — 그래서 각 노드는 `make_*_node(...)`로
만들어지고, 반환된 함수 자체는 (의존성을 고정한 상태에서) State -> State다.

프로바이더를 임포트 시점이 아니라 팩토리 인자로 받는 이유: 테스트에서 가짜
프로바이더를 끼워 넣어 그래프 구조만 따로 검증할 수 있게 하기 위함이다.

**계측.** 각 팩토리는 `trace`(실행 1건에 대응하는 RunTrace)를 선택적으로 받는다.
없으면 계측 없이 동작한다 — 관측은 본 작업의 부수 효과지 전제 조건이 아니다.
모델 호출 1건 = span 1개이고, 토큰·지연은 프로바이더 응답(`LLMResponse`)에서
그대로 가져온다. 노드가 따로 토큰을 세지 않는다 (ADR-007).

**노드 안 동시 호출 (v1.2-P1, ADR-026).** Researcher·Verifier는 `max_concurrency`가
2 이상이면 항목별 호출을 스레드 풀로 동시에 보낸다. 그래프 구조·프롬프트·판정 로직은
그대로이고, 결과는 **원래 항목 순서로** 합친다. 1이면 풀을 만들지 않고 순차
루프를 그대로 돈다. 팩토리 인자의 기본은 1이고, 실행 진입점(bench·`run_research.py`)이
`load_concurrency_settings()`(기본 켜짐, 상한 4)를 읽어 넘긴다. 검색은 동시 실행에서도 **항상 직렬**이다(`_RETRIEVAL_LOCK`).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from src.obs import RunTrace
from src.providers import ChatMessage, LLMError, LLMProvider, LLMResponse
from src.tools.retrieval import FilterValueError, RetrievalError, RetrievedChunk, Retriever
from src.tools.sanitize import wrap_untrusted

from . import prompts
from .citations import attach_source_table
from .state import Citation, Finding, LLMCallRecord, ResearchState

# 노드의 계약: State를 받아 "이번에 바뀐 키만" 담은 부분 갱신을 돌려준다.
NodeFn = Callable[[ResearchState], ResearchState]

# 라우터의 계약: State를 읽기만 하고 다음 노드 이름을 돌려준다.
RouterFn = Callable[[ResearchState], str]

# 조사 항목 1건당 검색할 후보 문서 수. 늘리면 재현율이 오르지만 프롬프트가 길어지고
# 모델이 관련 없는 후보를 고를 여지도 커진다.
DEFAULT_TOP_K = 4

# 조사 항목이 "근거 있음"으로 인정되는 최소 인용 수 (ADR-006).
DEFAULT_MIN_CITATIONS = 1

# 근거 후보로 모델에 보여줄 때 문서 본문을 자르는 길이. 초록 하나가 보통
# 1,000~1,500자라 4건이면 프롬프트가 길어진다.
_SNIPPET_LIMIT = 900

# 노드 팩토리의 동시 호출 상한 기본값. 1 = 순차 (ADR-026). 노드를 직접 만드는 코드(테스트)가
# 스레드 순서에 기대지 않게 1로 둔다. **실행 기본값은 `ConcurrencySettings`가 정한다(켜짐, 4).**
DEFAULT_MAX_CONCURRENCY = 1

# 검색 직렬화. Chroma 질의가 인덱스 파일을 쓰므로(v1.1 Session 3.5) 같은 프로세스 안에서
# 검색이 겹치지 않게 한다. 노드마다가 아니라 **모듈에 하나** 두는 이유: 막으려는 것이
# 인덱스 파일 경합이라, 노드 인스턴스가 여럿이어도 같은 파일을 쓴다.
_RETRIEVAL_LOCK = threading.Lock()

_T = TypeVar("_T")

# `tech_domain` 선택 호출의 기권 값 (v1.2-T2). 통제어휘에 없는 문자열이어야 한다.
TECH_DOMAIN_ABSTAIN = "없음"


def tech_domain_schema(vocab: Iterable[str]) -> dict:
    """선택 호출의 `response_format` — enum = 통제어휘(정렬) + `"없음"` (v1.2-T2, ADR-028).

    어휘를 코드에 복제하지 않고 인자로 받는다(계약 §5). 정렬하는 이유: manifest 순회 순서가
    바뀌어도 스키마가 같아야 캐시 키·입력 해시가 흔들리지 않는다.
    """
    values = sorted(set(vocab))
    if TECH_DOMAIN_ABSTAIN in values:
        raise ValueError(f"통제어휘에 기권 값 {TECH_DOMAIN_ABSTAIN!r}이 들어 있다")
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "tech_domain_choice",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "tech_domain": {"type": "string", "enum": [*values, TECH_DOMAIN_ABSTAIN]},
                },
                "required": ["tech_domain"],
                "additionalProperties": False,
            },
        },
    }


class TechDomainSelectionError(RuntimeError):
    """선택 호출의 출력이 스키마를 어겼다 — json_schema 강제가 깨졌다 (ADR-028).

    **실행을 멈춘다.** 무필터로 폴백하면 강제 실패가 기권처럼 보이고, 사전 등록의 기권율·해악률이
    틀린 분모 위에서 계산된다(사전 등록 §4). 엔드포인트 오류(`LLMError`)는 이것이 아니다 —
    그쪽은 `selection_error`로 세고 무필터로 진행한다.
    """


def _parse_tech_domain(text: str, enum: Sequence[str]) -> tuple[str | None, str | None]:
    """(선택값, 위반 사유). 스키마가 강제되므로 **관대하게 파싱하지 않는다** — `_extract_json`의
    코드펜스·머리말 흡수를 쓰지 않는다. 흡수하면 강제가 꺼진 출력도 통과한다."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, "JSON이 아니다"
    if not isinstance(parsed, dict) or set(parsed) != {"tech_domain"}:
        return None, "키가 스키마와 다르다"
    value = parsed["tech_domain"]
    if value not in enum:
        return None, f"enum 밖의 값 {value!r}"
    return value, None


def _select_tech_domain(
    provider: LLMProvider,
    *,
    schema: dict,
    query: str,
    topic: str,
    trace: RunTrace | None,
    revision: int,
    item_index: int,
) -> tuple[str | None, str, LLMCallRecord | None]:
    """선택 호출 1회 → (검색에 걸 값 또는 None, 결과, 호출 기록).

    결과는 "chosen" | "abstain" | "selection_error". span(`researcher_select`)에는 원문 응답
    (output), 선택값·결과(metadata), 그리고 Researcher 호출과 같은 `(revision, item_index)`가 남는다.
    """
    enum = schema["json_schema"]["schema"]["properties"]["tech_domain"]["enum"]
    vocab = [value for value in enum if value != TECH_DOMAIN_ABSTAIN]

    def judged(response: LLMResponse) -> dict:
        value, problem = _parse_tech_domain(response.text, enum)
        if problem is not None:
            return {"selection_outcome": "violation", "violation": problem}
        return {
            "selected": value,
            "selection_outcome": "abstain" if value == TECH_DOMAIN_ABSTAIN else "chosen",
        }

    response = _call(
        provider,
        node="researcher_select",
        trace=trace,
        system=prompts.TECH_DOMAIN_SELECT_SYSTEM.format(
            vocab=", ".join(vocab), abstain=TECH_DOMAIN_ABSTAIN
        ),
        user=prompts.TECH_DOMAIN_SELECT_USER.format(query=query, topic=topic),
        max_tokens=64,
        span_name="researcher_select",
        span_metadata={"topic": topic, "revision": revision, "item_index": item_index},
        response_format=schema,
        response_metadata=judged,
    )
    if response is None:
        return None, "selection_error", None

    value, problem = _parse_tech_domain(response.text, enum)
    if problem is not None:
        raise TechDomainSelectionError(
            f"선택 호출 출력이 스키마를 어겼다({problem}) — 항목 {topic!r}, "
            f"응답 {response.text[:200]!r}. json_schema 강제가 깨졌으므로 멈춘다 (ADR-028)"
        )
    record = _record("researcher_select", response, revision)
    if value == TECH_DOMAIN_ABSTAIN:
        return None, "abstain", record
    return value, "chosen", record


class _KeyedTrace:
    """검색 계층이 여는 span에 항목 키를 붙인다 (v1.2-T2).

    `retrieval_filter` span은 검색 계층이 만들고, 검색 계층은 어느 항목의 검색인지 모른다.
    동시 실행에서는 span이 완료 순서로 쓰이므로 키 없이는 귀속을 검사할 수 없다.
    """

    def __init__(self, inner: RunTrace, *, revision: int, item_index: int) -> None:
        self._inner = inner
        self._key = {"revision": revision, "item_index": item_index}

    @property
    def run_id(self) -> str:
        return self._inner.run_id

    def span(self, name, *, kind="span", input=None):  # noqa: A002 - RunTrace 프로토콜 이름
        merged = {**input, **self._key} if isinstance(input, dict) else {"value": input, **self._key}
        return self._inner.span(name, kind=kind, input=merged)

    def end(self, **kwargs) -> None:  # pragma: no cover - 검색 계층은 trace를 닫지 않는다
        raise RuntimeError("_KeyedTrace는 span만 연다")


# ---------------------------------------------------------------------------
# 공통 헬퍼
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> object | None:
    """모델 출력에서 첫 JSON 값을 꺼낸다.

    형식을 지시해도 모델은 코드펜스나 머리말을 붙이곤 한다. 그때마다 재시도하면
    호출 수가 늘고 지연이 커지므로, 흔한 형태는 파싱 단계에서 흡수한다.
    파싱이 끝내 실패하면 예외가 아니라 None을 돌려주고, 호출한 노드가
    "빈 결과"로 처리한다 — 형식 실패가 파이프라인을 멈추지는 않게 한다.
    """
    if not text:
        return None

    stripped = text.strip()
    # ```json ... ``` 코드펜스 제거
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # 앞뒤에 설명이 붙은 경우: 첫 배열 또는 객체만 잘라낸다.
    for opener, closer in (("[", "]"), ("{", "}")):
        start = stripped.find(opener)
        end = stripped.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


def _input_hash(
    messages: Sequence[ChatMessage],
    *,
    temperature: float,
    max_tokens: int | None,
    response_format: dict | None = None,
) -> str:
    """호출 입력(메시지·파라미터)의 지문 (ADR-026).

    동시 실행이 "같은 입력을 보냈는가"를 트레이스만으로 답하려고 둔다. span의 `input`은
    `_safe()`가 4,000자에서 자르므로 원문 대조로는 답할 수 없다. 모델명은 넣지 않는다 —
    호출 지점이 모르는 값이고 실행 1건 안에서 고정이다(`LLMResponse.model`로 따로 남는다).
    직렬화 규칙은 캐시 키(`make_cache_key`)와 같다. `response_format`은 있을 때만 넣는다 —
    없는 호출의 해시는 v1.2-P1과 같아야 한다(도구 off = p1-on 입력 동일, v1.2-T2).
    """
    payload = {
        "messages": [[m.role, m.content] for m in messages],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        payload["response_format"] = response_format
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _map_ordered(
    fn: Callable[[int, str], _T], items: Sequence[str], max_concurrency: int
) -> list[_T]:
    """`fn(index, item)`을 항목마다 실행하고 **원래 순서로** 결과를 돌려준다 (ADR-026).

    `max_concurrency`가 1 이하면 풀을 만들지 않는다 — 순차 경로는 v1.2-S0c의 `for` 루프와
    같은 순서로 같은 호출을 만든다. 결과 순서는 완료 순서가 아니라 제출 순서다.
    """
    if max_concurrency <= 1 or len(items) <= 1:
        return [fn(index, item) for index, item in enumerate(items)]
    with ThreadPoolExecutor(
        max_workers=min(max_concurrency, len(items)), thread_name_prefix="node-call"
    ) as pool:
        futures = [pool.submit(fn, index, item) for index, item in enumerate(items)]
        return [future.result() for future in futures]


def _record(node: str, response: LLMResponse, revision: int) -> LLMCallRecord:
    """감사 로그 1건. 토큰·지연은 프로바이더가 실어 보낸 값을 그대로 쓴다."""
    return LLMCallRecord(
        node=node,
        model=response.model,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        latency_s=round(response.latency_s, 4),
        revision=revision,
        cached=response.cached,
    )


def _call(
    provider: LLMProvider,
    *,
    node: str,
    trace: RunTrace | None,
    system: str,
    user: str,
    max_tokens: int,
    span_name: str | None = None,
    span_metadata: dict | None = None,
    response_format: dict | None = None,
    response_metadata: Callable[[LLMResponse], dict] | None = None,
) -> LLMResponse | None:
    """모델 호출 + span 기록을 한 곳에 모은다.

    노드마다 span 열고 닫는 코드를 반복하면 한 군데를 빠뜨렸을 때 계측에 구멍이
    생긴다. 호출 경로를 하나로 두면 "모든 호출이 기록된다"가 구조적으로 보장된다.

    호출이 실패하면 None을 돌려준다. 한 조사 항목의 실패가 전체 실행을 죽이는 것보다,
    그 항목을 "근거 없음"으로 남기고 계속 가는 편이 낫다 — 그래야 Verifier가
    그 사실을 볼 수 있다.
    """
    messages = [ChatMessage("system", system), ChatMessage("user", user)]
    input_hash = _input_hash(
        messages, temperature=0.0, max_tokens=max_tokens, response_format=response_format
    )
    # 스키마가 없으면 프로바이더에 인자를 넘기지 않는다 — 기존 호출은 요청 본문까지 그대로다.
    extra = {"response_format": response_format} if response_format is not None else {}
    span = None
    if trace is not None:
        span = trace.span(
            span_name or f"{node}_call",
            kind="generation",
            input={"system": system, "user": user},
        )

    try:
        response = provider.complete(
            messages, temperature=0.0, max_tokens=max_tokens, **extra
        )
    except LLMError as exc:
        if span is not None:
            span.end(
                output=None,
                metadata={
                    "node": node,
                    "error": str(exc),
                    "input_hash": input_hash,
                    **(span_metadata or {}),
                },
            )
        return None

    if span is not None:
        span.end(
            output=response.text,
            metadata={
                "node": node,
                "tokens": response.total_tokens,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "latency_s": round(response.latency_s, 4),
                "finish_reason": response.finish_reason,
                "truncated": response.truncated,
                "cached": response.cached,
                "billed_tokens": response.billed_tokens,
                "prompt_version": prompts.PROMPT_VERSION,
                "input_hash": input_hash,
                **(span_metadata or {}),
                # 응답을 읽어야 정해지는 값(선택 호출의 선택값·판정). span을 닫기 전에 붙인다.
                **(response_metadata(response) if response_metadata is not None else {}),
            },
            usage={
                "input": response.prompt_tokens,
                "output": response.completion_tokens,
            },
            model=response.model,
        )
    return response


def _citations_by_topic(findings: Sequence[Finding]) -> dict[str, list[Citation]]:
    """재검색 루프에서 같은 항목이 여러 번 쌓인 것을 하나로 모은다.

    `findings`는 reducer로 누적되므로 2회차 조사 결과가 1회차 위에 덧붙는다.
    판정은 "지금까지 모인 근거 전체"를 대상으로 해야 한다.
    """
    merged: dict[str, list[Citation]] = {}
    for finding in findings:
        bucket = merged.setdefault(finding.topic, [])
        seen = {(c.doc_id, c.locator) for c in bucket}
        for citation in finding.citations:
            key = (citation.doc_id, citation.locator)
            if key not in seen:
                seen.add(key)
                bucket.append(citation)
    return merged


def _format_candidates(chunks: Sequence[RetrievedChunk]) -> str:
    """후보 문서를 프롬프트에 실을 형태로 만든다.

    본문은 **우리가 쓰지 않은 텍스트**다. 그대로 이어 붙이면 문서에 적힌 문장이
    지시로 읽힐 수 있으므로(간접 인젝션) 신뢰 경계로 감싼다 (ADR-009).
    제목도 감싼다 — 짧아서 안전해 보이지만 똑같이 외부 입력이다.

    **유사도 점수는 싣지 않는다 (ADR-025, session-19).** 절대 유사도는 질의 간 비교가
    성립하지 않는 값이라 근거 적합도 신호로 오도적이다. 후보는 이미 점수순이므로
    `[1]`~`[k]` 번호가 순위를 담는다.
    """
    lines = []
    for index, chunk in enumerate(chunks, start=1):
        body = chunk.text[:_SNIPPET_LIMIT]
        title_block = wrap_untrusted(chunk.title, label=f"후보 {index} 제목")
        body_block = wrap_untrusted(body, label=f"후보 {index} 본문")
        lines.append(
            f"[{index}] doc_id={chunk.doc_id}\n"
            f"{title_block}\n"
            f"{body_block}"
        )
    return "\n\n".join(lines) if lines else "(검색 결과 없음)"


def _format_evidence(citations: Sequence[Citation]) -> str:
    """근거를 Verifier·Writer 프롬프트에 실을 형태로 만든다.

    snippet은 검색된 문서 본문이 그대로 들어온 것이다. Researcher를 통과했다는
    사실이 내용을 신뢰할 근거가 되지는 않으므로 여기서도 경계로 감싼다 —
    파이프라인 뒷단일수록 "이미 검증된 것"으로 착각하기 쉽다 (ADR-009).

    ⚠️ **`Citation`의 온톨로지 메타는 여기 들어오지 않는다 (ADR-024).** 이 함수의
    출력이 곧 LLM 입력이고, 입력이 그대로여야 `PROMPT_VERSION`이 바뀌지 않으며
    "인용 형식 하나만 바뀌었다"가 참이 된다. 그리고 `release_type`을 모델에게 보여주면
    근거의 신뢰도 신호로 읽는다 — Researcher 프롬프트의 유사도 점수 노출과 같은
    함정이다(session-13 §6.2). 메타는 `citations.render_source_table()`만 쓴다.
    `tests/test_citations.py`가 이 함수의 출력에 메타가 섞이지 않는지 붙든다.
    """
    if not citations:
        return "(근거 없음)"
    return "\n\n".join(
        f"[{c.doc_id} / {c.locator}]\n"
        + wrap_untrusted(c.snippet[:_SNIPPET_LIMIT], label=c.doc_id)
        for c in citations
    )


# ---------------------------------------------------------------------------
# Outliner
# ---------------------------------------------------------------------------


def make_outliner_node(
    provider: LLMProvider, *, trace: RunTrace | None = None
) -> NodeFn:
    """질의를 조사 항목 목록으로 분해한다.

    반환 키: `outline`, `trace`
    """

    def outliner(state: ResearchState) -> ResearchState:
        query = state.get("query", "")
        revision = state.get("revision", 0)

        response = _call(
            provider,
            node="outliner",
            trace=trace,
            system=prompts.OUTLINER_SYSTEM,
            user=prompts.OUTLINER_USER.format(query=query),
            max_tokens=512,
        )
        if response is None:
            # 호출 자체가 실패했다. 재시도하지 않고 빈 outline을 돌려준다 —
            # 어떻게 할지는 라우터가 판단한다.
            return ResearchState(outline=[])

        parsed = _extract_json(response.text)
        outline: list[str] = []
        if isinstance(parsed, list):
            outline = [str(item).strip() for item in parsed if str(item).strip()]

        return ResearchState(
            outline=outline,
            trace=[_record("outliner", response, revision)],
        )

    return outliner


# ---------------------------------------------------------------------------
# Researcher
# ---------------------------------------------------------------------------


def make_researcher_node(
    provider: LLMProvider,
    *,
    retriever: Retriever | None = None,
    trace: RunTrace | None = None,
    top_k: int = DEFAULT_TOP_K,
    sources: Sequence[str] | None = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    tech_domain_vocab: Iterable[str] | None = None,
) -> NodeFn:
    """조사 항목별로 근거 문서를 검색·추출한다.

    재검색 루프에서 여러 번 호출되며, 매번 `uncovered`에 남은 항목만 다시 다룬다.
    근거를 찾지 못한 항목은 citations가 빈 Finding으로 남긴다 — 조용히 누락시키지
    않아야 검증 단계가 그 사실을 볼 수 있다.

    `sources`는 검색 스코프 화이트리스트다. None이면 인덱스 전체를 검색하되,
    인덱스에는 애초에 공개 출처만 들어 있다 (ADR-004, `src/tools/corpus.py`).

    `max_concurrency` ≥ 2면 항목들을 동시에 처리한다(검색은 직렬, LLM 호출만 겹친다).
    `findings`·`trace`는 항목 순서 그대로다 (ADR-026).

    **`tech_domain` 선택 도구 (v1.2-T2, ADR-028).** `tech_domain_vocab`을 주면 도구가 켜진다.
    1회차 항목마다 검색 **전에** 선택 호출 1회(json_schema, enum = 어휘 + "없음")를 하고,
    고른 값으로 strict 필터 검색을 한다(ADR-027). "없음"이면 무필터다. 재시도 회차는 다시
    고르지 않고 1회차 값을 쓴다(사전 등록 D1). 선택 호출은 항목 처리 안에 있으므로 동시 실행
    경로를 그대로 탄다 — 검색 락은 그대로다. None이면(기본) 선택 호출도, 검색 인자도, span
    입력도 v1.2-P1과 같다.

    반환 키: `findings`(누적), `revision`, `trace`
    """
    schema = tech_domain_schema(tech_domain_vocab) if tech_domain_vocab is not None else None

    def researcher(state: ResearchState) -> ResearchState:
        query = state.get("query", "")
        revision = state.get("revision", 0)
        outline = state.get("outline") or []
        uncovered = state.get("uncovered") or []

        # 1회차는 전체 항목, 재검색은 아직 근거를 못 찾은 항목만 다룬다.
        is_retry = revision > 0 and bool(uncovered)
        topics = uncovered if is_retry else outline

        # 재시도 회차가 재사용할 1회차 선택. Finding에 실려 있으므로 State 키를 새로 만들지 않는다.
        first_choice = {
            f.topic: (f.tech_domain, f.tech_domain_outcome)
            for f in state.get("findings") or []
            if f.revision == 0 and f.tech_domain_outcome is not None
        }

        def research_one(index: int, topic: str) -> tuple[Finding, list[LLMCallRecord]]:
            records: list[LLMCallRecord] = []
            tech_domain: str | None = None
            outcome: str | None = None
            source: str | None = None  # span 기록용: "selected" | "reused"
            if schema is not None and retriever is not None:
                if is_retry:
                    if topic not in first_choice:
                        raise TechDomainSelectionError(
                            f"재시도 항목 {topic!r}의 1회차 선택이 없다 — 재사용할 값이 없다"
                        )
                    tech_domain, outcome = first_choice[topic]
                    source = "reused"
                else:
                    tech_domain, outcome, record = _select_tech_domain(
                        provider, schema=schema, query=query, topic=topic, trace=trace,
                        revision=revision, item_index=index,
                    )
                    source = "selected"
                    if record is not None:
                        records.append(record)

            def finding(citations: tuple[Citation, ...] = ()) -> Finding:
                return Finding(
                    topic=topic, citations=citations, revision=revision,
                    tech_domain=tech_domain, tech_domain_outcome=outcome,
                )

            chunks = _retrieve(
                retriever, topic, query=query, top_k=top_k, sources=sources, trace=trace,
                revision=revision, item_index=index,
                tech_domain=tech_domain, tech_domain_source=source, tech_domain_outcome=outcome,
            )
            if not chunks:
                return finding(), records

            user = prompts.RESEARCHER_USER.format(
                query=query, topic=topic, candidates=_format_candidates(chunks)
            )
            if is_retry:
                user += prompts.RESEARCHER_RETRY_HINT

            response = _call(
                provider,
                node="researcher",
                trace=trace,
                system=prompts.RESEARCHER_SYSTEM,
                user=user,
                max_tokens=384,
                span_metadata={
                    "topic": topic,
                    "candidates": len(chunks),
                    "retry": is_retry,
                    "revision": revision,
                    "item_index": index,
                },
            )
            if response is None:
                return finding(), records

            records.append(_record("researcher", response, revision))
            return finding(_select_citations(response.text, chunks)), records

        results = _map_ordered(research_one, topics, max_concurrency)
        findings = [finding for finding, _ in results]
        records = [record for _, item_records in results for record in item_records]

        return ResearchState(
            findings=findings,
            revision=revision + 1,
            trace=records,
        )

    return researcher


def _retrieve(
    retriever: Retriever | None,
    topic: str,
    *,
    query: str,
    top_k: int,
    sources: Sequence[str] | None,
    trace: RunTrace | None,
    revision: int = 0,
    item_index: int = 0,
    tech_domain: str | None = None,
    tech_domain_source: str | None = None,
    tech_domain_outcome: str | None = None,
) -> list[RetrievedChunk]:
    """검색 툴 호출 + span 기록.

    검색어는 조사 항목 단독이 아니라 전체 질의와 함께 만든다 — 항목만 쓰면
    "한계점" 같은 짧은 항목이 문맥을 잃는다.

    **검색은 직렬이다 (ADR-026).** 락을 잡은 뒤에 span을 연다 — span 지연은 검색 자체만
    재고, 락을 기다린 시간은 `lock_wait_s`로 따로 남긴다. span에 `revision`·`item_index`를
    싣는 이유: 동시 실행에서는 span이 파일에 **완료 순서로** 쓰이므로, bench가 span을
    Finding과 짝지을 때 쓰기 순서가 아니라 이 키로 정렬한다.

    **필터 (v1.2-T2).** `tech_domain_source`가 None이면(도구 off) span 입력과 검색 호출이
    v1.2-P1과 같다. 도구가 켜져 있으면 span 입력에 필터 값·출처(selected/reused)·1회차 결과를
    싣고, 값이 있을 때만 `tech_domain`·`trace`를 검색에 넘긴다. 검색 계층이 남기는
    `retrieval_filter` span에도 같은 `(revision, item_index)`를 붙인다 — 그 span은 필터 후
    결과 수를 담는데, 키가 없으면 동시 실행에서 어느 항목의 것인지 알 수 없다.
    `FilterValueError`는 삼키지 않는다 — span에 남기고 다시 던진다(ADR-027, 사전 등록 §4).
    """
    if retriever is None:
        return []

    search_text = f"{query} {topic}".strip()
    span_input = {
        "topic": topic,
        "search_text": search_text,
        "k": top_k,
        "revision": revision,
        "item_index": item_index,
    }
    filter_kwargs: dict = {}
    if tech_domain_source is not None:
        span_input.update(
            tech_domain=tech_domain,
            tech_domain_source=tech_domain_source,
            tech_domain_outcome=tech_domain_outcome,
        )
        if tech_domain is not None:
            filter_kwargs = {
                "tech_domain": tech_domain,
                "trace": _KeyedTrace(trace, revision=revision, item_index=item_index)
                if trace is not None else None,
            }
    wait_started = time.perf_counter()
    with _RETRIEVAL_LOCK:
        lock_wait_s = round(time.perf_counter() - wait_started, 4)
        span = None
        if trace is not None:
            span = trace.span("researcher_retrieve", kind="span", input=span_input)

        try:
            chunks = retriever.search(search_text, k=top_k, sources=sources, **filter_kwargs)
        except FilterValueError as exc:
            if span is not None:
                span.end(output=None, metadata={"error": f"FilterValueError: {exc}",
                                                "lock_wait_s": lock_wait_s})
            raise
        except RetrievalError as exc:
            if span is not None:
                span.end(output=None, metadata={"error": str(exc), "lock_wait_s": lock_wait_s})
            return []

        if span is not None:
            span.end(
                output=[{"doc_id": c.doc_id, "score": c.score} for c in chunks],
                metadata={"hits": len(chunks), "lock_wait_s": lock_wait_s},
            )
    return chunks


def _select_citations(
    model_output: str, chunks: Sequence[RetrievedChunk]
) -> tuple[Citation, ...]:
    """모델이 고른 후보 번호를 Citation으로 바꾼다.

    파싱에 실패하면 빈 튜플이다. 파싱 실패 시 "그냥 상위 후보를 쓴다"로 처리하지
    않는 이유: 그러면 모델이 "뒷받침하는 문서 없음"이라고 판단한 경우와 형식을
    어긴 경우를 구분할 수 없게 되고, 근거 없는 인용이 보고서에 실린다.
    """
    parsed = _extract_json(model_output)
    if not isinstance(parsed, dict):
        return ()

    raw_indices = parsed.get("supporting")
    if not isinstance(raw_indices, list):
        return ()

    citations: list[Citation] = []
    for raw in raw_indices:
        try:
            index = int(raw)
        except (TypeError, ValueError):
            continue
        if not 1 <= index <= len(chunks):
            continue  # 모델이 없는 번호를 지어낸 경우
        chunk = chunks[index - 1]
        citations.append(
            Citation(
                doc_id=chunk.doc_id,
                locator=chunk.locator,
                snippet=chunk.text[:_SNIPPET_LIMIT],
                # 온톨로지 메타는 검색 결과에서 **그대로** 옮긴다 (ADR-024).
                # 모델 출력에서 읽지 않는다 — 모델이 고른 것은 후보 번호뿐이다.
                published=chunk.published,
                release_type=chunk.release_type,
                tech_domains=chunk.tech_domains,
                has_ontology=chunk.has_ontology,
            )
        )
    return tuple(citations)


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


def make_verifier_node(
    provider: LLMProvider,
    *,
    trace: RunTrace | None = None,
    min_citations: int = DEFAULT_MIN_CITATIONS,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> NodeFn:
    """각 조사 항목에 실제로 근거가 붙었는지 판정한다.

    Writer 앞에 두는 이유: 근거 없는 항목을 Writer에게 넘기면 모델이 그 빈칸을
    메우려 든다. 근거 유무 판정을 쓰기 전에 끝내야 "근거 없으면 문장을 만들지
    않는다"는 원칙이 구조적으로 지켜진다.

    판정은 두 단계다 (ADR-006):

    1. **기계 판정** — 인용이 `min_citations`개 미만이면 모델을 부르지 않고
       바로 uncovered. 셀 수 있는 것을 모델에게 묻지 않는다. 호출 수와 지연이
       줄고, 판정이 결정적(deterministic)이 된다.
    2. **모델 판정** — 인용이 붙은 항목만, 그 근거가 실제로 항목을 뒷받침하는지
       묻는다. 검색은 주제가 비슷하기만 해도 걸리므로 이 단계가 필요하다.

    `max_concurrency` ≥ 2면 항목별 모델 판정을 동시에 보낸다. 판정 단위(매 회차 outline
    전체)는 바꾸지 않는다 — 그건 ADR-006 사안이다. `uncovered`는 outline 순서 그대로다.

    반환 키: `uncovered`, `trace`
    """

    def verifier(state: ResearchState) -> ResearchState:
        revision = state.get("revision", 0)
        outline = state.get("outline") or []
        by_topic = _citations_by_topic(state.get("findings") or [])

        def verify_one(index: int, topic: str) -> tuple[bool, LLMCallRecord | None]:
            """(covered 여부, 호출 기록)."""
            citations = by_topic.get(topic, [])

            if len(citations) < min_citations:
                return False, None

            response = _call(
                provider,
                node="verifier",
                trace=trace,
                system=prompts.VERIFIER_SYSTEM,
                user=prompts.VERIFIER_USER.format(
                    topic=topic, evidence=_format_evidence(citations)
                ),
                max_tokens=256,
                span_metadata={
                    "topic": topic,
                    "citations": len(citations),
                    "revision": revision,
                    "item_index": index,
                },
            )
            if response is None:
                # 판정하지 못한 항목은 통과시키지 않는다. 검증 실패를 통과로
                # 처리하면 검증 단계가 있으나 마나가 된다.
                return False, None

            parsed = _extract_json(response.text)
            verdict = ""
            if isinstance(parsed, dict):
                verdict = str(parsed.get("verdict", "")).strip().lower()
            return verdict == "covered", _record("verifier", response, revision)

        results = _map_ordered(verify_one, outline, max_concurrency)
        uncovered = [
            topic for topic, (covered, _) in zip(outline, results, strict=True) if not covered
        ]
        records = [record for _, record in results if record is not None]

        return ResearchState(uncovered=uncovered, trace=records)

    return verifier


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def make_writer_node(
    provider: LLMProvider, *, trace: RunTrace | None = None
) -> NodeFn:
    """근거에 붙은 초안을 작성한다.

    Researcher가 반환한 근거 범위 밖의 주장은 생성하지 않는다. 근거가 없는 항목은
    본문에 "근거 없음"으로 표시한다 (docs/architecture.md 설계 원칙).

    **출처 표는 모델이 아니라 코드가 붙인다 (ADR-024).** 모델은 지금까지와 똑같은
    입력을 받고 똑같이 본문만 쓴다. 그 뒤에 인용된 문서의 온톨로지 메타
    (종류·기술 영역·발행일)를 표로 렌더링해 덧붙인다 — 값이 모델을 거치지 않으므로
    옮겨 적기 오류가 원리적으로 생기지 않는다.

    반환 키: `draft`, `trace`
    """

    def writer(state: ResearchState) -> ResearchState:
        query = state.get("query", "")
        revision = state.get("revision", 0)
        outline = state.get("outline") or []
        uncovered = set(state.get("uncovered") or [])
        by_topic = _citations_by_topic(state.get("findings") or [])

        blocks: list[str] = []
        cited: list[Citation] = []
        for topic in outline:
            if topic in uncovered:
                blocks.append(f"## {topic}\n(근거 없음 — 검색에서 뒷받침 자료를 찾지 못함)")
                continue
            topic_citations = by_topic.get(topic, [])
            cited.extend(topic_citations)
            blocks.append(f"## {topic}\n{_format_evidence(topic_citations)}")

        if not blocks:
            # 조사 항목 자체가 없다. 지어내지 않고 그 사실을 초안으로 남긴다.
            return ResearchState(
                draft=(
                    f"# {query}\n\n조사 항목을 만들지 못해 보고서를 작성할 수 없습니다. "
                    "(Outliner 단계 실패)"
                )
            )

        response = _call(
            provider,
            node="writer",
            trace=trace,
            system=prompts.WRITER_SYSTEM,
            user=prompts.WRITER_USER.format(query=query, findings="\n\n".join(blocks)),
            max_tokens=2048,
        )
        if response is None:
            return ResearchState(
                draft=f"# {query}\n\n보고서 생성에 실패했습니다 (모델 호출 실패)."
            )

        return ResearchState(
            # 본문은 모델이, 출처 표는 코드가 쓴다 (ADR-024). 본문은 한 글자도 바꾸지 않는다.
            draft=attach_source_table(response.text.strip(), cited),
            trace=[_record("writer", response, revision)],
        )

    return writer


# ---------------------------------------------------------------------------
# 라우터
# ---------------------------------------------------------------------------


def route_after_verify(state: ResearchState) -> str:
    """검증 후 분기 — 재검색할지 초안 작성으로 넘어갈지.

    라우터는 State를 읽기만 하는 순수 함수이며 모델을 호출하지 않는다.
    루프 종료 조건을 여기 한 곳에 모아두어야 "왜 루프가 멈췄는가"를
    그래프 정의만 보고 답할 수 있다.
    """
    uncovered = state.get("uncovered") or []
    if not uncovered:
        return "write"

    if state.get("revision", 0) >= state.get("max_revisions", 0):
        # 상한 도달: 남은 항목은 "근거 없음"으로 표시된 채 초안으로 넘어간다.
        return "write"

    return "retry"
