# ADR-027: tech_domains 필터를 검색 계층에 배선한다 — 전수 조회 + 후처리 (재인덱싱 없음)

- **Status:** Accepted (2026-10-01, session-21 — 사용자 결정)
- **Date:** 2026-10-01
- **Decision:** `ChromaRetriever.search()`에 선택적 인자 `tech_domain`을 추가한다. 값을 주면 **항상 전수**(`collection.count()`, `sources`가 있으면 그 출처의 문서 수)를 순위로 받아 strict로 거른 뒤 상위 `k`건을 돌려준다. `None`이면 기존 경로를 그대로 탄다. 저장 형태와 인덱스는 바꾸지 않는다.
- **Scope:** multiagent-research-lab (`src/tools/retrieval.py` · `scripts/probe_retrieval.py` · v1.2-T1)
- **Decision Source:** Human

---

## Context

### Problem

v1.1(ADR-023)은 `tech_domain` 필터를 **프로브 안의 후처리 arm**으로만 배선했다. 그때 오라클 값으로
층 A ko 재현율 14/14, 간격 0.0286/0.0502를 얻었다. 파이프라인이 필터를 쓰려면(T2·T3) 검색 계층에
같은 필터가 있어야 한다. 이때 **v1.1 프로브와 같은 결과를 낸다는 것**을 보여야 한다.

`tech_domains`는 `|`로 이은 문자열이다. Chroma 1.5.5에는 부분 일치 연산자가 없어서
`where`로 거를 수 없다(ADR-020, `contract_import._LIST_JOIN`). 선택지는 두 가지였다.

- **A** — 도메인별 불리언 키로 펼쳐 저장하고 재인덱싱한 뒤, Chroma `where`로 거른다.
- **B** — 큰 k로 받아 검색 계층 안에서 후처리한다.

### Constraints

- T2·T3의 비교 기준은 `docs/eval/bench-v1.2-p1-on.json`이다(session-20). 이 측정은 **현재 인덱스** 위에서 이루어졌다.
- 에이전트, 프롬프트, LLM 호출은 범위 밖이다. 이 결정은 검색 계층만 바꾼다(ADR-002, 한 번에 하나만 바꾼다).
- HNSW는 근사 검색이라 N건을 물어도 N-1건만 오는 실행이 있다(ADR-005 Amendment 3). 깨끗한 인덱스에서는 0/120이었다(Amendment 4).
- 통제어휘는 복제하지 않고 export manifest에서 읽는다(계약 §5).

## Decision

### Selected

- **Technology:** 추가 의존성 없음. Chroma 1.5.5 그대로이고, 저장 형태(`tech_domains` 연결 문자열)도 그대로다.
- **Architecture:** 필터는 검색 계층(`ChromaRetriever`)에 둔다. 노드는 아직 이 인자를 넘기지 않는다. 질의에서 필터 값을 고르는 플래너는 범위 밖이다.
- **Implementation:**
  1. `search(query, *, k=4, sources=None, tech_domain=None, trace=None)`. `Retriever` 프로토콜에도 같은 선택 인자를 추가했다.
  2. **`tech_domain=None`이면 기존 코드 경로와 같다.** `n_results=k`이고 `count()`·`get()`을 호출하지 않으며, `trace`도 쓰지 않는다. 필터 경로는 별도 메서드 `_search_tech_domain`이다.
  3. **k는 설정값이 아니다.** 받아오는 건수는 항상 컬렉션 문서 수다(`sources`가 있으면 `get(where=…)` 건수). k<N 경로는 만들지 않았다. 호출자의 `k`는 필터 **뒤**에 자르는 데만 쓴다.
  4. **strict.** `tech_domain in chunk.tech_domains`인 문서만 남긴다. 온톨로지 메타가 없는 문서는 탈락하고, 생존자 순서는 무필터 순위 그대로다. v1.1 프로브 `_survives(..., "strict")`와 같은 정의다.
  5. **통제어휘 밖 값은 `FilterValueError`(`ValueError`)로 처리한다.** `RetrievalError`가 아니다. Researcher가 `RetrievalError`를 "근거 없음"으로 삼키면, 오타가 strict의 생존자 0과 구별되지 않는다. 어휘는 export manifest들의 합집합이고 생성자에서 주입할 수 있다.
  6. **전수가 오지 않으면 `RetrievalError`로 처리한다.** 빠진 문서가 필터 생존자일 수 있어서 조용히 넘기지 않는다.
  7. **트레이스.** `trace`를 주면 `retrieval_filter` span에 `tech_domain`·`null_policy`·`k`를 입력으로, `expected`·`fetched`·`survivors`·`returned`를 메타로 남긴다.
  8. 프로브에 `--filter-impl {postprocess,retrieval}`를 추가했다(기본값 postprocess = v1.1). `retrieval`은 `tech_domain` strict에서만 허용한다. 필터 값이 선언되지 않은 층 B 케이스는 검색 인자로 표현할 수 없다(None은 무필터를 뜻한다). 그래서 strict의 정의대로 생존자 0으로 둔다.

