# ADR-026: Researcher·Verifier 노드 안 LLM 호출 동시 실행 — 그래프 팬아웃 대신

- **Status:** Proposed
- **Date:** 2026-09-30
- **Decision:** Researcher(`nodes.py` 항목 루프)와 Verifier(outline 루프)의 항목별 LLM 호출을 **노드 안에서**
  스레드 풀로 동시에 보낸다. 그래프 구조·프롬프트(`PROMPT_VERSION 2026-09-30.1`)·판정 로직은 바꾸지 않는다.
  검색은 모듈 락으로 직렬화한다. 상한 기본 4, on/off는 `RESEARCH_PARALLEL` 한 줄(기본 꺼짐).
- **Scope:** multiagent-research-lab (`src/orchestrator/nodes.py` · `graph.py` · `src/obs/tracer.py` · `src/providers/config.py` · `scripts/bench_golden.py`)
- **Decision Source:** Human

---

## Context

### Problem

- 두 노드의 항목 루프가 직렬이라 질의당 순차 깊이 ≈ 호출 수 전체였다(session-17 §5.1). 그래프 수준에는 병렬 지점이 없다.
- v1.2의 하네스 변수는 한 번에 하나씩 바꾼다(ADR-002). 이번 변수는 **"노드 안 LLM 호출의 동시 실행 여부" 하나**다.
- 동시 요청은 서버 배칭을 바꿔 temperature 0 greedy 출력도 달라질 수 있다. "입력이 같았는가"(로직)와
  "출력이 같았는가"(배칭 비결정성)를 분리해서 답할 수 있어야 한다. span `input`은 4,000자에서 잘려 원문 대조가 안 된다.
- bench는 `researcher_retrieve` span을 Finding과 **파일 쓰기 순서로** 짝지었다(ADR-025 before 값). 동시 실행에서는 span이 완료 순서로 쓰인다.

### Constraints

- 그래프 불변, `Send` 미사용. 프롬프트·판정 불변 → `PROMPT_VERSION` 유지.
- 검색은 동시 실행 금지 — Chroma 질의가 인덱스 파일을 쓴다(v1.1 Session 3.5).
- 엔드포인트 동시 수용량은 우리가 볼 수 없다(KT 할당 GPU, 소유하지 않음) — 호출로만 확인 가능(session-17 §5.3).
- LLM 목적지는 `VLLM_BASE` 하나(ADR-021). 무료 할당분.

## Decision

### Selected

- **Technology:** 표준 라이브러리 `concurrent.futures.ThreadPoolExecutor` · `threading.Lock`. 새 의존성 없음.
- **Architecture:** 노드 함수 안에서 `_map_ordered(fn, items, max_concurrency)`로 항목을 처리하고
  **제출(항목) 순서로** 합친다. `max_concurrency ≤ 1`이면 풀을 만들지 않고 순차 루프(S0c 코드 경로)를 돈다.
  검색은 모듈 수준 `_RETRIEVAL_LOCK`으로 직렬 — 노드 인스턴스가 여럿이어도 같은 인덱스 파일을 쓰기 때문이다.
- **Implementation:**
  - 호출별 `input_hash` = sha256(메시지 · temperature · max_tokens, 캐시 키와 같은 직렬화)를 span metadata에.
  - 검색·호출 span에 `revision`·`item_index`. 락은 span을 열기 **전에** 잡고 대기 시간은 `lock_wait_s`로 따로 남긴다.
  - `_JsonlTrace._write`에 쓰기 락 — JSONL 한 줄 = 한 이벤트 계약 유지.
  - bench `item_records`는 키가 있으면 `(revision, item_index)`로 정렬 후 짝짓고 `items[].input_hash`를 붙인다.
    결과 JSON에 `parallel`·`max_concurrency`·`effective_concurrency`·`endpoint_errors`.
  - 설정: `RESEARCH_PARALLEL`(기본 꺼짐) · `RESEARCH_MAX_CONCURRENCY`(기본 4). bench는 `--parallel on|off` · `--max-concurrency`.

## Rationale

1. **팬아웃보다 노드 안 동시 호출이 변수 하나만 바꾼다.** 항목별 `Send`로 쪼개면 `revision`(reducer 없는 덮어쓰기,
   `state.py`)을 병렬 분기가 같은 스텝에 써서 `InvalidUpdateError`가 난다 — 회차 증가를 합류 노드로 옮겨야 하고,
   그건 그래프 구조 변경이다. Verifier는 매 회차 outline **전체**를 판정하는데, 섹션 단위로 쪼개면 판정 단위가 바뀐다(ADR-006 사안).
