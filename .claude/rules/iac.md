---
paths:
  - "infra/**"
  - "Dockerfile"
  - "docker-compose.yml"
---
# 배포/IaC 컨벤션
- **IaC 도구는 Terraform으로 고정한다. CDK를 쓰지 않는다 (ADR-014).**
  이직 시장에서 요구되는 폭이 넓어 포트폴리오 노출 가치가 크다는 판단이다.
  IaC 코드는 `infra/terraform/` 한 곳에만 둔다 — 두 도구가 공존하면 어느 쪽이
  진실인지 모호해진다.
- 실제 리소스를 만드는 명령(terraform apply, aws 등)은 반드시
  .claude/deploy-approved 파일 존재를 먼저 확인하고, 없으면 실행하지 않는다
  (`infra/terraform/apply.sh`가 이 확인을 강제한다 — 직접 `terraform apply`를 치지 말 것)
- 리소스는 최소 단위로 추가, 삭제 절차도 함께 문서화
