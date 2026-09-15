# ADR-007: 계측은 Tracer 파사드 뒤에 두고, 로컬 JSONL을 기본으로 Langfuse를 옵션으로 얹는다

- **Status:** Proposed
- **Date:** 2026-09-15
- **Decision:** 노드는 `src/obs`의 `Tracer` 파사드만 호출한다. 로컬 JSONL 기록은 항상 켜져 있고, Langfuse는 `.env`에 self-host 주소와 키가 모두 있을 때만 추가로 전송한다.
- **Scope:** multiagent-research-lab (계측 / 관측)
- **Decision Source:** Human

---

## Context

### Problem

ADR-002의 Implementation 체크리스트에 "Langfuse 계측 연동으로 노드별 지연·토큰
사용량 기록"이 남아 있고, session-03에서 sub-agent 구현과 함께 붙이기로 했다.
문제는 Langfuse 서버가 아직 없다는 것이다.

여기서 두 요구가 충돌한다.

- **감사 로그는 조건부가 아니다.** 어떤 질의에 어떤 프롬프트로 어떤 모델이 무엇을
  답했는지는 사후 재현 가능한 형태로 남아야 한다 (`docs/problem-statement.md` §2).
  규제성 요구라 나중에 붙이기 어렵다.
- **관측 데이터를 외부 SaaS로 내보내지 않는다.** 같은 문서가 "로그 자체도 우리가
  통제하는 인프라(self-host)에 저장하며, 외부 SaaS 관측 도구로 내보내지 않는다"고
  못박는다. Langfuse Cloud를 쓰는 것은 이 제약에 걸린다.

Langfuse self-host를 세우는 것은 새 리소스 기동이라 이번 세션의 범위(동작 확인)를
넘고, 세션 6~7의 컨테이너화·배포와 함께 다루는 것이 자연스럽다. 그렇다고 그때까지
계측을 비워두면 감사 로그 요구가 미충족인 채로 남는다.

### Constraints

- Langfuse SDK의 API가 메이저 버전마다 바뀐다. v2의 `langfuse.trace()`는 v4에서
  `start_observation()`으로 바뀌었다 (실측, langfuse 4.15.2).
- 계측 실패가 리서치 실행을 실패시키면 안 된다 — 관측은 본 작업의 부수 효과다.
- 토큰·지연은 이미 프로바이더 계층이 응답(`LLMResponse`)에 실어 나른다 (session-02).
  노드가 따로 세지 않아야 한다.
- 노드는 State를 받아 State를 반환하는 순수 함수 형태를 유지해야 한다
  (`.claude/rules/orchestrator.md`).

## Decision

### Selected

- **Technology:** 자체 `Tracer` 파사드 (`src/obs/`) + 로컬 JSONL 백엔드 + Langfuse 4.15.2 백엔드(옵션)
- **Architecture:** **실행 1건 = trace 1개, 모델 호출 1건 = span 1개.** 노드 팩토리가 `trace`를 주입받고, 모델 호출은 전부 `_call()` 한 곳을 지난다 — 호출 경로를 하나로 두면 "모든 호출이 기록된다"가 구조적으로 보장된다. 백엔드는 `CompositeTracer`로 합성해 로컬 JSONL은 항상, Langfuse는 구성됐을 때만 받는다.
- **Implementation:** 기록 내용은 노드 이름, 프롬프트 입력·출력, 입력/출력 토큰, 지연, `finish_reason`, 잘림 여부, 프롬프트 버전이다. 검색 호출도 별도 span(`researcher_retrieve`)으로 남긴다. Langfuse 백엔드의 모든 호출은 예외를 삼켜, 계측 실패가 실행을 죽이지 않는다. 로컬 기록은 `var/traces/<run_id>.jsonl`이며 커밋하지 않는다. State의 `trace` 필드(`LLMCallRecord` 누적)는 그대로 유지해, 계측 백엔드 없이 그래프 실행 결과만으로도 추적이 가능하다.

## Rationale

1. **감사 로그가 인프라 구성 여부에 좌우되면 안 된다.** Langfuse 서버가 없다고 계측이
   꺼지면 규제성 요구가 "나중에" 미뤄진다. 로컬 JSONL을 기본으로 두면 추가 인프라
   없이 오늘부터 기록이 남는다.
