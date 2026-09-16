# ADR-014: IaC 도구는 Terraform으로 고정한다 (CDK 기각)

- **Status:** Proposed
- **Date:** 2026-09-16
- **Decision:** 이 프로젝트의 모든 IaC 작업은 Terraform(HCL)으로 작성한다. AWS CDK는 쓰지 않는다. 판단 기준은 **이직 시장에서의 스킬 수요 폭**이다.
- **Scope:** multiagent-research-lab (IaC 도구 선택)
- **Decision Source:** Human

---

## Context

### Problem

session-06에서 배포 대상을 ECS Fargate로 정하고(ADR-013) `infra/terraform/`에 최소 구성을
작성했다. **그런데 "왜 Terraform인가"는 기록되지 않았다** — ADR-013은 *어디에 배포할지*를
다뤘고 *무엇으로 배포를 기술할지*는 다루지 않았다. 결과적으로 도구 선택이 근거 없이
코드에만 남아 있었다.

이 프로젝트는 포트폴리오이기도 하다. 그래서 도구 선택의 기준에 **그 스킬이 채용 시장에서
얼마나 폭넓게 요구되는가**가 포함된다 — 이것은 기술적 우열과 별개의 축이다.

CDK는 이 프로젝트가 Python이라는 점에서 실질적인 후보였다. CDK는 Python을 지원하므로
애플리케이션과 인프라가 같은 언어를 쓰게 되고, HCL이라는 두 번째 언어를 배우지 않아도 된다.
**그럼에도 기각한다.**

### Constraints

- 포트폴리오 가치 판단은 사람이 한다. 측정할 수 있는 종류의 기준이 아니다.
- 이미 `infra/terraform/`에 Terraform 코드가 있다 — 이 결정은 그것을 사후 승인하는 동시에
  **이후 작업에 대한 구속**이다.
- 아직 `terraform apply`를 한 적이 없다 (ADR-013). 지금 도구를 바꾼다면 비용이 가장 싼
  시점이며, 반대로 지금 고정해 두면 이후 흔들릴 일이 없다.

## Decision

### Selected

- **Technology:** Terraform `~> 1.9` + `hashicorp/aws ~> 5.60` (HCL). AWS CDK 미사용.
- **Architecture:** IaC 코드는 `infra/terraform/` 한 곳에 둔다. 다른 디렉터리에 다른 도구의
  IaC를 두지 않는다 — 두 도구가 공존하면 어느 쪽이 진실인지가 모호해지고, 포트폴리오에서도
  "정하지 못했다"로 읽힌다.
- **Implementation:** 이미 작성된 `infra/terraform/`(ECR + CloudWatch 로그 그룹, `apply.sh`
  승인 게이트)가 그대로 유효하다. **재작성할 것이 없다** — 세션 6에서 CDK로 쓴 것이 없음을
  확인했다(레포 전수 검색: `aws-cdk` / `cdk.json` / `CdkStack` 등 흔적 0건).

## Rationale

1. **채용 시장 수요의 폭이 기준이다.** Terraform은 클라우드 벤더와 무관하게 통용되고,
   CDK는 AWS 사용 조직에서만 의미가 있다. 포트폴리오로서 도달 범위가 다르다.
   **이것은 기술적 우열 주장이 아니라 노출 가치 판단이며, 사용자가 명시적으로 내린 결정이다.**
2. **선언적 HCL이 이 프로젝트의 IaC 규모와 맞는다.** `.claude/rules/iac.md`가 "리소스는
   최소 단위로 추가"를 요구하고 실제 리소스도 2개다. 이 규모에서 CDK의 추상화(construct,
   프로그래밍 언어의 조건·반복)가 값을 할 여지가 적고, 오히려 `plan` 출력과 코드 사이에
   합성(synth) 단계가 하나 끼어든다.
3. **승인 게이트가 이미 Terraform 전제로 구현돼 있다.** `apply.sh`는 `plan`/`validate` 같은
   읽기 동작을 통과시키고 `apply`/`destroy`만 `.claude/deploy-approved`로 막는다
   (`docs/governance.md` "승인 게이트"). CDK로 가면 `cdk diff`/`cdk deploy`에 맞춰 게이트를
   다시 설계해야 한다 — **검증된 안전장치를 다시 만드는 비용이다.**
4. **지금이 고정 비용이 가장 낮은 시점이다.** `apply`를 아직 한 적이 없어 상태 파일도,
   실제 리소스도 없다. 도구를 확정해도 잃는 것이 없고, 미루면 매 세션 다시 흔들린다.

