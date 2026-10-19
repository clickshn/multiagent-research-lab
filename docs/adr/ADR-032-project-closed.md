# ADR-032: 프로젝트 종료 — 개발과 운영을 모두 마치고 레포를 닫는다

- **Status:** Accepted
- **승인 메모:** 개발 종료는 session-27(2026-10-16)의 사용자 결정이다(`docs/handoff/session-27.md` §4, `bda6c4c`). 운영 종료와 이 ADR 작성은 2026-10-19 사용자 지시다
- **Date:** 2026-10-19
- **Decision:** multiagent-research-lab(MARA)의 **상태는 closed다.** 개발과 운영을 모두 끝낸다. 새 기능·새 측정·새 하네스 변수는 착수하지 않는다.
  마지막 기준점은 태그 `v1.2.1`(`bda6c4c`)이다. 아래 "다시 열 조건" 중 하나가 성립하면 이 ADR에 Amendment를 붙이고 다시 연다.
- **Scope:** multiagent-research-lab (레포 전체 · 프로젝트 상태)
- **Decision Source:** Human

---

## Context

### Problem

이 프로젝트가 검증하려던 것은 **하네스(오케스트레이션 구조·검증 루프·컨텍스트 관리)가 결과 품질을 얼마나 좌우하는가**다(ADR-002, `docs/problem-statement.md` §4 측정 원칙).
표면 목표는 `docs/problem-statement.md` §3의 세 가지였다.

1. **리서치 시간 단축.** 질의를 넣으면 개요·근거·초안까지 자동으로 만든다.
2. **근거(출처) 항상 제시.** 근거를 못 찾으면 문장을 만들지 않고 "근거 없음"으로 남긴다.
3. **운영 가시성.** 단계별 입력·출력·지연·토큰을 계측한다.

v1.0(session-08) · v1.1(session-16) · v1.2(session-25) · v1.2.1(session-27)을 거쳤다. 사용자는 session-27에서 "MARA는 이 세션으로 종료한다"고 정했다.
남은 일 중 push·태그와 인덱스 백업 삭제는 사용자 몫으로 넘어갔다. 지금은 태그 `v1.2.1`이 `bda6c4c`에 있고, `var/chroma-backup-v1.2-*`는 없다.
이 ADR은 그 결정과 2026-10-19의 운영 종료 지시를 한곳에 모은다. 달성한 것, 끝낸 이유, 남은 문제, 다시 열 조건을 적는다.

### Constraints

- **사용자가 종료를 결정한 동기는 기록에 없다.** 아래 Rationale에는 기록으로 확인되는 사정만 적었다. 결정 동기를 추정해서 채우지 않았다.
- **이 ADR은 클라우드 리소스를 철거하지 않았다.** session-07에 ECS Fargate 스택을 배포했다(`fdab1bb`, 리소스 27개).
  그 뒤로 레포와 핸드오프에 철거 기록이 없다. 철거는 상태 변경 명령이라 사전 승인과 `.claude/deploy-approved`가 필요하다(`docs/governance.md` 승인 게이트).
- 수치는 전부 기존 README·ADR·핸드오프에 이미 적힌 값이다. 이 ADR을 쓰면서 새로 잰 것은 없다(LLM 호출 0건).
- 공통 측정 조건: 모델 `gemma-4-31B-it`(`VLLM_BASE`, fp `1897f0ecc081`), temperature 0, 캐시 끔, 코퍼스 37건, 골든셋 v2.0 30건(층 A 14 · B 9 · C 7), 조건당 1회다. **층은 합산하지 않는다**(ADR-022).

## Decision

### Selected

- **Technology:** 변경 없음. 의존성 추가·삭제 없음.
- **Architecture:** 마지막 상태를 그대로 둔다. Outliner → Researcher → Verifier ⇄ Researcher → Writer 고정 그래프(LangGraph)다.
  노드 안 동시 실행은 켜져 있다(상한 4). `tech_domain` 선택 도구는 기본 off이고 필터 정책은 strict다(ADR-026 · 029 · 030 · 031).
- **Implementation:**
  - 이 ADR을 추가한다. README 맨 위에 상태 한 줄(`상태: 종료 (2026-10-19)`)을 넣는다.
  - 코드·골든셋·코퍼스·인덱스는 바꾸지 않는다.
  - push하지 않는다. push는 사용자가 한다.

## Rationale

