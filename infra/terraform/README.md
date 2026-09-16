# infra/terraform — 최소 구성 (ADR-013)

> **이 세션(06)에서는 `apply`하지 않았다.** 코드만 준비돼 있다.
> 실제 리소스 생성은 세션 7에서 사용자 승인을 받고 진행한다.

## 무엇을 만드는가

리소스 2개뿐이다. 둘 다 ECS의 **선행 리소스**이면서 유휴 비용이 사실상 0인 것들이다.

| 리소스 | 이름 | 유휴 비용 |
| --- | --- | --- |
| `aws_ecr_repository` (+ 수명주기 정책) | `multiagent-research-lab/orchestrator` | 저장한 GB만큼 ($0.10/GB-월) |
| `aws_cloudwatch_log_group` | `/ecs/multiagent-research-lab/orchestrator` | 빈 상태에서 $0 |

**클러스터·태스크 정의·IAM 역할은 아직 없다.** `var/`(감사 로그·캐시)를 클라우드에서
어디에 둘지가 정해지지 않았기 때문이다 (ADR-013 Risks). Fargate 태스크의 로컬 스토리지는
태스크 종료와 함께 사라지므로, 지금 태스크 정의를 쓰면 **감사 로그가 사라지는 구성**을
코드로 굳히게 된다. 그 결정이 먼저다.

## 승인 게이트

`terraform apply` / `destroy`는 **직접 실행하지 않는다.** `./apply.sh`를 쓴다.

```bash
./apply.sh plan       # 게이트 없음 — 상태를 바꾸지 않는다
./apply.sh apply      # .claude/deploy-approved 없으면 차단
./apply.sh destroy    # 같음
```

게이트는 두 가지를 요구하고 **둘 다** 필요하다 (`docs/governance.md` "승인 게이트",
`.claude/rules/iac.md`).

1. **사용자의 명시적 승인** — 사람이 판단한다. 스크립트가 대신할 수 없다.
2. **`.claude/deploy-approved` 파일 존재** — 스크립트가 강제한다.

승인 파일은 `.gitignore` 대상이라 커밋되지 않는다. **승인은 이 머신에 한정된다.**

## 삭제 절차

`.claude/rules/iac.md`가 "삭제 절차도 함께 문서화"를 요구한다.

```bash
# 1. ECR 리포지토리에 이미지가 남아 있으면 destroy가 실패한다.
#    force_delete를 켜지 않은 것은 의도다 — 실수로 이미지를 날리지 않기 위해서다.
aws ecr list-images --repository-name multiagent-research-lab/orchestrator --region ap-northeast-2
aws ecr batch-delete-image \
  --repository-name multiagent-research-lab/orchestrator \
  --region ap-northeast-2 \
  --image-ids imageTag=dev

# 2. destroy
./apply.sh destroy

# 3. 확인 — 남은 것이 없어야 한다
aws ecr describe-repositories --region ap-northeast-2 | grep -c multiagent-research-lab || echo "없음"
aws logs describe-log-groups --log-group-name-prefix /ecs/multiagent-research-lab --region ap-northeast-2
```

CloudWatch 로그 그룹은 `destroy`가 로그 스트림까지 함께 지운다 — **되돌릴 수 없다.**
남겨야 할 로그가 있으면 먼저 내보낸다.

## 상태 파일

백엔드는 로컬(기본)이다. 원격 상태(S3 + DynamoDB)는 **그 자체가 새 유료 리소스**라
승인 대상이고, 지금은 1인 작업이라 잠금이 필요 없다.

⚠️ `terraform.tfstate`가 로컬 파일로 남고 리소스 ARN 등이 들어간다. **커밋하지 않는다**
(레포 `.gitignore`에 추가돼 있다). 협업이 시작되면 원격 백엔드가 먼저다.

## apply 전 사전 준비

`docs/handoff/session-06.md`의 "다음 세션 사전 준비" 목록을 참조한다.