2. **파사드가 SDK 버전 변화를 흡수한다.** Langfuse SDK는 메이저 버전마다 API가 바뀐다.
   노드가 SDK 시그니처에 직접 묶이면 SDK 업그레이드가 노드 수정이 된다. 프로바이더
   계층에서 이미 쓰고 있는 원칙을 계측에도 같게 적용한다.
3. **외부 전송은 명시적으로만 켜진다.** Langfuse 호스트를 기본값으로 두지 않으므로,
   `.env`에 self-host 주소를 넣기 전에는 관측 데이터가 어디로도 나가지 않는다.
   제약을 기본값으로 지킨다.
4. **실행 1건 = trace 1개여야 단계 비교가 된다.** 노드마다 별도 trace를 만들면 한 질의의
   여러 호출이 무관한 기록으로 흩어져, "어느 단계에서 품질이 떨어졌는가"를 답하기
   어려워진다 (`docs/problem-statement.md` §3 목표 3).
5. **JSONL은 한 줄이 한 이벤트다.** 실행 중에도 tail로 볼 수 있고 파싱에 라이브러리가
   필요 없다. Langfuse 서버가 생기면 그대로 읽어 재적재할 수 있다.
6. **토큰은 프로바이더가 준 값을 그대로 쓴다.** 노드가 따로 토큰을 세면 계산 기준이
   갈리고, 엔드포인트가 보고하는 값과 어긋난다.

## Evidence

- **Experiment:** session-03 e2e 실행 (run_id `20260915-152557-c34630`, 코퍼스 16건) — 로컬 JSONL 22개 이벤트 기록

  | 이벤트 | 개수 |
  | --- | --- |
  | `trace` / `trace_end` | 1 / 1 |
  | `generation` (모델 호출) | 14 |
  | `span` (검색 호출) | 6 |

  단계별 집계 (JSONL과 State `trace` 양쪽에서 동일하게 재구성됨):

  | 노드 | 호출 | 입력 토큰 | 출력 토큰 | 지연 합(s) |
  | --- | ---: | ---: | ---: | ---: |
  | outliner | 1 | 286 | 81 | 2.28 |
  | researcher | 6 | 7,184 | 355 | 6.68 |
  | verifier | 6 | 3,336 | 482 | 8.62 |
  | writer | 1 | 911 | 663 | 11.69 |
  | **합계** | **14** | **11,717** | **1,581** | **29.26** |

  → Researcher가 입력 토큰의 61%를 쓴다(검색 후보 문서를 프롬프트에 싣기 때문). 다음 세션의
  비용·레이턴시 실측에서 먼저 볼 지점이다.

  또한 **벽시계 55.60s 중 LLM 지연은 29.26s뿐이다.** 나머지는 대부분 임베딩 모델 첫
  로딩이다. 계측이 이 둘을 나눠 기록하지 않았다면 "모델이 느리다"고 잘못 읽혔을 값이다.

## Alternatives

### Langfuse SDK를 노드에서 직접 호출

- **Pros:** 중간 계층이 없어 코드가 짧고, SDK 기능(세션, 사용자, 스코어)을 그대로 쓸 수 있다.
- **Cons:** Langfuse 서버가 없으면 계측이 통째로 비고, SDK 메이저 버전 변경이 노드 수정으로 번진다.
- **Rejected because:** 감사 로그가 인프라 구성 여부에 좌우되고, 이 프로젝트의 "하네스를 계층으로 분리한다"는 원칙과 어긋난다.

### Langfuse Cloud 사용

- **Pros:** 서버를 세울 필요가 없어 즉시 붙는다. UI·보존·백업을 신경 쓰지 않아도 된다.
- **Cons:** 프롬프트·응답 전문이 외부 SaaS로 전송된다.
- **Rejected because:** "로그도 self-host에 저장하며 외부 SaaS 관측 도구로 내보내지 않는다"는 제약에 정면으로 걸린다 (`docs/problem-statement.md` §2).

