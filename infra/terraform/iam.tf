# IAM — 역할 2개. **분리하는 것이 요점이다.**
#
#   execution role : ECS 에이전트가 쓴다. 이미지 pull, 로그 쓰기, 시크릿 읽기.
#                    **우리 코드는 이 역할을 쓰지 않는다.**
#   task role      : 컨테이너 안의 프로세스가 쓴다. EFS 마운트/쓰기만.
#
# 둘을 하나로 합치면 애플리케이션 코드가 ECR·시크릿 권한을 그대로 갖게 된다.
# 분리해 두면 인젝션으로 코드 실행이 뚫려도(ADR-009) 손에 들어오는 권한이
# "EFS 3개 디렉터리"로 제한된다.
#
# IAM 역할·정책은 유휴 고정비 $0이다.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    # 혼동된 대리인(confused deputy) 방지 — 이 계정의 ECS 태스크만 이 역할을 맡을 수 있다.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

# ---------------------------------------------------------------------------
# 1. 실행 역할 (ECS 에이전트)
# ---------------------------------------------------------------------------
resource "aws_iam_role" "execution" {
  name               = "${var.project_name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
  # ECS 에이전트용 — ECR pull / CloudWatch Logs / SSM 시크릿 조회
  # (IAM description은 [ -~¡-ÿ]만 허용한다 — 한국어는 주석으로 둔다.
  #  terraform validate는 이걸 못 잡는다. 서버 측 검증이라 apply에서만 드러났다.)
  description = "ECS agent: ECR pull, CloudWatch Logs, SSM secret lookup"
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# 시크릿 조회 — `VLLM_BASE`를 SSM Parameter Store에서 읽는다.
# 관리형 정책에는 SSM 권한이 없어서 따로 붙인다. **이름으로 한 개만** 허용한다.
data "aws_iam_policy_document" "execution_secrets" {
  statement {
    sid       = "ReadVllmBaseParameter"
    effect    = "Allow"
    actions   = ["ssm:GetParameters"]
    resources = [local.vllm_base_param_arn]
  }

  statement {
    sid     = "DecryptSecureString"
    effect  = "Allow"
    actions = ["kms:Decrypt"]
    # SSM SecureString의 기본 키(aws/ssm). 고객 관리 키가 아니라 고정비가 없다.
    resources = ["arn:aws:kms:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:key/*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${data.aws_region.current.name}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "read-vllm-base"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

# ---------------------------------------------------------------------------
# 2. 태스크 역할 (컨테이너 안의 프로세스)
# ---------------------------------------------------------------------------
# **이 역할이 `var/`의 "접근 주체"다.** governance.md의 클라우드 기준이 가리키는 것이 여기다.
resource "aws_iam_role" "task" {
  name               = "${var.project_name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
  # 오케스트레이터 프로세스용 — EFS(traces/llm_cache/chroma) 읽기·쓰기만
  description = "Orchestrator process: read/write EFS traces/llm_cache/chroma only"
}

data "aws_iam_policy_document" "task_efs" {
  statement {
    sid    = "MountAndWriteViaAccessPoints"
    effect = "Allow"
    actions = [
      "elasticfilesystem:ClientMount",
      "elasticfilesystem:ClientWrite",
    ]
    resources = [aws_efs_file_system.var_data.arn]

    # 액세스 포인트를 경유할 때만 허용한다. 파일시스템 루트를 직접 마운트하면
    # 세 디렉터리를 한꺼번에 보게 되므로 액세스 포인트의 격리가 무의미해진다.
    condition {
      test     = "StringEquals"
      variable = "elasticfilesystem:AccessPointArn"
      values   = [for ap in aws_efs_access_point.var_data : ap.arn]
    }
  }
}

resource "aws_iam_role_policy" "task_efs" {
  name   = "efs-var-access"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task_efs.json
}

# ClientRootAccess는 **일부러 주지 않았다.** 주면 컨테이너가 uid 0으로 EFS에 쓸 수 있어
# 액세스 포인트의 posix_user 강제가 풀린다.
