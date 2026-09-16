# infra/terraform — ECS Fargate 온디맨드 배포 (ADR-013 / ADR-015 / ADR-016)

> **아직 `apply`하지 않았다.** 코드만 준비돼 있고 `terraform validate`까지 통과했다.
> 실제 리소스 생성은 사용자 승인을 받고 진행한다.

## 검증 상태 (session-07)

| 검사 | 결과 |
| --- | --- |
| `terraform fmt -check -recursive` | 통과 |
| `terraform init -backend=false` | hashicorp/aws **v5.100.0** 설치 |
| `terraform validate` | **통과** |
| `terraform plan` / `apply` | **미실행** — AWS 자격증명 필요 |

Terraform **v1.16.2** (`~> 1.9` 충족), AWS CLI **v2.36.46**. 세션 7에서 winget으로 설치했다 —
세션 6에서는 CLI가 없어 `validate`조차 돌리지 못했다.

⚠️ **`validate`는 문법·스키마 검사다.** 계정 상태에 따라 실패할 수 있는 것(권한 부족,
서비스 한도, 이름 충돌)은 `plan`/`apply`에서만 드러난다.

## 무엇을 만드는가

**리소스 27개 / 종류 19개.** 세션 6에서는 2개였는데, 선행 결정(`var/` 저장소 — ADR-015)이
풀리면서 VPC·EFS·IAM·ECS가 들어왔다.

| 파일 | 리소스 | 개수 | 유휴 비용 |
| --- | --- | ---: | --- |
| `main.tf` | ECR 리포지토리 + 수명주기 정책 | 2 | ~$0.35/월 (3.46GB 이미지 1개) |
| `main.tf` | CloudWatch 로그 그룹 (14일 보존) | 1 | ~$0.01/월 |
| `network.tf` | VPC / IGW / 서브넷 2 / 라우트 테이블 + 연결 2 / SG 2 | 9 | **$0** |
| `efs.tf` | EFS + 마운트 타깃 2 + 액세스 포인트 3 + 파일시스템 정책 | 7 | ~$0.04/월 (<100MB) |
| `iam.tf` | 역할 2 (execution / task) + 인라인 정책 2 + 관리형 연결 1 | 5 | **$0** |
| `ecs.tf` | 클러스터 + 태스크 정의 | 2 | **$0** (존재 자체는 무료) |
| `budget.tf` | AWS Budgets 월 예산 | 1 | **$0** (계정당 2개까지 무료) |
| | **합계** | **27** | **~$0.40/월** |

실행 1회(10분) **~$0.020**. 월 100회 실행해도 유휴 포함 **~$2.4**.

**`aws_ecs_service`는 일부러 없다.** 오케스트레이터는 데몬이 아니라 작업 컨테이너이고
(ADR-010), 서비스를 두면 `desiredCount=1`만으로 유휴 고정비가 **월 ~$87**이 된다 —
ADR-013이 기각한 EKS($73)보다 비싸다. 실행은 `RunTask`로 한다.

**NAT Gateway도 없다.** 태스크는 퍼블릭 서브넷에서 퍼블릭 IP로 직접 나간다.
세 가지 아웃바운드 경로의 비교는 `network.tf` 상단 주석에 있다.

## 승인 게이트

`terraform apply` / `destroy`는 **직접 실행하지 않는다.** `./apply.sh`를 쓴다.

```bash
./apply.sh plan       # 게이트 없음 — 상태를 바꾸지 않는다
./apply.sh apply      # .claude/deploy-approved 없으면 차단
./apply.sh destroy    # 같음
```

게이트는 이제 **세 겹이다.** 셋 다 통과해야 리소스가 만들어진다.

1. **사용자의 명시적 승인** — 사람이 판단한다. 스크립트가 대신할 수 없다.
2. **`.claude/deploy-approved` 파일 존재** — `apply.sh`가 강제한다.
3. **도구 계층 (session-07 추가)** — `.claude/settings.json`의 `permissions.deny`와
   `.claude/hooks/check-deploy-approval.sh`(PreToolUse). 스크립트를 우회해 명령을 직접 쳐도
   걸린다. `deny`는 **승인 파일로도 풀리지 않는 하드 차단**이라, `aws` CLI 명령은
   에이전트가 아니라 **사람이 실행한다.**

승인 파일은 `.gitignore` 대상이라 커밋되지 않는다. **승인은 이 머신에 한정된다.**
배포 확인이 끝나면 **삭제해서** 다음 배포에 다시 승인을 받는다.

## apply 전 사전 준비

### 1. 자격증명

```bash
aws configure    # IAM 사용자 키. 루트 계정 키를 쓰지 않는다.
```

리전 `ap-northeast-2`(서울, ADR-013 Amendment에서 확정). 필요 권한:
`ecr:*` `logs:*` `ec2:*`(VPC) `elasticfilesystem:*` `iam:*` `ecs:*` `ssm:PutParameter` `budgets:*`.

### 2. `VLLM_BASE`를 SSM Parameter Store에 넣는다

⚠️ **Terraform이 이 값을 만들지도 읽지도 않는다.** `aws_ssm_parameter` 리소스나
`data.aws_ssm_parameter`를 쓰면 값이 **`terraform.tfstate`에 평문으로** 들어가는데,
`docs/governance.md` "시크릿 취급"이 금지하는 것이 정확히 그것이다.
그래서 `ecs.tf`는 **ARN만 문자열로 조립**한다.