## Alternatives

### AWS CDK (Python)

- **Pros:** 애플리케이션과 같은 언어(Python)로 인프라를 기술한다 — HCL이라는 두 번째 언어를
  배우지 않아도 되고, 타입 힌트와 IDE 지원을 그대로 받는다. 고수준 construct가 ECS 태스크
  정의·IAM 역할 같은 보일러플레이트를 줄여 준다(세션 7에서 늘어날 부분이다).
- **Cons:** AWS에 종속된다 — 다른 클라우드나 온프렘으로 옮길 때 재사용되지 않는다.
  코드와 실제 리소스 사이에 CloudFormation 합성 단계가 끼어 `plan` 대비 추적이 한 겹 깊어진다.
  승인 게이트(`apply.sh`)를 다시 설계해야 한다.
- **Rejected because:** **이직 시장에서 요구되는 폭이 Terraform보다 좁다.** 이 프로젝트의
  포트폴리오 목적상 노출 가치가 판단 기준이며, 사용자가 이 기준으로 확정했다.
  Python 공유라는 장점은 실재하지만 그 기준을 이기지 못한다.

## Consequences

### Positive

- IaC 도구가 고정돼 이후 세션에서 다시 논의하지 않는다. `.claude/rules/iac.md`에 규칙으로
  박아 에이전트도 따른다.
- 이미 작성된 `infra/terraform/`과 `apply.sh` 승인 게이트가 그대로 유효하다. **재작성 없음.**
- ADR-013의 "벤더 종속" 우려가 일부 완화된다 — 배포 대상은 AWS지만 기술 방식은 벤더 중립이라,
  대체 경로를 찾아야 하는 2026년 말 상황(`CLAUDE.md`)에서 IaC 코드의 일부는 재사용된다.

### Negative

- **HCL이라는 두 번째 언어를 유지해야 한다.** 이 레포의 나머지는 전부 Python이다.
  CDK를 골랐다면 없었을 비용이다.
- 세션 7에서 늘어날 ECS 태스크 정의·IAM 역할은 HCL에서 보일러플레이트가 길다.
  CDK의 고수준 construct가 줄여 줬을 부분이다.
- 상태 파일(`terraform.tfstate`)을 직접 관리해야 한다. CloudFormation은 상태를 AWS가 들고
  있어 이 부담이 없다. 현재는 로컬 백엔드라 커밋 금지 규칙으로 막아 뒀지만
  (`infra/terraform/README.md`), 협업이 시작되면 원격 백엔드가 선행 과제가 된다.

### Risks

- **포트폴리오 가치는 검증할 수 없는 전제다.** 측정치가 아니라 시장 판단이며, 시장이 바뀌면
  근거도 바뀐다. 이 ADR은 그 판단을 *기록*하는 것이지 *입증*하지 않는다.
- 두 도구가 섞이는 상황을 규칙으로만 막고 있다. 자동 감지 수단은 없다.

## Implementation

- [x] 레포에 CDK 산출물이 없음을 확인 (`aws-cdk` / `cdk.json` / `CdkStack` 등 전수 검색 0건)
- [x] `infra/terraform/` 유지 — 재작성 불필요
- [x] `.claude/rules/iac.md`에 도구 고정 규칙 추가 (에이전트가 따르도록)
- [ ] 세션 7의 ECS 클러스터 / 태스크 정의 / IAM 역할도 Terraform으로 작성

## Reversibility

- **Reversible:** Yes
- **Rollback:** 아직 `apply`한 적이 없어 상태 파일도 실제 리소스도 없다. 도구를 바꾸려면
  `infra/terraform/`을 지우고 다시 쓰면 되며, 현재 리소스가 2개라 재작성 비용이 작다.
  **다만 `apply` 이후에는 이야기가 다르다** — 이미 만든 리소스를 새 도구로 import해야 하고,
  그 시점부터 Migration Cost는 Medium 이상이 된다.
- **Migration Cost:** Low (현재 시점 한정)

## References

- **Related ADR:** ADR-013 (배포 대상 — *어디에* 배포할지. 이 ADR은 *무엇으로* 기술할지를 다룬다)
- **Documentation:** `.claude/rules/iac.md`, `infra/terraform/README.md`,
  `docs/governance.md` "승인 게이트", `docs/handoff/session-06.md` §7
