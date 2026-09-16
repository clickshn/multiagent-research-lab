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
  value       = <<-EOT
    aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${split("/", aws_ecr_repository.orchestrator.repository_url)[0]}
    docker tag multiagent-research-lab/orchestrator:dev ${aws_ecr_repository.orchestrator.repository_url}:dev
    docker push ${aws_ecr_repository.orchestrator.repository_url}:dev
  EOT
}

# ---------------------------------------------------------------------------
# session-07 추가 — 실행 / EFS
# ---------------------------------------------------------------------------

output "cluster_name" {
  description = "ECS 클러스터 이름. run-task에 쓴다."
  value       = aws_ecs_cluster.main.name
}

output "task_definition_arn" {
  description = "태스크 정의 ARN(리비전 포함). 특정 리비전을 재현 실행할 때 쓴다."
  value       = aws_ecs_task_definition.orchestrator.arn
}

output "efs_file_system_id" {
  description = "`var/`(traces/llm_cache/chroma)가 사는 EFS. 삭제 절차는 README 참조."
  value       = aws_efs_file_system.var_data.id
}

output "efs_access_point_ids" {
  description = "액세스 포인트 3개. compose의 named volume 3개와 1:1 (ADR-015)."
  value       = { for k, ap in aws_efs_access_point.var_data : k => ap.id }
}

output "network_config" {
  description = "run-task의 --network-configuration에 그대로 넣는 값."
  value = format(
    "awsvpcConfiguration={subnets=[%s],securityGroups=[%s],assignPublicIp=ENABLED}",
    join(",", aws_subnet.public[*].id),
    aws_security_group.task.id,
  )
}

output "run_commands" {
  description = <<-EOT
    태스크 실행 절차. **서비스가 아니라 단발 실행이다** — 한 번 돌고 끝난다(ADR-013).
    상시 실행 리소스가 없으므로 이 명령을 치지 않는 동안 컴퓨트 비용은 0이다.
  EOT
  value       = <<-EOT
    CLUSTER=${aws_ecs_cluster.main.name}
    TASKDEF=${aws_ecs_task_definition.orchestrator.family}
    NETCFG='${format("awsvpcConfiguration={subnets=[%s],securityGroups=[%s],assignPublicIp=ENABLED}", join(",", aws_subnet.public[*].id), aws_security_group.task.id)}'
    REGION=${var.aws_region}

    # 1) 헬스체크 (태스크 정의 기본 CMD)
    #    ecs run-task --cluster "$CLUSTER" --task-definition "$TASKDEF" \
    #      --launch-type FARGATE --network-configuration "$NETCFG" --region "$REGION"

    # 2) 인덱스 구축 — 결과는 EFS의 chroma에 남고 태스크가 끝나도 유지된다
    #    위 명령에 다음을 추가:
    #      --overrides '{"containerOverrides":[{"name":"orchestrator","command":["python","scripts/build_index.py"]}]}'

    # 3) 리서치 1회
    #      --overrides '{"containerOverrides":[{"name":"orchestrator","command":["python","scripts/run_research.py","질의"]}]}'

    # 로그 확인: logs tail ${aws_cloudwatch_log_group.orchestrator.name} --follow --region "$REGION"
    # (명령 앞에 `aws`를 붙인다 — 이 출력에 그대로 적으면 배포 승인 훅이 문서까지 걸고 넘어진다)
  EOT
}
