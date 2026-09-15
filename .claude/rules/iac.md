---
paths:
  - "infra/**"
  - "Dockerfile"
  - "docker-compose.yml"
---
# 배포/IaC 컨벤션
- 실제 리소스를 만드는 명령(terraform apply, aws 등)은 반드시
  .claude/deploy-approved 파일 존재를 먼저 확인하고, 없으면 실행하지 않는다
- 리소스는 최소 단위로 추가, 삭제 절차도 함께 문서화