1. **사용자 결정이다.** session-27 §4는 "MARA는 이 세션으로 종료한다"고 적었다. 다음 버전 1순위 후보(Verifier 통과 항목 재판정 제거)도 "착수하지 않는다"고 적었다. 운영 종료는 2026-10-19 지시다.
2. **계획한 측정이 모두 끝났다.** v1.2와 v1.2.1에서 하네스 변수를 하나씩 바꿨다. 각 측정은 사전 등록한 판정 규칙으로 끝까지 판정했다(`ce86b04` · `5122e97` · `9454c23`).
   README §5.11에 남은 후보는 전부 **새 변수**다. 하려면 사전 등록과 별도 회차가 먼저 필요하다(ADR-002).
3. **서빙 자원에 기한이 있다.** KT Cloud 무상 할당은 2026년 말에 끝나고, 대체 경로는 없다(ADR-003 Review Trigger, README §7 "서빙 자원 만료").
   모델 고정 원칙(ADR-002) 때문에 모델이나 서빙이 바뀐 뒤의 측정은 지금까지의 비교선을 잇지 못한다. 지금 레포를 닫으면 모든 수치가 같은 모델·같은 엔드포인트 지문 위에 남는다.

## Evidence

- **Benchmark:** 현재 기본 경로 `p1-on`(필터 없음, 동시 실행 상한 4), n=30
  - 골든셋 pass: 층 A **12/14** · 층 B **5/9** · 층 C(음성, 근거 없음 유지) **7/7** — README §4.4 · §4.7, `docs/eval/bench-v1.2-p1-on.json`
  - 파이프라인 wall p50 **9.56s** · p95 **14.40s**(nearest-rank). 같은 세션의 순차 실행은 p50 11.89s · p95 20.83s — ADR-026, README §4.7
  - 요청당 청구 토큰 평균 **10,435**, 항목당 호출 **2.99**. 이 값은 순차 실행 S0c 기준이다(README §4.3). 동시 실행 호출 수는 297로 같고 토큰은 313,047 → 313,041이다(README §4.2)
  - 검색 재현율(top-4, 무필터, ko): 층 A **12/14** · 층 B **3/9**. en은 층 A 12/14 · 층 B 7/9 — README §0
  - 점수 간격: 최난도 **0.0098** · 평균 **0.0268**(negative n=7). 층 A 앵커로 재면 최난도 **−0.0052** — ADR-005 Amendment 2
- **Experiment:** 바꾼 하네스 변수와 판정. 판정 규칙은 전부 측정 전에 커밋했다
  - 재현성: 같은 조건 연속 2회의 pass/fail 뒤집힘 **0**(n=30). 무필터 검색어 재생 **340/340**(ID·순위·점수, 허용오차 0) — ADR-025 · 027 · 031
  - Researcher 후보 점수 제거(S0c): **무해 확인**. 뒤집힘 0(n=30), 토큰 −2.0% — ADR-025
  - 노드 안 동시 실행(P1): 입력 해시 297/297 동일, 인용·pass/fail 차이 0, wall p95 20.83 → 14.40s → **기본 켬** — ADR-026
  - 에이전트 `tech_domain` 선택 + strict(T3a): 층 B 5/9 → **0/9** → 해악(D4) — ADR-029
  - 선택 + null-통과(N1): 층 C 7/7 → **6/7**(GS-027) → 해악(D5) — ADR-030
  - 메타 37건 전부 재추출 + strict(v1.2.1): 층 B **7/9** 회복, 층 C 7/7 → **6/7**(GS-027 재현) → 해악(D5). 도구는 기본 off 유지 — ADR-031
  - 재검색 루프: 재검색이 가져온 새 근거는 v1.2 전 조건에서 **0건** — ADR-006 Amendment, README §5.1
  - 인덱스 유실: 중복 upsert가 방아쇠였다. 구 인덱스 **6.5%**(185 표본) → 재생성 후 **0%**(120 표본). 빌드 시점 방어를 넣었다 — ADR-005 Amendment 3~5
  - 프롬프트 인젝션 10건: 구조적 성립 **4 → 0**, 모델 의존 **1 → 0** — README §7, `docs/security/injection-results-session-05.md`
- **Production Data:**
  - ECS Fargate 온디맨드 배포 **27/27**, drift 0 — ADR-013, README §6.2, `fdab1bb`
  - 테스트 **346 passed**, 실패 0. session-27의 README 커밋 전에 1회 실행했다 — `docs/handoff/session-27.md`
  - 결정 기록은 이 ADR 전까지 ADR **31건**과 Amendment **24건**이다 — README §8.1
