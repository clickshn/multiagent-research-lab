#!/usr/bin/env bash
#
# terraform 상태 변경 게이트 (`.claude/rules/iac.md`, `docs/governance.md` "승인 게이트")
#
# 규칙은 두 가지이고 **둘 다** 필요하다.
#
#   1. 사용자 승인 — 사람이 판단한다. 이 스크립트가 대신할 수 없다.
#   2. `.claude/deploy-approved` 파일 존재 — 기계가 확인한다. 이 스크립트가 강제한다.
#
# 파일이 없으면 실행하지 않는다. 파일이 있다고 해서 1번이 면제되는 것도 아니다 —
# 이 파일은 "승인이 있었다"의 **기록**이지 승인 그 자체가 아니다.
#
# 사용:
#   ./apply.sh plan       # 게이트 없이 볼 수 있다 (상태를 바꾸지 않는다)
#   ./apply.sh apply      # 게이트 통과 필요
#   ./apply.sh destroy    # 게이트 통과 필요
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APPROVAL_FILE="${REPO_ROOT}/.claude/deploy-approved"

ACTION="${1:-plan}"
shift || true

# plan / validate / fmt / init 은 상태를 바꾸지 않으므로 게이트가 없다.
# 게이트를 읽기 동작까지 걸면 사람들이 게이트를 우회하는 습관을 들인다.
case "${ACTION}" in
  init|validate|fmt|plan|show|output)
    echo "[INFO] '${ACTION}'은(는) 상태를 바꾸지 않습니다 — 게이트 없이 진행합니다."
    exec terraform -chdir="${SCRIPT_DIR}" "${ACTION}" "$@"
    ;;
  apply|destroy)
    : # 아래에서 게이트 확인
    ;;
  *)
    echo "[FAIL] 알 수 없는 동작: '${ACTION}'" >&2
    echo "       사용: $0 {init|validate|fmt|plan|show|output|apply|destroy}" >&2
    exit 2
    ;;
esac

if [[ ! -f "${APPROVAL_FILE}" ]]; then
  cat >&2 <<EOF
[BLOCKED] '${ACTION}'을(를) 실행하지 않았습니다.

  승인 파일이 없습니다: ${APPROVAL_FILE}

  이 동작은 **새 유료 리소스를 만들거나 되돌리기 어려운 외부 상태를 바꿉니다.**
  docs/governance.md "승인 게이트"와 .claude/rules/iac.md에 따라 두 가지가 모두 필요합니다.

    1. 사용자의 명시적 승인 (사람)
    2. 승인 파일 존재 (기계)

  승인을 받은 뒤에 파일을 만드세요:

      touch "${APPROVAL_FILE}"

  이 파일은 .gitignore 대상이라 커밋되지 않습니다 — 승인은 이 머신에 한정됩니다.
EOF
  exit 3
fi

echo "[OK] 승인 파일 확인: ${APPROVAL_FILE}"
echo "[INFO] terraform ${ACTION} 실행 (chdir=${SCRIPT_DIR})"

# apply/destroy는 대화형 확인을 **끄지 않는다.** `-auto-approve`를 기본으로 두면
# 게이트가 하나 줄어드는 셈이라, 승인 파일을 둔 의미가 약해진다.
exec terraform -chdir="${SCRIPT_DIR}" "${ACTION}" "$@"