```bash
# 레포 루트에서. URL 조각을 명령에 직접 적지 않고 .env에서 읽는다
# (governance의 커밋 전 확인과 같은 자기참조 방식).
aws ssm put-parameter \
  --name /multiagent-research-lab/vllm_base \
  --type SecureString \
  --value "$(sed -n 's/^VLLM_BASE=//p' .env)" \
  --region ap-northeast-2
```

Standard 티어 + 기본 KMS 키(`aws/ssm`)는 **무료**다.
이 값은 태스크 정의의 `secrets`로 주입되므로 **태스크 정의에 남지 않는다** —
`environment`에 넣으면 `ecs:DescribeTaskDefinition` 권한자 누구나 읽는다.

### 3. `terraform.tfvars`

`budget_alert_email`은 **기본값이 없다.** 넣지 않으면 apply가 실패한다 — 의도다.
기본값을 두면 "비용 알람이 조용히 안 만들어진 상태"가 기본값이 된다.

```bash
cp terraform.tfvars.example terraform.tfvars
# budget_alert_email = "you@example.com"  주석을 풀고 값을 채운다
```

### 4. 이미지 푸시

`apply` 후 `terraform output push_commands`가 리전·계정 ID가 채워진 명령을 출력한다.
⚠️ **푸시 전에 이미지에 시크릿이 없는지 확인한다** — 검사 절차는 ADR-010 Evidence에 있다.

## 실행

`terraform output run_commands`가 클러스터·태스크 정의·네트워크 설정이 채워진 명령을 낸다.
요약하면:

```bash
# 헬스체크 (태스크 정의 기본 CMD)
aws ecs run-task --cluster multiagent-research-lab \
  --task-definition multiagent-research-lab-orchestrator \
  --launch-type FARGATE \
  --network-configuration "$(terraform output -raw network_config)" \
  --region ap-northeast-2

# 인덱스 구축 / 리서치는 --overrides 로 command를 덮어쓴다 (run_commands 출력 참조)
# 로그: aws logs tail /ecs/multiagent-research-lab/orchestrator --follow --region ap-northeast-2
```

첫 실행은 **3.46GB 이미지를 풀하므로 느리다.** 콜드 스타트는 아직 실측되지 않았다.

## 삭제 절차

`.claude/rules/iac.md`가 "삭제 절차도 함께 문서화"를 요구한다.
**세션 6보다 중요해졌다 — 이제 상태를 가진 리소스(EFS)가 있다.**

```bash
# 0. ⚠️ EFS에 남길 것이 있으면 먼저 꺼낸다. destroy는 데이터째 지우고 되돌릴 수 없다.
#    태스크를 띄워 마운트한 상태에서 복사하는 것이 유일한 경로다
#    (감사 로그가 여기 있다 — ADR-007).

# 1. ECR에 이미지가 남아 있으면 destroy가 실패한다.
#    force_delete를 켜지 않은 것은 의도다 — 실수로 이미지를 날리지 않기 위해서다.
aws ecr list-images --repository-name multiagent-research-lab/orchestrator --region ap-northeast-2
aws ecr batch-delete-image \
  --repository-name multiagent-research-lab/orchestrator \
  --region ap-northeast-2 --image-ids imageTag=dev

# 2. 실행 중인 태스크가 없는지 확인 (있으면 EFS 마운트 타깃 삭제가 막힌다)
aws ecs list-tasks --cluster multiagent-research-lab --region ap-northeast-2

# 3. destroy
./apply.sh destroy

# 4. SSM 파라미터는 Terraform 관리 밖이라 destroy로 지워지지 않는다 — 따로 지운다
aws ssm delete-parameter --name /multiagent-research-lab/vllm_base --region ap-northeast-2

# 5. 확인 — 남은 것이 없어야 한다
aws ecr describe-repositories --region ap-northeast-2 | grep -c multiagent-research-lab || echo "없음"
aws efs describe-file-systems --region ap-northeast-2 | grep -c multiagent-research-lab || echo "없음"
aws logs describe-log-groups --log-group-name-prefix /ecs/multiagent-research-lab --region ap-northeast-2
```

되돌릴 수 없는 것 두 가지:

- **EFS 데이터** — `var/traces`의 감사 로그가 여기 있다. 0단계를 건너뛰면 사라진다.
- **CloudWatch 로그 스트림** — `destroy`가 함께 지운다. 남겨야 하면 먼저 내보낸다.

## 상태 파일

백엔드는 로컬(기본)이다. 원격 상태(S3 + DynamoDB)는 **그 자체가 새 유료 리소스**라
승인 대상이고, 지금은 1인 작업이라 잠금이 필요 없다.

⚠️ `terraform.tfstate`가 로컬 파일로 남고 리소스 ARN 등이 들어간다. **커밋하지 않는다**
(레포 `.gitignore`). 협업이 시작되면 원격 백엔드가 먼저다.

**시크릿은 상태 파일에 없다** — `VLLM_BASE`를 Terraform이 다루지 않도록 설계했기 때문이다
(위 "사전 준비 2" 참조). 이것이 그 설계의 실질적 이득이다.