## Rationale

1. **재인덱싱이 없으므로 p1-on 기준이 그대로 유지된다.** 인덱스 파일을 건드리지 않았으므로 T2·T3가 옛 인덱스 위의 `bench-v1.2-p1-on`을 기준으로 쓰는 데 별도 논증이 필요 없다. 그래도 아래 Evidence의 동일성 대조로 확인했다.
2. **v1.1 프로브를 구조적으로 그대로 옮긴 것이다.** 프로브 역시 k=37로 전부 받아 후처리했다. 전수 조회에 같은 판정식을 쓰므로 동일성이 정의상 기대되고, 실측도 그랬다.
3. **k를 전수로 고정해 B의 약점(k<N이면 필터 뒤 상위 문서가 조용히 빠진다)을 경로째 없앴다.** 남은 약점인 HNSW 유실은 예외로 드러나게 했다.

## Evidence

- **Experiment (None 경로 동일성):** 새 코드로 `probe_retrieval.py`(무필터)를 돌린 결과가 `probe-retrieval-session-14-baseline-nofilter.json`, `…session-16-after-chunk-metadata.json`과 **시간 값을 제외하고 바이트 단위로 같다**(`compare_probe_runs.py`, sha256 `00f6353b…`). 새로 추가한 `summary.filter_impl` 키 1개만 다르고, 이 키를 빼면 sha256까지 같다. 골든 30건 × ko·en, k=37 전 순위가 대상이다.
- **Experiment (오라클 동일성):** `--arm tech_domain --null-policy strict --filter-impl retrieval`의 결과가 `probe-retrieval-session-14-tech-domain-strict.json`과 **바이트 단위로 같다**(sha256 `6463fa18…`, `filter_impl` 키 제외). 층 A ko **14/14**, en 14/14, 층 B strict ko·en **0/9**, GS-013 앵커 간격 ko **0.0286(최난도) / 0.0502(평균)**, en 0.0705 / 0.0900, negative n=7. 점수 허용오차는 **0**이다(사용자 결정).
- **Experiment (실제 파이프라인 검색어 재생):** `scripts/replay_retrieval_trace.py`를 썼다. p1-on·p1-off·p1-pathcheck 트레이스의 `researcher_retrieve` span **340건**(161 + 161 + 18, 행의 `retrieval_calls` 합과 같다)을 같은 `search_text`·`k=4`로 무필터 재생했다. 문서 ID·순위·점수 **340/340 일치**했고 허용오차는 0이다. 양성 대조로 트레이스 사본에서 순위 맞바꿈 1건과 점수 +0.0001 1건을 넣었고, **둘 다 검출**됐다(종료 1).
- **Experiment (변경 전 재현):** 코드를 바꾸기 전에 현재 인덱스에서 같은 두 프로브를 돌렸다. 두 결과 모두 세션 14 결과와 케이스·요약 단위로 같았다. 즉 현재 인덱스는 v1.1 값을 그대로 재현한다.
- **Cost:** 0원. LLM 호출 0건, 네트워크 호출 0건, 새 의존성 0개. 임베딩은 로컬이다.

## Alternatives

### A — 도메인별 불리언 키로 펼쳐 재인덱싱하고 Chroma `where`로 거른다