- **Cost:**
  - LLM: 무료 할당 자원이라 **0원**이다(ADR-003). v1.2.1 본측정은 논리 호출 410건이었다(ADR-031).
  - 클라우드 유휴: 약 **$0.14/월**(README §6.2, ADR-013 Amendment §3). 철거하지 않으면 계속 든다(Risks).
  - 외부 LLM 벤더: Session 0.5a에서 전제 위반이 1회 있었다(관련성 게이트 49건 + 구조화 추출 30건). 그 뒤로는 0건이다 — ADR-021

## Alternatives

### 개발을 계속한다 (README §5.11의 다음 버전 후보에 착수)

- **Pros:** 남은 1순위 후보(Verifier 통과 항목 재판정 제거)는 불필요한 호출을 줄인다. covered → uncovered 뒤집힘의 구조적 가능성도 없앤다(README §5.11).
- **Cons:** 하네스 변경은 전부 새 변수다. 사전 등록과 새 측정 회차가 필요하다(ADR-002). 서빙 할당이 끝나기 전에 비교선을 이을 시간도 짧다(ADR-003).
- **Rejected because:** 사용자가 session-27에서 "MARA가 이 세션으로 종료되므로 착수하지 않는다"고 정했다(`docs/handoff/session-27.md` §4).
- **Recheck if:** 아래 Review Trigger의 "다시 열 조건" 중 하나가 성립할 때

## Consequences

### Positive

- 모든 수치가 같은 모델(`gemma-4-31B-it`)과 같은 엔드포인트 지문(`1897f0ecc081`) 위에 고정된 채로 남는다. 서빙 만료 뒤에도 기록은 해석할 수 있다.
- 마지막 상태가 태그(`v1.2.1`)와 이 ADR로 한곳에 정리된다.

### Negative

- **목표 대비 미달이 그대로 남는다.**
  - `docs/problem-statement.md` §4의 성공 기준은 목표값이 끝내 확정되지 않았다(`TBD`). 골든셋 규모는 "최소 50건 목표"였고 30건에서 멈췄다.
  - 목표 1(리서치 시간 단축)의 "사람 검증 시간"은 한 번도 재지 않았다.
  - 목표 2(근거 항상 제시)는 기계 채점만 했다. `must_mention` 의미 판정은 사람이 보지 않았다(ADR-006).
- **알려진 문제 — 고치지 않고 닫는다** (README §5, `docs/handoff/session-27.md`)
  - 재검색 루프에 실효가 없다(새 근거 0건). 질의 재작성은 붙이지 않았다(ADR-006).
  - 임베딩 변별력이 부족하다(앵커 최난도 간격 −0.0052). 임베딩 교체와 진단 검증 E1은 하지 않았다(ADR-005 · 012).
  - 층 B 검색 재현율은 3/9(ko)다(README §0, §5.2).
  - GS-027: negative 채점이 Outliner의 하위 항목 분해와 부딪힌다. GS-012: `filter_axis`가 새 라벨과 어긋난다. 둘 다 사후에 고치지 않는다(ADR-022).
  - 라벨 감사는 Claude의 독립 감사였다. 사람 감사는 하지 않았다. 출처 표의 `release_type`은 감사 14건 중 8/14만 맞았다(ADR-031).
  - 인덱스 유실의 재생 메커니즘은 밝히지 못했다. 운영 검색 경로에 2차 방어를 둘지도 정하지 않았다(README §5.10).
  - 체크포인터 백엔드를 고르지 않았다. 그래서 중단·재개와 Outliner 승인 HITL은 하지 않았다(ADR-002).
  - 공급망: Python 의존성 해시를 고정하지 않았다. 엔드포인트의 모델 지문을 확인할 수단도 없다(README §5.10).
  - `src/orchestrator/citations.py` 독스트링의 층 B `미확인` 서술은 v1.2 기준 그대로다. 코드 동작은 맞다(session-27 §5).

### Risks

- **클라우드 리소스와 데이터가 남아 있을 수 있다.** 철거 기록이 없다(Constraints).
  - EFS는 자동 만료가 없어 아무도 지우지 않으면 영구히 남는다(`docs/governance.md` "로컬 산출물 취급" (나)).
  - CloudWatch Logs에는 질의 원문과 초안이 14일간 남는다.
  - SSM Parameter Store에는 무인증 엔드포인트 URL이 있다(README §5.10).
  - 철거 경로의 배포자 권한(`destroy`)은 검증하지 않았다. 관리형 정책 1개(`AWSBudgetsActionsWithAWSResourceControlAccess`)도 루트 콘솔에서 떼야 한다(ADR-017).
