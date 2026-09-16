# ADR-017: 배포자 IAM 권한을 관리형 정책 다발에서 최소 권한 고객 관리형 정책으로 교체

- **Status:** Proposed
- **Date:** 2026-09-16
- **Decision:** 배포자 IAM 사용자에 붙은 AWS 관리형 정책을 전부 떼고, 이 레포의
  Terraform·이미지 푸시·태스크 실행이 실제로 쓰는 액션만 담은 **고객 관리형 정책 2개**
  (`multiagent-research-lab-deploy-core` / `-app`)로 교체한다.
- **Scope:** `infra/iam/`, 배포자 자격증명 (AWS 계정 IAM)
- **Decision Source:** Human

---

## Context

### Problem

세션 7에서 `terraform apply`를 통과시키려고 **권한을 실패할 때마다 하나씩 붙였다.**
`ec2:CreateTags` → `ssm:PutParameter` → ECR/ECS/Logs 순으로 네 번에 걸쳐 드러났고,
그때마다 관리형 정책을 통째로 붙이는 방식으로 해결했다. 세션 8에서 실제 상태를 확인한
결과 **관리형 정책 10개**가 붙어 있다 (세션 7 핸드오프는 5개로 기록했다 — 그 뒤에 붙은
EFS·IAM·Budgets·Logs Full이 기록되지 않았다).

```
AmazonEC2ContainerRegistryFullAccess  AmazonEC2ContainerRegistryPowerUser
AmazonECS_FullAccess                  AmazonVPCFullAccess
AmazonSSMFullAccess                   AmazonElasticFileSystemFullAccess
CloudWatchLogsFullAccess              CloudWatchLogsReadOnlyAccess
IAMFullAccess                         AWSBudgetsActionsWithAWSResourceControlAccess
```

⚠️ **`IAMFullAccess`가 가장 크다.** 이 사용자는 역할을 새로 만들어 임의의 정책을 붙일 수
있으므로 **ADR-015가 태스크 역할에 건 격리를 그대로 우회할 수 있다.** `docs/governance.md`가
"IAM 통제 위에 계정 관리자가 있다"고 적어 둔 주체가 **배포자 본인**이라는 뜻이다.
중복도 있다 — ECR Full ⊃ PowerUser, Logs Full ⊃ ReadOnly. **실패할 때마다 붙인 흔적이다.**

**이것은 같은 세션이 태스크 역할에 적용한 원칙과 정반대다.** ADR-015는 태스크 역할에
`ClientRootAccess`를 주지 않고, 액세스 포인트 경유만 허용하고, 파일시스템 정책으로
비TLS를 거부했다. **실행 주체는 최소 권한으로 설계했는데 그것을 배포한 주체는
반대 방향으로 갔다.** 배포가 끝나 필요한 액션 목록이 확정된 지금이 좁힐 시점이다.

같은 맥락의 판단 하나를 여기 함께 기록한다. 세션 7 핸드오프(`docs/handoff/session-07.md`)에
**배포자 IAM 사용자명이 평문으로 들어가 푸시됐다.** 히스토리 재작성은 하지 않기로 했다 —
근거는 아래 Rationale 4.

### Constraints

- **IAM 사용자 인라인 정책은 2,048자 제한이다.** 이 레포가 필요로 하는 액션 목록은
  공백을 제거해도 core 2,287자 / app 3,469자다. **인라인으로는 들어가지 않는다.**
  고객 관리형 정책은 6,144자라 둘 다 들어간다.
- 배포자가 자기 자신에게 정책을 붙일 수 있으면(`iam:AttachUserPolicy`) "최소 권한"이
  스스로를 관리자로 승격할 수 있다는 뜻이 되어 의미가 없어진다. 그래서 넣지 않으며,
  **그 결과 교체 후 롤백 경로는 루트 계정 콘솔뿐이다.**
- `iam:AttachRolePolicy`를 프로젝트 역할에 열어 주면 그 역할에 `AdministratorAccess`를
  붙인 뒤 태스크로 실행하는 경로가 생긴다. 조건 없이 열 수 없다.
- `ecs:RegisterTaskDefinition` / `DeregisterTaskDefinition`은 리소스 수준 권한을
  지원하지 않는다. `Resource: "*"`가 불가피하다.

## Decision

### Selected

- **Technology:** AWS IAM 고객 관리형 정책 2개 + 부착/분리 스크립트
- **Architecture:** 정책을 **자원군으로 쪼갠다.** `-core`는 네트워크·EFS·예산
  (`aws:RequestedRegion` 조건으로 서울 리전에 가둔다), `-app`은 ECR·ECS·Logs·SSM·IAM 역할
  (대부분 프로젝트 접두사 ARN으로 가둔다). 하나로 합치면 6,144자에 닿는다.