2. **off가 S0c와 같은 코드 경로여야 전/후 비교가 성립한다.** 상한 1이면 풀 없이 같은 순서로 같은 호출을 만든다.
   HEAD(S0c) `nodes.py`와 가짜 입력으로 state·호출 순서·검색 이벤트 순서가 전부 같음을 확인했고, 실측 off는 S0c와
   호출 297 · 토큰 313,047이 같다.
3. **검색 직렬화 근거는 정합성이다.** 검색 지연은 질의당 평균 0.21s로 LLM 지연(≈11s)보다 두 자릿수 작다 —
   직렬로 둬서 잃는 것이 거의 없고, 인덱스 파일 경합은 조용히 결과를 깨뜨리는 종류다.
4. **입력 해시를 기록해야 로직과 비결정성이 갈린다.** 해시가 같은데 출력이 다르면 구현이 아니라 서버 배칭 탓이다.
5. **동시간 대조로만 지연을 비교한다.** S0c와는 측정 시각이 달라 서버 부하가 다르다. off/on을 같은 세션에서 연달아
   재고(off 16:03–16:09 → on 16:09–16:14), 순서를 결과에 남긴다.

## Evidence

- **Benchmark:** 골든셋 2.0 30건 × 1회 × off/on, 캐시 끔, `--require-trace`, 상한 4, `endpoint_fp=1897f0ecc081`.
  wall p50 **11.89 → 9.56s** (−20%), p95 **20.83 → 14.40s** (−31%), 평균 11.50 → 8.45s, 케이스별 on/off 비 중앙값 **0.731**
  (범위 0.617–0.932). bench 전체 345.6 → 254.0s.
- **Experiment:**
  - 로직: Outliner 30/30 동일, 1회차 Researcher 입력 해시 **99/99 동일** → 결함 0. 트레이스상 전 호출 297/297 입력 동일(전 노드·전 회차).
  - 출력(판정 지표): off↔on 인용 집합 0 · pass/fail 0 · 1회차 supporting 0/99. S0c↔off(시간 간)도 0 · 0 · 0/99.
  - 관찰(판정 밖, 측정 후 추가): 입력이 같고 **텍스트**가 다른 호출 off↔on **15/297**(Researcher 5 · Verifier 10),
    S0c↔off **2/297**. 둘 다 구조 필드(`supporting`·`verdict`) 차이 **0** — `note`·`reason` 문장만 다르다.
    한 실행 안에서 같은 입력이 반복된 31쌍 중 5쌍은 순차에서도 텍스트가 달랐다(S0c · off · on 모두 5/31).
  - span-항목 귀속: 경로 확인 3행 + off 30 + on 30행 불일치 0(변조 사본으로 검사기 양성 대조). on에서 21/30행이 완료 순서로 쓰였고 전부 재짝지음.
  - 노드별 호출당 지연: Researcher 0.60 → 0.76s(+26%), Verifier 1.15 → 1.22s. Outliner 1.15s · Writer 3.97s는 직렬로 남는다.
- **Production Data:** 엔드포인트 오류(최종 실패) off 0 · on 0 (429·타임아웃 0). LiteLLM 내부 재시도는 span에 안 보인다.
- **Cost:** 논리 호출 624건(경로 확인 30 + off 297 + on 297), 승인 상한 1,320 이내. 0원(할당분).

## Alternatives

### 그래프 팬아웃 (LangGraph `Send`로 항목별 분기)

- **Pros:** 그래프 수준에서 병렬이 보이고 LangGraph가 합류를 관리한다. langgraph 1.1.4가 지원한다.
- **Cons:** `revision`·`uncovered`·`outline`이 reducer 없는 덮어쓰기라 병렬 분기가 같은 스텝에 `revision`을 쓰면
  `InvalidUpdateError`. 회차 증가를 합류 노드로 옮겨야 한다. Verifier를 쪼개면 판정 단위가 outline 전체 → 섹션으로 바뀐다.
- **Rejected because:** 그래프 구조와 Verifier 판정 단위가 함께 바뀌어 변수 하나만 바꾸는 원칙(ADR-002)을 어긴다. Verifier 단위 변경은 ADR-006 사안이다.

### 검색도 동시 실행

- **Pros:** 항목별 처리가 완전히 겹친다.
- **Cons:** Chroma 질의가 인덱스 파일을 쓴다(v1.1 Session 3.5) — 경합 시 인덱스 무결성이 깨질 수 있다. 얻는 지연은 질의당 약 0.2s.
- **Rejected because:** 정합성 위험에 비해 이득이 없다.

