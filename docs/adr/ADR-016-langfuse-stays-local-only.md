# ADR-016: Langfuse는 클라우드에 올리지 않는다 (로컬 전용 관측 도구로 유지)

- **Status:** Proposed
- **Date:** 2026-09-16
- **Decision:** 세션 6에서 self-host로 검증한 Langfuse 스택을 **클라우드 배포 범위에서 제외**한다. 배포되는 것은 오케스트레이터 태스크뿐이고, 클라우드 실행의 계측은 **로컬 JSONL(EFS) + CloudWatch Logs**로 한다. Langfuse는 로컬 개발·분석 도구로만 쓴다.
- **Scope:** multiagent-research-lab (배포 범위 / 관측 / 세션 7)
- **Decision Source:** Human

---

## Context

### Problem

ADR-007이 계측을 **파사드**로 설계했다 — 로컬 JSONL이 기본이고 Langfuse는 선택적 백엔드다.
세션 6이 그 미검증 경로를 해소했다: v3 스택(web/worker + PostgreSQL + ClickHouse + Redis +
MinIO, **컨테이너 6개**)을 띄우고 `research_run` trace 1건 / observation 19개 수신을 실측했다.

그래서 세션 7의 배포 범위에 질문이 생겼다 — **Langfuse도 함께 올릴 것인가.**
세션 6 핸드오프가 이것을 "apply 전에 결정할 것" 세 가지 중 하나로 남겼다.

### Constraints

- **유휴 고정비가 0에 수렴해야 한다** (ADR-013). 이 프로젝트에서 비용을 지배하는 것은
  실행량이 아니라 **켜 두는 것** 자체다 — EKS를 기각한 근거가 그것이었다.
- **Langfuse는 데몬 6개다.** 오케스트레이터와 성격이 정반대다. 오케스트레이터는 작업
  컨테이너라 `RunTask` 한 번으로 끝나지만(ADR-010), Langfuse는 데이터를 받으려면 **상시로
  살아 있어야 한다.**
- **compose의 Langfuse 자격증명은 전부 로컬 개발용 기본값이다** — `pk-lf-local-dev`,
  0으로 채워진 `ENCRYPTION_KEY` 등. 평문으로 적혀 있고 의도된 것이다.
  세션 6 핸드오프가 "이 값이 배포로 넘어가면 사고다"라고 명시했다.
- **Langfuse에는 프롬프트와 응답 본문이 그대로 들어간다.** `var/traces`·`var/llm_cache`와
  같은 등급의 저장소다 (governance "로컬 산출물 취급").
- 이번 배포의 목적은 **오케스트레이터가 클라우드에서 도는 것을 보이는 것**이다.

## Decision

### Selected

- **Technology:** 배포 범위에서 Langfuse 제외. 클라우드 계측은 **EFS의 `var/traces` JSONL**
  (ADR-007 기본 경로, ADR-015로 영속화) + **CloudWatch Logs**(태스크 stdout/stderr).
- **Architecture:** 태스크 정의의 `LANGFUSE_HOST`를 **빈 값으로 고정**한다. ADR-007의 파사드는
  세 값이 비어 있으면 로컬 JSONL만 기록하므로, **코드 분기 없이** 전송이 꺼진다.
- **Implementation:** `infra/terraform/ecs.tf`의 `environment`에
  `{ name = "LANGFUSE_HOST", value = "" }`. docker-compose의 `--profile langfuse`는 그대로 둔다 —
  로컬에서는 계속 쓴다.

## Rationale

1. **상시 실행 6개는 이 프로젝트의 비용 원칙을 정면으로 깬다.** ADR-013이 EKS를 기각한
   이유가 "유휴 고정비가 실행 비용의 40배"였다. Langfuse는 그보다 나쁘다 — 아래 Evidence 참조.
2. **배포 목적에 필요하지 않다.** 오케스트레이터가 클라우드에서 돈다는 것을 보이는 데
   필요한 것은 실행 결과와 로그이고, 둘 다 CloudWatch와 EFS에 남는다. Langfuse가 주는 것은
   **분석 UI**이지 실행 능력이 아니다.
