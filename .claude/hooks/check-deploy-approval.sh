#!/bin/bash
# PreToolUse(Bash) — 실제 클라우드 리소스를 만드는 명령 앞에 승인 게이트를 세운다.
#
# 이 훅은 `infra/terraform/apply.sh`의 게이트와 **중복이며 의도된 중복이다.**
# apply.sh는 그 스크립트를 경유할 때만 동작한다. 훅은 도구 계층이라
# 스크립트를 우회하고 직접 명령을 쳐도 걸린다 (docs/governance.md "승인 게이트").
#
# 종료 코드 2 = 도구 호출 차단 + stderr를 모델에게 전달.
#
# ⚠️ 이 훅은 **모든 Bash 호출마다** 돈다. 여기서 실패하면 Bash 전체가 막힌다.
# 그래서 JSON 파서를 두 개 둔다: jq → python3/python 순. jq는 이 머신에 없었고
# (session-07에서 확인), python은 이 프로젝트의 하드 의존성이라 항상 있다.
# **둘 다 없으면 통과가 아니라 차단이다** — 게이트가 사라지는 것이 기본값이면 안 된다.
set -uo pipefail

input=$(cat)

extract_command() {
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$input" | jq -r '.tool_input.command // empty'
    return 0
  fi
  for py in python3 python py; do
    if command -v "$py" >/dev/null 2>&1; then
      printf '%s' "$input" | "$py" -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command","") or "")'
      return 0
    fi
  done
  return 1
}

if ! command=$(extract_command); then
  echo "check-deploy-approval.sh: jq도 python도 없어 명령을 검사하지 못했습니다. 게이트를 열지 않습니다." >&2
  exit 2
fi

[ -z "$command" ] && exit 0

# 매칭 대상 세 가지.
#
#   1. `terraform apply` / `terraform destroy` — 직접 실행(= `.claude/rules/iac.md`가 금지한 경로)
#   2. `apply.sh apply` / `apply.sh destroy`   — **정상 경로**. apply.sh가 자체 게이트를
#      갖고 있지만 여기서도 잡는다. 도구 계층이 "정상 경로만 못 보는" 상태가 되면
#      게이트의 적용 범위를 사람이 머릿속으로 따라가야 한다.
#   3. `aws` CLI 호출 — 단어 경계로 잡는다. 명령 첫 토큰이거나 ; & | ( 또는 공백 뒤일 때만이라
#      `pip install awscli-local` 같은 문자열 포함은 걸리지 않는다.
if echo "$command" | grep -qE 'terraform[[:space:]]+(apply|destroy)|apply\.sh[[:space:]]+(apply|destroy)|(^|[;&|(][[:space:]]*|[[:space:]])aws[[:space:]]'; then
  # CLAUDE_PROJECT_DIR이 없으면 cwd 기준. 훅은 레포 루트에서 도는 것이 전제다.
  approval="${CLAUDE_PROJECT_DIR:-.}/.claude/deploy-approved"
  if [ ! -f "$approval" ]; then
    cat >&2 <<'MSG'
배포 승인 파일(.claude/deploy-approved)이 없습니다. 먼저 비용/리소스 내역을
사용자에게 보여주고 명시적 승인을 받은 뒤에만 진행하세요.

- 생성할 리소스 종류와 개수, 예상 시간당/월 비용을 먼저 제시할 것
- 사용자가 승인하면 그때 승인 파일을 만들고 진행할 것
- 배포 확인이 끝나면 승인 파일을 삭제해 다음 배포에 다시 승인을 받을 것
MSG
    exit 2
  fi
fi

exit 0