## Consequences

### Positive

- 질의당 wall 지연이 p95 기준 약 3분의 1 줄었고, 판정 지표(인용·pass/fail·supporting)는 off·S0c와 같다.
- 모든 LLM 호출에 `input_hash`가 남아, 이후 하네스 변경에서도 "입력이 같았는가"를 트레이스만으로 답할 수 있다.
- bench 짝짓기가 쓰기 순서에 의존하지 않는다.

### Negative

- 동시 실행에서 자유 텍스트(`note`·`reason`)의 호출 간 변동이 2/297 → 15/297로 늘었다. 지금 판정에 쓰이는 필드는 아니지만,
  Writer 입력이 텍스트를 받게 되면 영향이 생긴다(현재 Writer는 근거 snippet만 받는다).
- Researcher 호출당 지연이 +26% — 서버 쪽 경합. 상한을 올려도 비례해서 빨라지지 않는다.
- Outliner·Writer(질의당 약 5.1s)가 직렬로 남아 속도 향상 상한이 거기서 걸린다.

### Risks

- 엔드포인트 동시 수용량을 모른다. 상한 4에서 오류 0이었지만 다른 부하·다른 날은 재지 않았다. 내부 재시도로 흡수된 429는 보이지 않는다.
- Langfuse 백엔드의 스레드 안전성은 검증하지 않았다(bench·클라우드는 로컬 JSONL만 쓴다, ADR-016).
- 기본값이 꺼짐이라, 켠 채로 재야 할 이후 측정(T2·T3)에서 `--parallel on`을 빠뜨리면 기준(`v1.2-p1-on`)과 조건이 달라진다 — 결과 JSON의 `parallel`로 확인한다.

## Implementation

- [x] 노드 안 동시 호출 · 검색 락 · 입력 해시 · span 키 · JSONL 쓰기 락 (`c07a5a1`)
- [x] 테스트: off = S0c 경로(풀 미생성·호출 순서), 결과 순서 보존, 검색 직렬화(락 대기), span-항목 귀속, 상한 준수, 설정 검증 (224 → 246)
- [x] 판정 스크립트 `compare_p1_concurrency.py` — 측정 전 커밋 (`c07a5a1`)
- [x] 경로 확인 3건 · 동시간 대조 30 × 2 (`6323e91`)
- [ ] 기본값을 켤지 결정 (현재 꺼짐, T2·T3 비교 기준은 `v1.2-p1-on`)
- [ ] 다른 날·다른 부하에서의 오류·지연

## Reversibility

- **Reversible:** Yes
- **Rollback:** `RESEARCH_PARALLEL`을 비우거나 bench `--parallel off` — 순차 경로는 S0c 코드 경로와 같다. 코드 원복은 `c07a5a1` revert.
- **Migration Cost:** Low

## References

- **Related ADR:** ADR-002(하네스 변수 하나씩) · ADR-006(Verifier 판정) · ADR-007(계측) · ADR-008(캐시 기본 꺼짐과 같은 이유) · ADR-025(비교 기준 S0c)
- **Documentation:** `docs/handoff/session-17.md` §5 (P1 사전 조사) · `docs/eval/p1-concurrency-off-vs-on.json`(판정) ·
  `docs/eval/p1-trace-outputs-off-vs-on.json` · `docs/eval/p1-trace-outputs-s0c-vs-off.json`(관찰) ·
  `docs/eval/bench-v1.2-p1-{pathcheck,off,on}.json` · 커밋 `c07a5a1`(코드) `89b31fb`(관찰 스크립트) `6323e91`(결과)

## AI/ML Details

- **Model:** gemma-4-31B-it (고정, `VLLM_BASE`)
- **Evaluation:** 골든셋 2.0 30건, 층별 채점(ADR-022), 동시간 off/on 대조
- **Inference:** temperature 0, 노드 안 동시 요청 상한 4

### Evaluation

| Metric | Before (off) | After (on) | Target |
| ------ | -----: | ----: | -----: |
| wall p50 (s, n=30) | 11.89 | 9.56 | — |
| wall p95 (s, n=30) | 20.83 | 14.40 | — |
| 논리 호출 | 297 | 297 | 같음 |
| 1회차 Researcher 입력 해시 동일 | — | 99/99 | 전부 |
| 인용 집합 · pass/fail 차이 | — | 0 · 0 | 0 |
| 층별 pass (A · B · C) | 12/14 · 5/9 · 7/7 | 12/14 · 5/9 · 7/7 | 같음 |
| 엔드포인트 오류 | 0 | 0 | 0 |
