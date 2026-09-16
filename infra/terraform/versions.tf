# 버전 고정 — requirements.txt와 같은 이유다 (재현성).
# 프로바이더가 조용히 올라가면 같은 코드가 다른 리소스를 만든다.
terraform {
  required_version = "~> 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  # 백엔드는 로컬(기본)이다. 원격 상태(S3 + DynamoDB 잠금)는 **그 자체가 새 유료 리소스**라
  # 사전 승인 대상이고(docs/governance.md "승인 게이트"), 지금은 1인 작업이라 잠금이 필요 없다.
  #
  # ⚠️ 그래서 `terraform.tfstate`가 로컬 파일로 남는다. 상태 파일에는 리소스 ARN 등이
  # 들어가므로 커밋하지 않는다 (.gitignore 참조). 협업이 시작되면 원격 백엔드가 먼저다.
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "multiagent-research-lab"
      ManagedBy = "terraform"
      # 이 태그로 비용 할당 보고서에서 이 프로젝트만 뽑아낼 수 있다.
      # 안 쓸 때 고정비가 0에 수렴하는지 확인하는 수단이다 (ADR-013).
      CostCenter = "personal-portfolio"
    }
  }
}
