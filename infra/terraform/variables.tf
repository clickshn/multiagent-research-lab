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