### State의 `trace` 필드(LLMCallRecord)만 쓰고 별도 계측을 두지 않음

- **Pros:** 추가 의존성이 없다. 이미 구현돼 있다.
- **Cons:** 실행이 끝나야 결과를 볼 수 있고, 프롬프트 입력·출력 전문이 남지 않는다. 검색 호출도 기록되지 않는다.
- **Rejected because:** "어떤 프롬프트로 무엇을 답했는지 재현 가능해야 한다"는 요구를 충족하지 못한다. 다만 유용하므로 제거하지 않고 병행한다.

## Consequences

### Positive

- Langfuse 서버 없이도 감사 로그 요구가 오늘부터 충족된다.
- Langfuse를 붙일 때 노드 코드를 고치지 않는다 — `.env`에 값 세 개를 넣으면 된다.
- 계측 실패가 리서치 실행을 죽이지 않는다.
- 단계별 토큰·지연이 한 트리에서 비교돼, 비용 분포가 바로 보인다.

### Negative

- 백엔드를 우리가 합성하므로 Langfuse의 고급 기능(스코어, 세션 묶기, 프롬프트 관리)을 쓰려면 파사드를 넓혀야 한다. 파사드가 SDK 기능의 부분집합만 노출한다.
- 로컬 JSONL에는 보존 기간·접근 권한 개념이 없다. 감사 로그 보존 요구사항이 확정되면 별도 처리가 필요하다.
- `langfuse` 패키지가 설치돼 있지만 실제로는 대부분 쓰이지 않는 상태다 — 서버가 구성되기 전까지 의존성만 있고 동작하지 않는 코드 경로가 존재한다.

### Risks

- Langfuse 백엔드 경로는 **실제 서버로 검증되지 않았다.** 서버가 없어 `LangfuseTracer`가 한 번도 실행되지 않았다. 세션 6~7에서 서버를 세울 때 이 경로에서 문제가 나올 수 있다.
- langfuse 4.15.2가 내부적으로 `pydantic.v1`을 임포트해 Python 3.14에서 경고를 낸다. 현재는 동작에 문제가 없지만(실측), Python 3.14 + Pydantic V1 비호환이 실제 오류가 되는 시점에 langfuse가 먼저 깨질 가능성이 있다.

## Implementation

- [x] `Tracer` / `RunTrace` / `Span` 프로토콜 (`src/obs/tracer.py`)
- [x] 로컬 JSONL 백엔드 — 실행 1건 = 파일 1개
- [x] Langfuse 백엔드 — `.env` 구성 시에만 활성화
- [x] `CompositeTracer` 합성 + `DISABLE_TRACING` 환경변수
- [x] 노드 모델 호출 경로 단일화 (`_call()`) + 검색 span
- [x] 실행 스크립트의 단계별 요약 출력 (`scripts/run_research.py`)
- [ ] Langfuse self-host 기동 및 실제 전송 검증 (세션 6~7)
- [ ] 감사 로그 보존 기간·접근 권한 반영

## Reversibility

- **Reversible:** Yes
- **Rollback:** 백엔드 교체는 `Tracer` 프로토콜을 구현한 클래스를 하나 더 쓰고 `get_tracer()`만 고치면 된다. 계측을 통째로 끄는 것은 `DISABLE_TRACING=1`이다. 노드 코드는 어느 쪽에도 영향받지 않는다.
- **Migration Cost:** Low

## Review Trigger

- 골든셋으로 측정한 결과 모델 판정 단계가 근거 정확도를 유의미하게 올리지 못하는 경우는 ADR-006 소관이다. 본 ADR의 재검토 조건은 Langfuse self-host 기동 시점 — 파사드가 실제 서버 연동에서 충분한지 확인한다.

## References

- **Related ADR:** ADR-002 (Langfuse 계측 연동이 Implementation에 남아 있던 항목), ADR-003 (토큰·지연을 실어 나르는 프로바이더 계층), ADR-006 (계측 대상이 되는 검증 루프)
- **Documentation:** `docs/problem-statement.md` §2·§3 목표 3, `src/obs/tracer.py`, `src/orchestrator/nodes.py`, `scripts/run_research.py`
