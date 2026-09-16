# 네트워크 — VPC / 퍼블릭 서브넷 2개 / IGW / 보안그룹
#
# **여기 있는 리소스는 전부 유휴 고정비 $0이다.** VPC·서브넷·IGW·라우트테이블·
# 보안그룹은 존재 자체로 과금되지 않는다. ADR-013의 "유휴 시 0에 수렴" 전제를 깨지 않는다.
#
# ---------------------------------------------------------------------------
# 왜 퍼블릭 서브넷 + 퍼블릭 IP인가 (NAT를 쓰지 않는 이유)
# ---------------------------------------------------------------------------
# 태스크는 **밖으로 나가야 한다** — 추론 엔드포인트가 AWS 밖(KT Cloud, 한국)에 있다
# (ADR-003). 아웃바운드 경로는 셋 중 하나다.
#
#   1. 퍼블릭 서브넷 + 퍼블릭 IP        유휴 $0        ← 채택
#   2. 프라이빗 서브넷 + NAT Gateway    유휴 ~$32/월   (0.045/h × 730 + 데이터 처리)
#   3. 프라이빗 서브넷 + VPC 엔드포인트  유휴 ~$22/월   (인터페이스 3개 × ~$7.2)
#                                        + **그래도 KT Cloud로는 못 나간다** → NAT 추가 필요
#
# 2번·3번은 유휴 고정비가 실행 비용(회당 ~$0.019)의 1,000배가 된다. ADR-013이
# EKS를 뺀 것과 같은 이유로 여기서도 뺀다.
#
# ⚠️ 트레이드오프: 태스크가 퍼블릭 IP를 갖는다. 다만 **인바운드 규칙이 0개**이고
# ECS 태스크는 리스너를 열지 않는다(작업 컨테이너다 — ADR-010). 도달 가능한 포트가
# 없으므로 노출면은 "나가는 연결만 하는 호스트"에 해당한다.
#
# ⚠️ 퍼블릭 IPv4는 2024-02부터 시간당 $0.005가 과금된다. **태스크가 도는 동안만**이라
# 10분 실행 기준 $0.0008이다. 상시 실행으로 바꾸면 월 ~$3.65가 되므로 이 값은
# "상시로 켤 것인가" 결정(ADR-016)에 딸려 움직인다.

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "main" {
  cidr_block = var.vpc_cidr

  # EFS 마운트 타깃을 DNS 이름으로 붙이려면 둘 다 필요하다.
  # (fs-xxxx.efs.<region>.amazonaws.com 해석)
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.project_name}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project_name}-igw" }
}

# AZ 2개 — EFS 마운트 타깃은 AZ마다 하나씩 필요하고, 마운트 타깃은 무료다.
# 한 AZ가 죽어도 태스크를 띄울 곳이 남는다.
resource "aws_subnet" "public" {
  count = 2

  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${var.project_name}-public-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${var.project_name}-public" }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ---------------------------------------------------------------------------
# 보안그룹 2개 — 태스크용 / EFS용
# ---------------------------------------------------------------------------
# 분리하는 이유: EFS 규칙을 "태스크 SG에서 오는 2049 포트만"으로 쓸 수 있다.
# CIDR로 쓰면 VPC 안의 아무 리소스나 마운트할 수 있게 된다.

resource "aws_security_group" "task" {
  name = "${var.project_name}-task"
  # 오케스트레이터 Fargate 태스크. 인바운드 없음, 아웃바운드만.
  # (AWS 보안그룹 description은 ASCII만 허용한다 — 한국어는 주석으로 둔다.)
  description = "Orchestrator Fargate task: no inbound, egress only"
  vpc_id      = aws_vpc.main.id

  # 인바운드 규칙 없음 — 태스크는 포트를 열지 않는다 (작업 컨테이너, ADR-010).

  egress {
    # 전체 아웃바운드 — ECR pull, CloudWatch Logs, KT Cloud 추론 엔드포인트
    description = "All egress: ECR pull, CloudWatch Logs, external inference endpoint"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-task" }
}

resource "aws_security_group" "efs" {
  name = "${var.project_name}-efs"
  # EFS 마운트 타깃. 태스크 SG에서 오는 NFS만 받는다.
  description = "EFS mount targets: NFS from orchestrator task SG only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "NFS from orchestrator task only"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.task.id]
  }

  tags = { Name = "${var.project_name}-efs" }
}
