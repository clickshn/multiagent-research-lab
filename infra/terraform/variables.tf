variable "aws_region" {
  description = <<-EOT
    배포 리전. 추론 엔드포인트가 AWS 밖(KT Cloud, 한국)에 있으므로 리전이 멀면
    매 LLM 호출의 왕복 지연이 늘어난다 — 이 파이프라인은 1회에 10~13콜을 돌린다(ADR-010 실측).
    그래서 기본값을 서울로 둔다. 비용은 us-east-1이 약간 싸지만 지연이 더 중요하다.
  EOT
  type        = string
  default     = "ap-northeast-2"
}

variable "project_name" {
  description = "리소스 이름 접두사. ECR 리포지토리·로그 그룹 이름에 쓰인다."
  type        = string
  default     = "multiagent-research-lab"
}

variable "log_retention_days" {
  description = <<-EOT
    CloudWatch 로그 보존 기간(일).

    **주의: 이것은 `var/traces/`의 감사 로그가 아니다.** 컨테이너 stdout/stderr일 뿐이다.
    감사 로그(ADR-007)를 클라우드에서 어디에 영속화할지는 아직 정해지지 않았고
    (ADR-013 Risks), 정해지기 전에는 이 값을 감사 로그 보존 기간으로 읽으면 안 된다.

    14일은 "디버깅에는 충분하고 비용은 거의 안 드는" 값이다. 무기한(0)으로 두면
    쌓이는 만큼 계속 과금된다.
  EOT
  type        = number
  default     = 14
}

variable "ecr_untagged_expire_days" {
  description = <<-EOT
    태그 없는 ECR 이미지를 며칠 뒤 지울지.

    이미지가 3.46GB라(ADR-010) 쌓이면 저장 비용이 선형으로 는다. ECR 저장은
    $0.10/GB-월이므로 이미지 10개면 월 $3.5 — 유휴 고정비를 0에 수렴시키겠다는
    ADR-013의 전제를 깨는 유일한 항목이다. 수명주기 정책이 필수인 이유다.
  EOT
  type        = number
  default     = 7
}

variable "ecr_keep_last_images" {
  description = "태그된 이미지를 몇 개까지 보관할지. 롤백 여지를 남기되 무한히 쌓지 않는다."
  type        = number
  default     = 5
}

# ---------------------------------------------------------------------------
# session-07 추가 — 네트워크 / 태스크 / EFS / 예산
# ---------------------------------------------------------------------------

variable "vpc_cidr" {
  description = <<-EOT
    VPC CIDR. /16에서 /24 두 개를 잘라 쓴다(퍼블릭 서브넷 2개).
    기존 네트워크와 겹치지 않게만 하면 되고, 이 프로젝트는 VPC 피어링을 쓰지 않는다.
  EOT
  type        = string
  default     = "10.20.0.0/16"
}

variable "task_cpu" {
  description = <<-EOT
    Fargate vCPU 단위(1024 = 1 vCPU). ADR-013의 비용 추정이 2 vCPU 기준이다.

    임베딩 모델을 CPU로 돌리므로(EMBEDDING_DEVICE=cpu) 코어 수가 인덱싱 시간에
    직접 영향을 준다 — 로컬 컨테이너 인덱싱 10.5s는 호스트 CPU 기준이라
    **Fargate에서 같은 값이 나오지 않는다.**
  EOT
  type        = number
  default     = 2048
}

variable "task_memory" {
  description = <<-EOT
    Fargate 메모리(MiB). 2 vCPU는 4096~16384만 허용된다.
    torch CPU + e5-small 가중치 470MB가 올라가므로 4096으로 시작한다.
  EOT
  type        = number
  default     = 4096
}

variable "image_tag" {
  description = "실행할 ECR 이미지 태그. compose가 만드는 로컬 태그와 같은 `dev`를 기본값으로 둔다."
  type        = string
  default     = "dev"
}

variable "vllm_model" {
  description = <<-EOT
    엔드포인트가 서빙하는 모델 이름. **시크릿이 아니다** — 시크릿인 것은 URL뿐이다
    (governance "시크릿 취급"). 모델 고정 원칙(ADR-002)상 이 값은 잘 바뀌지 않는다.
  EOT
  type        = string
  default     = "gemma-4-31B-it"
}

variable "vllm_base_param_name" {
  description = <<-EOT
    `VLLM_BASE`를 담을 SSM Parameter Store 파라미터 **이름**(값이 아니다).

    ⚠️ 값은 Terraform이 만들지도 읽지도 않는다 — 상태 파일에 평문으로 남기 때문이다
    (ecs.tf 주석 참조). apply 전에 `aws ssm put-parameter`로 한 번 넣는다.
    SecureString + 기본 KMS 키(aws/ssm)는 Standard 티어에서 **무료**다.
  EOT
  type        = string
  default     = "/multiagent-research-lab/vllm_base"
}

variable "monthly_budget_usd" {
  description = <<-EOT
    월 예산 한도(USD). 임계 80%/100%에서 메일이 온다.

    기본값 5는 "정상 운영의 2배쯤"에서 울리도록 잡은 값이다 — 유휴 ~$0.40,
    월 100회 실행 ~$2.3(ADR-013 갱신치). 상시 실행을 실수로 켜면 월 $83 규모가 되므로
    **며칠 안에 예측 임계가 먼저 튄다.** 그게 이 값의 목적이다.
  EOT
  type        = string
  default     = "5"
}

variable "budget_alert_email" {
  description = <<-EOT
    예산 알림을 받을 메일 주소. **기본값을 두지 않는다.**

    기본값을 두면 값을 안 넣어도 apply가 되고, 그러면 "알람이 조용히 안 만들어진
    상태"가 기본값이 된다. 비용 감시는 그런 식으로 빠지면 안 되는 종류의 장치다.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.budget_alert_email))
    error_message = "budget_alert_email에 실제 메일 주소를 넣어야 한다 (terraform.tfvars 또는 -var)."
  }
}
