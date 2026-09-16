# 최소 구성 — 리소스 2개 (ADR-013, `.claude/rules/iac.md` "리소스는 최소 단위로 추가")
#
# 여기 있는 것은 ECS의 **선행 리소스**뿐이다. 클러스터·태스크 정의·IAM 역할은 아직 없다.
# 이유 두 가지.
#
#   1. `var/`(감사 로그·캐시)를 클라우드에서 어디에 둘지가 아직 미정이다 (ADR-013 Risks).
#      Fargate 태스크의 로컬 스토리지는 태스크 종료와 함께 사라지므로, 지금 태스크 정의를
#      쓰면 **감사 로그가 사라지는 구성**을 코드로 굳히게 된다. 그 결정이 먼저다.
#   2. 이 두 개는 유휴 비용이 사실상 0이라 먼저 만들어도 ADR-013의 전제를 깨지 않는다.
#      ECR은 저장한 GB만큼(수명주기 정책으로 억제), 로그 그룹은 빈 상태에서 $0이다.
#
# **apply 전에 `.claude/deploy-approved` 확인이 필요하다** (`.claude/rules/iac.md`).
# `./apply.sh`를 쓰면 그 확인이 강제된다.

# ---------------------------------------------------------------------------
# 1. ECR — 오케스트레이터 이미지 저장소
# ---------------------------------------------------------------------------
resource "aws_ecr_repository" "orchestrator" {
  name = "${var.project_name}/orchestrator"

  # 같은 태그로 덮어쓸 수 있게 둔다(MUTABLE). IMMUTABLE로 두면 `:dev` 재푸시가 막혀
  # 개발 중 매번 태그를 바꿔야 한다. 배포 태그를 도입하는 시점에 다시 본다.
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    # 푸시할 때마다 취약점 스캔. 추가 비용이 없는 기본 스캔이다.
    # 이 이미지는 torch를 포함해 의존성 표면이 넓다 (ADR-005 Consequences).
    scan_on_push = true
  }

  encryption_configuration {
    # AES256은 AWS 관리 키다. KMS 고객 관리 키는 월 $1/키의 고정비가 붙는데,
    # 이 이미지에 시크릿이 없으므로(ADR-010에서 검사로 확인) 값을 하지 않는다.
    encryption_type = "AES256"
  }
}

# 수명주기 정책 — 이미지 3.46GB × 누적이 유휴 고정비를 만드는 유일한 경로다.
resource "aws_ecr_lifecycle_policy" "orchestrator" {
  repository = aws_ecr_repository.orchestrator.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "태그 없는 이미지는 ${var.ecr_untagged_expire_days}일 뒤 만료"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = var.ecr_untagged_expire_days
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "태그된 이미지는 최근 ${var.ecr_keep_last_images}개만 보관"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.ecr_keep_last_images
        }
        action = { type = "expire" }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# 2. CloudWatch Logs — 태스크 stdout/stderr
# ---------------------------------------------------------------------------
#
# ⚠️ **이것은 감사 로그가 아니다.** ADR-007의 감사 로그는 `var/traces/`의 JSONL이고
# 프롬프트·응답 본문을 담는다. 여기 오는 것은 `run_research.py`가 터미널에 찍는 요약이다.
#
# 다만 **터미널 요약에도 질의 원문과 초안 본문이 포함된다.** 즉 이 로그 그룹은
# "덜 민감"할 뿐 무해하지 않다. 실데이터를 넣기 시작하면(ADR-004 전제가 깨지면)
# 이 그룹의 보존 기간과 접근 권한도 함께 정해야 한다.
resource "aws_cloudwatch_log_group" "orchestrator" {
  name              = "/ecs/${var.project_name}/orchestrator"
  retention_in_days = var.log_retention_days
}
