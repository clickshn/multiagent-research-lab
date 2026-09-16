output "ecr_repository_url" {
  description = "이미지를 푸시할 주소. `docker tag` / `docker push`에 쓴다."
  value       = aws_ecr_repository.orchestrator.repository_url
}

output "ecr_repository_arn" {
  description = "ECS 태스크 실행 역할에 pull 권한을 줄 때 쓴다 (세션 7)."
  value       = aws_ecr_repository.orchestrator.arn
}

output "log_group_name" {
  description = "ECS 태스크 정의의 awslogs 드라이버가 가리킬 로그 그룹 (세션 7)."
  value       = aws_cloudwatch_log_group.orchestrator.name
}

output "push_commands" {
  description = <<-EOT
    이미지 푸시 절차. 리전·계정 ID를 직접 적지 않고 출력에서 가져다 쓰기 위한 것이다.
    ⚠️ 이미지를 푸시하는 것은 apply와 별개의 동작이며, 푸시 자체는 승인 게이트 대상이
    아니다(기존 리포지토리에 쓰는 것이므로). 다만 **푸시 전에 이미지에 시크릿이 없는지
    확인한다** — 검사 절차는 ADR-010 Evidence에 있다.
  EOT
  value = <<-EOT
    aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${split("/", aws_ecr_repository.orchestrator.repository_url)[0]}
    docker tag multiagent-research-lab/orchestrator:dev ${aws_ecr_repository.orchestrator.repository_url}:dev
    docker push ${aws_ecr_repository.orchestrator.repository_url}:dev
  EOT
}