- **Implementation:**
  - `infra/iam/deploy-core.json` / `deploy-app.json` — 계정 ID 자리는 `ACCOUNT_ID`
    자리표시자로 두고 **커밋한다.** 렌더링 결과(`infra/iam/.rendered/`)는 `.gitignore` 대상이다.
  - `infra/iam/apply_least_privilege.sh` — `show` / `attach` / `detach` / `rollback`.
    사용자명을 코드에 적지 않고 호출자 ARN에서 뽑는다.
  - **순서를 스크립트가 강제한다: 붙이고 → `./apply.sh plan`으로 확인하고 → 뗀다.**
    반대로 하면 권한이 하나도 없는 구간이 생기고, 거기서 실패하면 되돌릴 권한조차 없다.
  - 권한 경계 3곳: `iam:PassRole`은 프로젝트 역할 2개 + `ecs-tasks.amazonaws.com`로,
    `iam:AttachRolePolicy`는 `AmazonECSTaskExecutionRolePolicy` **하나로**,
    `iam:CreateServiceLinkedRole`은 `ecs`/`elasticfilesystem` 서비스명으로 제한한다.
  - 예외 1건: `logs:DescribeLogGroups`는 접두사 패턴으로 좁히면 거부되는 경우가 있어
    해당 계정·리전의 `log-group:*`로 따로 뒀다. 로그 그룹 메타데이터 조회라 위험이 낮다.
    나머지 `logs:*`는 `/ecs/multiagent-research-lab/*`에 묶여 있다.

## Rationale

1. **필요한 액션 목록이 이제 확정됐다.** 세션 7의 apply가 27/27 성공했고 drift가 0이므로,
   Terraform 구성에 선언된 리소스가 요구하는 액션이 곧 필요 권한의 전부다. 배포 전이라면
   추측이었겠지만 지금은 목록을 코드에서 읽어낼 수 있다.
2. **이 프로젝트의 서사가 "역할 분리"다.** ADR-015가 태스크 역할/실행 역할을 나눈 이유를
   설명해 놓고 배포자만 계정 전체 권한을 들고 있으면, 그 설명이 원칙이 아니라 편의였다는
   뜻이 된다. 고치는 비용이 낮고(스크립트 1개) 드러나는 비용이 크다.
3. **인라인이 아니라 관리형인 것은 취향이 아니라 제약이다.** 2,048자에 안 들어간다(위 Constraints).
   버전 관리·롤백(정책 버전 5개)이 따라오는 것은 부수 이득이다.
4. **사용자명·계정 ID 노출에 대해서는 히스토리 재작성을 하지 않는다.** 액세스 키 없이
   계정 ID와 사용자명만으로 할 수 있는 것이 없고, 이 사용자는 콘솔 로그인이 비활성이다.
   실질적 방어는 이 ADR의 권한 축소 쪽이며, 권한이 좁아지면 이름이 알려져도 정찰 가치가
   사라진다. 히스토리 재작성은 v1.0 태그와 푸시된 커밋을 전부 갈아엎는 비용인데
   막는 것이 없다. **앞으로 새로 쓰는 문서에서 마스킹하는 것만 지킨다.**
   (레포 전수 검사 결과 **계정 ID는 커밋 이력 어디에도 없다** — 노출된 것은 사용자명뿐이다.)

## Evidence

- **Production Data:** 세션 7 apply 결과 — 리소스 **27/27 생성, drift 0**
  (`terraform plan` → "No changes"). 필요 액션 목록의 근거가 되는 상태.
- **Cost:** IAM 정책·역할은 유휴 고정비 $0. 이 변경은 비용을 바꾸지 않는다.
- **Benchmark:** 정책 크기(공백 제거) — core **2,287자**, app **3,600자**.
  IAM 사용자 인라인 한도 2,048자 초과, 관리형 한도 6,144자 이내.
- **Production Data:** 교체 전 부착 상태 — **관리형 정책 10개, 인라인 0개**
  (`apply_least_privilege.sh show`, 2026-09-16 실측).

## Alternatives

### 현행 유지 (AWS 관리형 정책 다발)

- **Pros:** 추가 작업 0. 앞으로 리소스를 늘려도 권한 때문에 막히지 않는다.
- **Cons:** 배포자 자격증명 하나가 새면 계정의 VPC·SSM·ECR·ECS·Logs 전반이 함께 샌다.
  ADR-015가 태스크 역할에 적용한 원칙과 정면으로 어긋난다.
- **Rejected because:** 배포가 끝나 필요 액션이 확정됐으므로 "넓게 열어 두는" 근거였던
  불확실성이 사라졌다.

### IAM 사용자 인라인 정책

- **Pros:** 사용자에 붙어 다니므로 정책 객체를 따로 관리하지 않는다. 사용자를 지우면 같이 사라진다.
- **Cons:** 2,048자 한도. 버전 관리·롤백이 없다.
- **Rejected because:** 필요한 액션 목록이 한도를 넘는다(core 2,287 / app 3,600).
  **기술적으로 불가능하다.**