3. **파사드 덕분에 제외에 비용이 들지 않는다.** ADR-007이 Langfuse를 선택적 백엔드로
   설계해 둔 결과, 끄는 데 필요한 것은 환경변수 하나다. **코드가 갈라지지 않는다** —
   같은 이미지가 로컬에서는 Langfuse로 보내고 클라우드에서는 JSONL만 쓴다.
4. **올리면 자격증명을 전부 새로 발급해야 한다.** compose의 값은 로컬 전용이고, 그대로
   넘기면 사고다. 즉 "그냥 같이 올린다"가 성립하지 않고 **별도의 시크릿 관리 작업**이
   따라붙는다 — 배포 목적에 기여하지 않는 일이다.
5. **관측 데이터의 노출면을 늘리지 않는다.** Langfuse에는 프롬프트·응답 본문이 쌓인다.
   클라우드에 올리면 웹 UI가 붙은 상시 서비스가 그 데이터를 들고 있게 된다.
   무인증 엔드포인트 문제(세션 3부터 미해결)가 정리되지 않은 상태에서 노출 지점을 하나 더
   만들 이유가 없다.

## Evidence

- **Cost:** Langfuse v3를 Fargate에서 상시로 돌릴 경우의 하한 추정 (ap-northeast-2, list price).
  컨테이너 6개를 태스크 1개에 묶어 **4 vCPU / 8GB**로 잡아도 (ClickHouse+PostgreSQL이 같이
  뜨므로 이보다 작기 어렵다):

  | 항목 | 계산 | 월 |
  | --- | --- | ---: |
  | Fargate 컴퓨트 | (4 × $0.04656 + 8 × $0.00511) × 730h | **$165** |
  | 퍼블릭 IPv4 | $0.005 × 730h | $3.65 |
  | 영속 스토리지(EFS/RDS 등) | 최소 | $1~ |
  | **합계** | | **~$170/월** |

  비교 기준: 오케스트레이터 **유휴 ~$0.14/월**(ECR 실측 862MB 반영), 실행 1회 ~$0.0034
  (헬스체크 103s 실측), 월 100회 실행해도 ~$0.5. **Langfuse를 올리면 고정비가 실행
  비용의 300배를 넘는다.**
  ADR-013이 기각한 EKS($73/월)보다도 두 배 이상이다.
- **Experiment:** 세션 6 로컬 검증 — 서버 `{"status":"OK","version":"3.225.7"}`,
  `research_run` trace 1건 / observation 19개(GENERATION 13 + SPAN 6) 수신,
  `usage={input:1131, output:247, total:1378}`가 터미널 요약(13콜 / 12875+881 토큰)과 일치.
  **전송 경로가 검증됐다는 사실 자체는 이 결정으로 사라지지 않는다** — 로컬에서 계속 유효하다.

## Alternatives

### Langfuse도 함께 클라우드에 올린다

- **Pros:** 클라우드 실행의 트레이스를 UI로 본다. 로컬 JSONL을 손으로 읽지 않아도 된다.
  포트폴리오에서 "관측 스택까지 운영했다"고 말할 수 있다.
- **Cons:** 고정비 ~$170/월 (위 Evidence). 자격증명 전부 재발급 필요. 프롬프트·응답을
  담은 상시 서비스가 하나 더 생긴다. 운영 대상이 1개에서 7개로 늘어난다.
- **Rejected because:** **"유휴 시 비용 0원" 원칙과 양립하지 않고, 이번 배포 목적에
  기여하지 않는다.** 배포 목적은 오케스트레이터를 클라우드에서 돌리는 것이고, 그 검증에
  필요한 계측은 EFS JSONL + CloudWatch로 충분하다. Langfuse가 주는 것은 분석 편의이며
  그 편의는 로컬에서 이미 얻고 있다.

### Langfuse Cloud(SaaS)를 쓴다

