# 비용 알람 — AWS Budgets
#
# **계정당 예산 2개까지 무료다.** 여기서 1개를 쓴다. 유휴 고정비를 늘리지 않으면서
# "0에 수렴한다"는 ADR-013의 전제가 실제로 지켜지는지 감시하는 유일한 수단이다.
#
# ⚠️ 예산 알림은 **사후 통보다. 지출을 막지 않는다.** AWS에는 "한도 초과 시 정지"가
# 없다. 이 알람이 하는 일은 "무언가 켜진 채로 잊혔다"를 사람에게 알리는 것뿐이고,
# 끄는 것은 사람이 한다. 진짜 방어는 상시 실행 리소스를 만들지 않는 구조 쪽이다.
#
# 임계값을 3개로 나눈 이유: 실제(ACTUAL) 80%/100%는 이미 쓴 돈이고,
# 예측(FORECASTED) 100%는 **이번 달이 끝나기 전에** 알려준다. 상시 실행을 실수로
# 켜 두면 실제 지출이 임계에 닿기 며칠 전에 예측이 먼저 튄다.

resource "aws_budgets_budget" "monthly" {
  name         = "${var.project_name}-monthly"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # 이 프로젝트의 태그가 붙은 리소스만 센다 (versions.tf의 default_tags).
  # 계정에 다른 것이 있어도 섞이지 않는다.
  cost_filter {
    name   = "TagKeyValue"
    values = ["user:Project$${var.project_name}"]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}