- **Recheck if:** Terraform 구성이 줄어 액션 목록이 2,048자 안에 들어오게 되는 경우.

### 권한 경계(Permissions Boundary)만 붙이고 관리형 정책은 유지

- **Pros:** 정책을 다시 쓰지 않고 상한만 씌운다.
- **Cons:** 경계는 **상한**이라 실제 부여 권한은 여전히 넓다. 경계 문서 자체가 또 하나의
  6,144자 문서라 결과적으로 같은 목록을 쓰게 된다.
- **Rejected because:** 같은 작업량으로 실제 권한을 줄이지 못한다.

## Consequences

### Positive

- 배포자 자격증명 유출 시 손에 들어오는 것이 **이 프로젝트의 리소스 + 서울 리전**으로 제한된다.
- 사용자명이 알려져 있어도 정찰 가치가 낮아진다 (Rationale 4가 히스토리 재작성을 하지 않는
  근거로 삼은 바로 그 효과다 — **이 ADR이 적용돼야 그 판단이 성립한다**).
- 필요 권한이 **코드로 남는다.** 다른 머신·CI에서 재현할 때 "무엇이 필요한지"를 다시 발견하지 않는다.

### Negative

- 리소스를 새로 추가하면 **권한이 또 막힌다.** 세션 7의 "네 번에 걸쳐 하나씩" 경험이
  반복될 수 있고, 이번에는 정책 파일을 고쳐야 한다.
- 정책 객체 2개 + 스크립트 1개가 유지 대상으로 늘어난다.

### Risks

- ⚠️ **잠금 위험.** `iam:AttachUserPolicy`도 `iam:CreatePolicyVersion`도 넣지 않았다 —
  넣으면 자기 정책에 관리자 상당 권한을 써 넣을 수 있어 최소 권한이 무의미해진다.
  **그 대가로 분리 후에는 이 정책을 고칠 수도, 다시 붙일 수도 없다. 롤백·수정 경로는
  루트 계정 콘솔뿐이다.** 이것은 자기제한(self-limiting)의 본질적 비용이며 우회하면
  목적 자체가 사라진다. 분리 전에 루트 로그인 수단(MFA 포함)이 살아 있는지 확인해야 한다.
- ⚠️ **드러나지 않은 액션이 남아 있을 수 있다.** 이 목록은 Terraform 구성에서 역산한 것이고,
  `plan`은 읽기만 하므로 **`plan` 통과가 `apply`/`destroy` 통과를 보장하지 않는다.**
  세션 7이 "validate 통과 ≠ apply 통과"로 겪은 것과 같은 클래스의 문제다.
- ⚠️ **계정 관리자 권한은 이 통제 위에 있다** (ADR-015와 같은 단서). `iam:*` 보유 주체는
  정책을 다시 붙일 수 있다. 이 ADR이 막는 것은 배포자 자격증명 유출이지 관리자가 아니다.

## Implementation

- [x] `infra/iam/deploy-core.json` / `deploy-app.json` 작성 (계정 ID 자리표시자)
- [x] `infra/iam/apply_least_privilege.sh` (show/attach/detach/rollback, 순서 강제)
- [x] `infra/iam/.rendered/`를 `.gitignore`에 추가
- [ ] **`attach` 실행 후 `./apply.sh plan` 확인** — 사람이 실행한다 (`aws`는 deny 대상)
- [ ] **`detach` 실행 후 `./apply.sh plan` 재확인** — 이것이 실제 검증이다
- [ ] `apply`/`destroy` 경로 권한은 다음 배포·철거 때 드러난다 (위 Risks)

## Reversibility

- **Reversible:** Partial
- **Rollback:** `apply_least_privilege.sh rollback <분리했던 정책ARN...>`
  (분리 목록은 `infra/iam/.rendered/detached-*.txt`에 남는다). **단, 분리 후에는 배포자에게
  그 명령을 실행할 권한이 없다 — 루트 계정 콘솔에서 재부착해야 한다.**
- **Migration Cost:** Low (정책 부착/분리뿐, 인프라 리소스는 건드리지 않는다)

## Review Trigger

- Terraform 구성이 줄어 필요 액션 목록이 2,048자 안에 들어오게 되는 경우 —
  인라인 정책으로 옮길지 재검토.

## References

- **Related ADR:** ADR-013(배포 대상), ADR-014(Terraform), ADR-015(EFS·태스크 역할 최소 권한)
- **Documentation:** `docs/handoff/session-07.md` §5(권한이 네 번에 걸쳐 드러난 경위),
  `infra/terraform/iam.tf`(태스크/실행 역할 분리), `docs/security/owasp-notes.md`