- **로컬 산출물도 남아 있다.** `var/traces/`와 `var/llm_cache/`에는 프롬프트와 응답 본문이 평문으로 있다. 삭제 책임자는 정해지지 않았다(README §5.10). 지금 데이터는 공개 자료뿐이라 실질 위험은 낮다(ADR-004).
- 무인증 엔드포인트를 사내 보안 정책이 허용하는지는 끝내 확인하지 못했다(README §5.10).
- 2026년 말 할당이 끝나면 레포를 그대로 받아도 파이프라인이 돌지 않는다. `.env`의 `VLLM_BASE`를 바꿔야 한다(ADR-003).

## Implementation

- [x] 이 ADR 작성 (`docs/adr/ADR-032-project-closed.md`)
- [x] README 맨 위에 상태 한 줄 추가
- [ ] README §0의 ADR 건수와 §8.1 ADR 목록에 ADR-032 반영 — 이번 작업 범위 밖이라 하지 않았다
- [ ] 클라우드 스택 철거 — 사전 승인이 필요하다. `infra/terraform/README.md`의 삭제 절차를 따른다
- [ ] 로컬 `var/traces/` · `var/llm_cache/` 정리 — 사용자 판단
- [ ] push — 사용자가 한다

## Reversibility

- **Reversible:** Partial
- **Rollback:** 이 ADR에 Amendment를 붙여 상태를 되돌리면 개발을 다시 열 수 있다. 코드와 기록은 그대로 레포에 있다.
  다만 측정을 이어가려면 같은 모델·같은 엔드포인트가 필요하다. 그것은 2026년 말 할당 만료라는 외부 조건에 달려 있다(ADR-003).
  만료 뒤에 다시 열면 새 엔드포인트에서 기준선(p1-on)부터 다시 재야 한다.
- **Migration Cost:** Medium

## Review Trigger

다시 열 조건이다. 기존 ADR의 Review Trigger 가운데 종료 뒤에도 유효한 것을 모았다. 하나라도 성립하면 이 ADR에 Amendment를 붙이고 다시 연다.
다시 열어도 하네스 변경은 사전 등록부터 한다(ADR-002).

- **대체 서빙 경로가 정해질 때.** 자체 GPU, 다른 할당, 유료 전환 중 하나다. 새 엔드포인트에서 p1-on 기준선부터 다시 잰다 — ADR-003
- **접근 가능한 엔드포인트가 2개 이상이 될 때.** 단계별 모델 라우팅 실험을 검토한다 — ADR-003
- **사내 문서나 고객 데이터를 넣으려 할 때.** 그 전에 보존 기간·접근 주체·삭제 책임자를 정해야 한다 — ADR-004, `docs/governance.md`
- **선택 도구를 기본 on으로 바꾸자는 제안이 나올 때**(ADR-009와 함께 본다). 또는 골든셋 negative 채점이나 근접 negative 설계를 바꾸거나, 사람 라벨 감사가 이뤄지거나, 라벨러가 바뀔 때 — ADR-031
- **엔드포인트가 임베딩 서빙을 추가할 때** — ADR-005
- **코퍼스 문서 수가 수천 단위가 될 때.** 필터 배선 A안을 재검토한다 — ADR-027

## References

- **Related ADR:** ADR-002(모델 고정 원칙) · ADR-003(서빙과 만료) · ADR-004(데이터 범위) · ADR-005 · ADR-006 · ADR-013 · ADR-017 · ADR-021 · ADR-022 · ADR-025 · ADR-026 · ADR-027 · ADR-029 · ADR-030 · ADR-031
- **Documentation:**
  - 목표와 성공 기준: `docs/problem-statement.md` §3 · §4
  - 종료 결정: `docs/handoff/session-27.md` §4 · `bda6c4c`(태그 `v1.2.1` 위치)
  - 수치: `README.md` §0 · §0.2 · §4 · §5 · §6.2 · §7 · §8.1
  - 사전 등록: `ce86b04`(T2/T3) · `5122e97`(N1) · `9454c23`(v1.2.1)
  - 배포: `fdab1bb`. 기본 동시 실행 켬: `668b207`