- **Pros:** 우리가 운영하지 않는다. 고정비가 무료 티어 안에서 0일 수 있다.
- **Cons:** 프롬프트·응답 본문이 **타사 서비스로 나간다.**
- **Rejected because:** 이 프로젝트의 실제 제약이 **외부 LLM 벤더 비의존 — 데이터가 타사
  모델·서비스로 나가지 않는 것**이다 (ADR-007이 self-host 주소만 쓰도록 정한 이유).
  비용이 아니라 데이터가 향하는 곳이 기준이므로 후보가 되지 않는다.

## Consequences

### Positive

- 유휴 고정비가 ~$0.14/월로 유지된다. ADR-013의 전제가 배포 후에도 성립한다.
- 배포 표면이 작다 — 상시 실행 서비스가 **0개**다. 끌 것을 잊어서 과금되는 경로가 없다.
- 로컬 개발용 자격증명이 클라우드로 넘어갈 위험이 구조적으로 없다.
- 클라우드에서 돌린 트레이스도 EFS에 남으므로, **나중에 로컬 Langfuse로 불러와 분석할 수 있다.**

### Negative

- 클라우드 실행 결과를 UI로 바로 볼 수 없다. CloudWatch 로그와 EFS의 JSONL을 봐야 한다.
- EFS의 JSONL을 로컬로 가져오는 절차가 아직 없다 — 지금은 태스크를 띄워서 읽거나
  EFS를 마운트해야 한다. **불편이 실제로 문제가 되는지는 써 봐야 안다.**
- 포트폴리오에서 "관측 백엔드를 클라우드에서 운영했다"는 항목은 없다. 다만 세션 6의
  self-host 검증 기록은 남는다.

### Risks

- ⚠️ **로컬과 클라우드의 계측 경로가 갈라진다.** 로컬은 JSONL + Langfuse, 클라우드는
  JSONL만이다. 파사드 덕분에 **기록되는 JSONL 자체는 같지만**, "로컬에서 보던 화면을
  클라우드에서도 본다"는 가정은 성립하지 않는다.
- ⚠️ Langfuse SDK의 `Context error: No active span in current context` 경고는 **여전히
  원인 미확인이다** (세션 6). 이 결정은 그 문제를 해결하지 않고 **클라우드 경로에서
  마주치지 않게 할 뿐이다.**

## Implementation

- [x] `infra/terraform/ecs.tf` — 태스크 정의 `environment`에 `LANGFUSE_HOST = ""`
- [x] docker-compose의 `--profile langfuse` 유지 (로컬 전용)
- [ ] EFS의 `var/traces`를 로컬로 가져와 Langfuse에 적재하는 절차 — 필요해지면 만든다

## Reversibility

- **Reversible:** Yes
- **Rollback:** 올리기로 바뀌면 Langfuse 스택용 Terraform(서비스 + 영속 스토리지 + 시크릿)을
  추가하고 태스크 정의의 `LANGFUSE_*` 세 값을 채운다. **코드 변경은 없다** — 파사드가
  환경변수만 본다. 다만 자격증명은 전부 새로 발급해야 한다.
- **Migration Cost:** Medium (스택 추가와 시크릿 관리가 필요하지만 애플리케이션은 안 바뀐다)

## Review Trigger

- **여러 사람이 클라우드 실행 결과를 봐야 하는 경우** — JSONL을 각자 내려받는 것이
  성립하지 않게 되면 공유 UI의 값이 생긴다. 그때 비용($170/월)과 견준다.
- **"유휴 시 비용 0원" 원칙 자체가 바뀌는 경우** — 이 결정의 1순위 근거가 그 원칙이다.

## References

- **Related ADR:** ADR-007 (계측 파사드 — 이 결정이 가능한 이유), ADR-010 (컨테이너 스택 /
  Langfuse self-host 검증), ADR-013 (배포 대상 / 유휴 고정비 원칙), ADR-015 (EFS — 대체 계측 경로)
- **Documentation:** `infra/terraform/ecs.tf`, `docker-compose.yml` (`langfuse` 프로파일),
  `docs/handoff/session-06.md` §3
