# EFS — `var/` (traces / llm_cache / chroma)의 클라우드 대응물 (ADR-015)
#
# ---------------------------------------------------------------------------
# 왜 EFS인가 (S3가 아니라)
# ---------------------------------------------------------------------------
# 기존 코드가 **POSIX 파일시스템 호출로** 읽고 쓴다 — `Tracer`는 JSONL에 append하고,
# 캐시는 파일 경로로 조회하며, Chroma는 로컬 디렉터리를 SQLite+파일로 연다.
# EFS는 마운트하면 그대로 돈다. S3는 객체 스토리지라 append도 seek도 안 되므로
# **세 모듈을 전부 고쳐야 한다** — 배포 때문에 검증된 코드를 바꾸게 된다.
#
# 이것이 판단 기준이다: 이번 배포의 목적은 "돌아가는 것을 올리는 것"이지
# "올리려고 고쳐 쓰는 것"이 아니다. 상세는 ADR-015.
#
# ---------------------------------------------------------------------------
# 비용
# ---------------------------------------------------------------------------
# Standard 스토리지 $0.33/GB-월 (ap-northeast-2, list). **최소 요금이 없다.**
# 현재 데이터는 트레이스 JSONL + 캐시 JSON + Chroma 인덱스로 100MB 미만 —
# 월 $0.04 수준이다. 마운트 타깃·액세스 포인트는 무료.
# throughput_mode를 bursting으로 두는 이유도 비용이다 — elastic은 읽고 쓴 GB마다
# 과금되고, bursting은 처리량 요금이 없다. 우리 워크로드는 초당 수십 KB다.

resource "aws_efs_file_system" "var_data" {
  creation_token = "${var.project_name}-var"

  # 저장 시 암호화. AWS 관리 키(aws/elasticfilesystem)라 KMS 고정비가 없다.
  # 고객 관리 키는 월 $1/키인데 여기 들어가는 것이 공개 arXiv 초록이라(ADR-004)
  # 값을 하지 않는다. **ADR-004 전제가 깨지면 이 판단도 다시 본다.**
  encrypted = true

  # 처리량 요금 0. 위 주석 참조.
  throughput_mode = "bursting"

  lifecycle_policy {
    # 30일 안 읽힌 파일은 IA로 내린다 ($0.33 → $0.016/GB-월).
    # 지난 세션 트레이스가 그대로 쌓이는 경로라 유효하다.
    transition_to_ia = "AFTER_30_DAYS"
  }

  lifecycle_policy {
    # 다시 읽히면 Standard로 올린다 — IA 읽기 요금이 반복 조회에 붙지 않게.
    transition_to_primary_storage_class = "AFTER_1_ACCESS"
  }

  tags = { Name = "${var.project_name}-var" }
}

# 마운트 타깃 — AZ마다 하나. 무료.
resource "aws_efs_mount_target" "var_data" {
  count = length(aws_subnet.public)

  file_system_id  = aws_efs_file_system.var_data.id
  subnet_id       = aws_subnet.public[count.index].id
  security_groups = [aws_security_group.efs.id]
}

# ---------------------------------------------------------------------------
# 파일시스템 정책 — **"접근 주체는 IAM 역할"을 강제하는 곳이 여기다**
# ---------------------------------------------------------------------------
# governance.md의 `var/` 정책을 클라우드 기준으로 다시 쓴 근거가 이 리소스다.
# 로컬에서는 "그 머신에 로그인할 수 있는 주체"가 접근 주체였다. 여기서는
# **이 정책이 허용한 IAM 주체만**이다. 기본 거부를 켜서 명시되지 않은 주체를 막는다.
resource "aws_efs_file_system_policy" "var_data" {
  file_system_id = aws_efs_file_system.var_data.id

  # 명시적으로 허용된 주체 외에는 거부. 이걸 켜지 않으면 계정 내 누구나 마운트할 수 있다.
  bypass_policy_lockout_safety_check = false

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowOrchestratorTaskRoleOverTLS"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.task.arn
        }
        Action = [
          "elasticfilesystem:ClientMount",
          "elasticfilesystem:ClientWrite",
        ]
        Resource = aws_efs_file_system.var_data.arn
        Condition = {
          Bool = {
            # 전송 중 암호화를 강제한다. 태스크 정의의 transit_encryption과 짝이다.
            "elasticfilesystem:AccessedViaMountTarget" = "true"
          }
        }
      },
      {
        Sid       = "DenyUnencryptedTransport"
        Effect    = "Deny"
        Principal = { AWS = "*" }
        Action    = "*"
        Resource  = aws_efs_file_system.var_data.arn
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# 액세스 포인트 3개 — docker-compose의 named volume 3개와 1:1
# ---------------------------------------------------------------------------
# 하나의 EFS 안에 디렉터리를 나누고, 각 액세스 포인트가 자기 디렉터리에 갇힌다
# (chroot와 같다). traces를 마운트한 프로세스가 llm_cache를 볼 수 없다.
#
# **POSIX 소유권을 여기서 강제하는 것이 핵심이다.** 컨테이너는 uid 10001(app)로 돈다
# (Dockerfile). 액세스 포인트가 uid/gid를 덮어쓰므로 non-root가 그대로 쓸 수 있고,
# 로컬 compose에서 볼륨 소유권을 맞춰 준 것과 같은 상태가 된다.
locals {
  efs_access_points = {
    traces    = "/traces"
    llm_cache = "/llm_cache"
    chroma    = "/chroma"
  }
  container_uid = 10001
  container_gid = 10001
}

resource "aws_efs_access_point" "var_data" {
  for_each = local.efs_access_points

  file_system_id = aws_efs_file_system.var_data.id

  posix_user {
    uid = local.container_uid
    gid = local.container_gid
  }

  root_directory {
    path = each.value

    # 디렉터리가 없으면 이 소유권/권한으로 만든다. 없으면 마운트가 실패한다
    # (EFS는 루트 디렉터리를 자동 생성하지 않는다).
    creation_info {
      owner_uid   = local.container_uid
      owner_gid   = local.container_gid
      permissions = "0750"
    }
  }

  tags = { Name = "${var.project_name}-${each.key}" }
}