- **Pros:** DB가 후보를 자르므로 문서 수가 커져도 받아오는 양이 생존자 수로 줄어든다. 운영 경로가 DB 질의 하나로 끝난다.
- **Cons:** 운영 인덱스를 다시 만들어야 한다(`--reset`). 같은 정보가 연결 문자열과 불리언 키 두 곳에 생겨 정합성을 따로 지켜야 하고, 어휘가 바뀌면 키가 바뀌어 재인덱싱이 필요하다. Chroma 1.5.5에서 `where`가 붙은 검색 경로의 완전성(ADR-005 A3의 유실)은 측정된 적이 없다.
- **Rejected because:** (1) 재인덱싱 없이 p1-on 기준을 유지할 수 있다. (2) `where` 경로의 완전성이 측정되지 않았다. (3) 이번 주 범위를 넘는다.
- **Recheck if:** 문서 수가 수천 단위로 늘 때, 또는 A3 유실을 해결할 때.

## Consequences

### Positive

- 검색 계층에 필터가 생겼다. T2·T3에서 노드 쪽 배선만 남는다.
- 인덱스·저장 형태를 바꾸지 않았다. p1-on 기준이 유효하다는 것을 실제 검색어 340건으로 직접 확인했다.
- 필터 생존자 수가 트레이스에 남아, "필터 뒤 후보 0"을 사후에 구별할 수 있다.

### Negative

- 필터 검색 1회는 전수(현재 37건)를 받아 Python에서 거른다. 문서 수에 비례해 비용이 늘어난다.
- 필터 값은 아직 **오라클**이다(골든셋 선언에서 유도). 질의에서 값을 고르는 플래너가 없으므로 위 수치는 필터 효과의 상한이다(ADR-023 Negative와 같다).
- 층 B "strict 0/9"는 검색 경로가 낸 결과가 아니다. 필터 값이 선언되지 않았다는 **정의**에서 나온 값이다. 검색 인자로는 표현할 수 없다.

### Risks

- 필터 경로는 HNSW 유실이 일어나면 예외로 멈춘다. Researcher에서는 `RetrievalError`가 그 항목의 "근거 없음"이 된다. 깨끗한 인덱스에서 유실은 0/120이었다(ADR-005 A4). 하지만 인덱스가 더러워지면 필터 검색이 실패로 드러난다(조용히 틀리는 것보다 낫다).
- 통제어휘는 manifest 합집합이다. 과거 export에만 있던 값도 허용된다.

## Implementation

- [x] `src/tools/retrieval.py` — `tech_domain`·`trace` 선택 인자, `_search_tech_domain`, `FilterValueError`, `tech_domain_vocab()`
- [x] `scripts/probe_retrieval.py` — `--filter-impl retrieval`, 결과에 `filter_impl`
- [x] `scripts/replay_retrieval_trace.py` — 트레이스 검색어 재생 대조
- [x] 테스트 `tests/test_retrieval_filter.py` 17건 — None 경로 인자 동일 · 항상 전수 · strict·순위 보존 · 어휘 밖 값 · 유실 예외 · 트레이스 생존자 수
- [x] 동일성 결과 — `docs/eval/probe-retrieval-v1.2-t1-*.json`, `docs/eval/t1-retrieval-replay-p1.json`
- [ ] 노드(Researcher)에서 필터 값 전달 + 플래너(에이전트의 `tech_domain` 선택) — **다음 세션 T2** (사용자 결정, session-21)

## Reversibility

- **Reversible:** Yes
- **Rollback:** `tech_domain`을 넘기지 않으면 기존 경로다. 코드 커밋을 되돌리면 끝이고, 인덱스·코퍼스 변경은 없다.
- **Migration Cost:** Low

## Review Trigger

- 문서 수가 수천 단위로 늘 때, 또는 ADR-005 A3 유실을 해결할 때 A(불리언 키 + `where`)를 다시 검토한다.

## References

- **Related ADR:** ADR-023(프로브 후처리 arm · 오라클), ADR-020(Chroma 부분 일치 부재), ADR-005 Amendment 3·4(HNSW 유실 · 인덱스 상태), ADR-025(점수 비노출), ADR-026(p1-on 기준), ADR-002
- **Documentation:** `docs/handoff/session-21.md`, `docs/eval/probe-retrieval-v1.2-t1-nofilter.json`, `docs/eval/probe-retrieval-v1.2-t1-tech-domain-strict-retrieval.json`, `docs/eval/t1-retrieval-replay-p1.json`
