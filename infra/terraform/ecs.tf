# ECS — 클러스터 + 태스크 정의
#
# **서비스(aws_ecs_service)를 만들지 않는다.** 오케스트레이터는 데몬이 아니라
# 작업 컨테이너이고(ADR-010), 서비스는 "원하는 개수만큼 상시 유지"가 목적이라
# desired_count=1이면 그 순간 유휴 고정비가 월 ~$83이 된다. 실행은 run-task로 한다.
#
# 클러스터와 태스크 정의는 **존재 자체로는 $0이다.** 과금은 태스크가 도는 동안만.

resource "aws_ecs_cluster" "main" {
  name = var.project_name

  setting {
    # Container Insights는 CloudWatch 커스텀 메트릭 과금이 붙는다($0.30/메트릭-월 규모).
    # 태스크가 한 번에 하나 도는 배치에 값을 하지 않는다. 필요해지면 켠다.
    name  = "containerInsights"
    value = "disabled"
  }
}

# ---------------------------------------------------------------------------
# VLLM_BASE — SSM Parameter Store SecureString
# ---------------------------------------------------------------------------
# ⚠️ **이 파라미터를 Terraform으로 만들지 않는 것은 의도다.**
#
# `aws_ssm_parameter` 리소스를 쓰면 값이 `terraform.tfstate`에 **평문으로** 들어간다.
# governance.md "시크릿 취급"이 금지하는 것이 정확히 그것이다(.env 외 평문 금지).
# 그래서 여기서는 **ARN만 문자열로 조립**하고 값은 건드리지 않는다.
# `data.aws_ssm_parameter`도 쓰지 않는다 — 데이터 소스도 값을 상태 파일에 남긴다.
#
# 파라미터는 apply 전에 한 번 수동으로 만든다 (infra/terraform/README.md 참조):
#
#   aws ssm put-parameter --name <var.vllm_base_param_name> --type SecureString \
#       --value "$(sed -n 's/^VLLM_BASE=//p' .env)" --region <region>
#
# 명령줄에 URL을 직접 적지 않고 `.env`에서 읽는다 — governance의 커밋 전 확인과 같은 방식이다.
#
# Task definition의 `secrets`로 주입하면 값이 **태스크 정의에 남지 않는다.**
# `environment`에 넣으면 `ecs:DescribeTaskDefinition` 권한자 누구나 읽을 수 있다 —
# 무인증 엔드포인트라 URL이 곧 접근 권한이므로(governance) 그 차이가 결정적이다.
locals {
  vllm_base_param_arn = "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter${var.vllm_base_param_name}"
}

# ---------------------------------------------------------------------------
# 태스크 정의
# ---------------------------------------------------------------------------
resource "aws_ecs_task_definition" "orchestrator" {
  family                   = "${var.project_name}-orchestrator"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"

  cpu    = var.task_cpu
  memory = var.task_memory

  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  # 이미지가 3.46GB다(ADR-010). Fargate 기본 임시 스토리지는 20GB로 충분하지만,
  # 압축 해제 + 레이어 캐시 여유를 조금 둔다. 21GB까지는 추가 과금이 없다.
  ephemeral_storage {
    size_in_gib = 21
  }

  # --- EFS 볼륨 3개 — compose의 named volume과 1:1 --------------------------
  dynamic "volume" {
    for_each = local.efs_access_points

    content {
      name = volume.key

      efs_volume_configuration {
        file_system_id = aws_efs_file_system.var_data.id

        # 전송 중 암호화. EFS 파일시스템 정책의 SecureTransport 조건과 짝이다.
        transit_encryption = "ENABLED"

        authorization_config {
          access_point_id = aws_efs_access_point.var_data[volume.key].id
          # 태스크 역할로 IAM 인증. 끄면 파일시스템 정책이 걸러낼 주체가 없어진다.
          iam = "ENABLED"
        }
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "orchestrator"
      image     = "${aws_ecr_repository.orchestrator.repository_url}:${var.image_tag}"
      essential = true

      # 기본 명령은 헬스체크다(Dockerfile CMD와 같다). 실제 리서치는 run-task의
      # containerOverrides.command로 덮어쓴다 — outputs.tf의 run_commands 참조.

      environment = [
        # 컨테이너 안의 마운트 경로. compose와 같은 값이다.
        { name = "TRACE_LOG_DIR", value = "/app/var/traces" },
        { name = "LLM_CACHE_DIR", value = "/app/var/llm_cache" },
        { name = "CHROMA_DIR", value = "/app/var/chroma" },
        # 캐시는 기본 꺼짐 — 측정 왜곡 방지 (governance "측정 방법론", ADR-008).
        { name = "LLM_CACHE", value = "" },
        # 모델 이름은 시크릿이 아니다 (ADR-003). 엔드포인트 URL만 시크릿이다.
        { name = "VLLM_MODEL", value = var.vllm_model },
        # Langfuse는 클라우드에 올리지 않는다 (ADR-016). 비워 두면 로컬 JSONL만 기록한다.
        { name = "LANGFUSE_HOST", value = "" },
      ]

      secrets = [
        {
          name      = "VLLM_BASE"
          valueFrom = local.vllm_base_param_arn
        },
      ]

      mountPoints = [
        for key, path in local.efs_access_points : {
          sourceVolume  = key
          containerPath = "/app/var/${key}"
          readOnly      = false
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.orchestrator.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = "task"
        }
      }

      # ⚠️ Dockerfile의 HEALTHCHECK는 Fargate에서 무시된다 — 여기 정의한 것만 돈다.
      # 작업 컨테이너라 상시 헬스체크가 의미는 적지만, 설정 주입 실패를
      # 종료 코드보다 먼저 드러내 준다.
      healthCheck = {
        command     = ["CMD", "python", "scripts/healthcheck.py"]
        interval    = 60
        timeout     = 30
        retries     = 2
        startPeriod = 60
      }
    }
  ])
}
