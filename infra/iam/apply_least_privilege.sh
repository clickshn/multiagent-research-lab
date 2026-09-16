#!/usr/bin/env bash
#
# 배포자(IAM 사용자) 권한을 관리형 정책 다발 -> 최소 권한 고객 관리형 정책 2개로 교체한다.
# 근거와 남은 위험은 `docs/adr/ADR-017-deployer-least-privilege.md`.
#
# ⚠️ 이 스크립트는 AWS CLI를 쓴다. `.claude/settings.json`의 deny 규칙상
# **에이전트가 아니라 사람이 실행한다.** Claude Code에서는
# `! bash infra/iam/apply_least_privilege.sh <서브커맨드>`.
#
# 순서가 중요하다 — **붙이고 -> 확인하고 -> 뗀다.** 반대로 하면 중간에 아무 권한도 없는
# 구간이 생기고, 그 상태에서 실패하면 되돌릴 권한조차 없다.
#
#   1) bash infra/iam/apply_least_privilege.sh show       # 지금 붙어 있는 것 기록 (롤백 근거)
#   2) bash infra/iam/apply_least_privilege.sh free-slots  # 중복 정책 2개 분리 (권한 손실 0)
#   3) bash infra/iam/apply_least_privilege.sh attach      # 새 정책 2개 생성 + 부착
#   4) cd infra/terraform && ./apply.sh plan               # 아직 되는지 확인
#   5) bash infra/iam/apply_least_privilege.sh detach      # 기존 관리형 정책 분리
#   6) cd infra/terraform && ./apply.sh plan               # 최소 권한만으로 되는지 <- 진짜 검증
#
# ⚠️ 2)가 필요한 이유: **IAM 사용자당 관리형 정책은 10개까지다.** 세션 8 시점에 이미
# 10개가 붙어 있어 3)이 LimitExceeded로 실패했다. 2)는 함께 붙어 있는 FullAccess의
# 부분집합인 중복 정책만 떼므로 **유효 권한이 줄지 않는다.**
#
# 되돌리기: `rollback <정책ARN...>` — 2)·5)가 남긴 목록 파일의 내용을 그대로 넘긴다.
#
# ⚠️ **잠금 위험.** 새 정책에는 자기 자신에게 정책을 붙이는 권한(`iam:AttachUserPolicy`)도,
# 정책 내용을 고치는 권한(`iam:CreatePolicyVersion`)도 없다. 일부러 뺐다 — 있으면
# "최소 권한"이 스스로를 관리자로 만들 수 있다는 뜻이 된다.
# 5)를 돌린 뒤 문제가 생기면 **루트 계정 콘솔**로 되돌린다. 그 경로가 살아 있는지
# (루트 로그인 + MFA 수단) 5) 전에 확인할 것.

set -euo pipefail

# Git Bash(MSYS)가 `/multiagent-research-lab/...` 같은 인자를 Windows 경로로 바꾸는 것을 막는다
# (session-07에서 실제로 겪었다 — infra/run_task.sh 주석 참조).
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

PREFIX="multiagent-research-lab-deploy"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RENDER_DIR="${HERE}/.rendered"   # .gitignore 대상 — 렌더링 결과에 계정 ID가 들어간다

winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

CALLER_ARN="$(aws sts get-caller-identity --query Arn --output text)"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

# 사용자명을 코드에 적지 않는다 — 호출자 ARN에서 뽑는다 (session-08: 계정 ID·사용자명 마스킹).
# `IAM_USER=...`로 덮어쓸 수 있다. 역할(role)로 호출하면 사용자명이 안 나오므로 그때는 필수다.
USER_NAME="${IAM_USER:-${CALLER_ARN##*/}}"
if [[ "${CALLER_ARN}" != *":user/"* ]] && [ -z "${IAM_USER:-}" ]; then
  echo "[FAIL] IAM 사용자로 호출되지 않았다 (${CALLER_ARN%%/*}/...). IAM_USER=<이름> 을 지정할 것." >&2
  exit 2
fi

# 템플릿의 ACCOUNT_ID 자리를 채운다. **렌더링 결과는 커밋 대상이 아니다** —
# 레포에 남는 것은 자리표시자 쪽이다 (session-08: 새 문서에서 계정 ID/사용자명 마스킹).
render() {
  mkdir -p "${RENDER_DIR}"
  local name
  for name in core app; do
    sed "s/ACCOUNT_ID/${ACCOUNT_ID}/g" "${HERE}/deploy-${name}.json" > "${RENDER_DIR}/deploy-${name}.json"
    echo "[INFO] 렌더링: infra/iam/.rendered/deploy-${name}.json"
  done
}

# 정책이 이미 있으면 새 버전을 기본으로 올린다.
# 정책당 버전은 5개까지라, 꽉 차기 전에 가장 오래된 비기본 버전을 지운다.
put_policy() {
  local name="$1" file="$2"
  local arn="arn:aws:iam::${ACCOUNT_ID}:policy/${name}"
  if aws iam get-policy --policy-arn "${arn}" >/dev/null 2>&1; then
    local old
    old="$(aws iam list-policy-versions --policy-arn "${arn}" \
            --query 'Versions[?!IsDefaultVersion]|[-1].VersionId' --output text)"
    if [ "${old}" != "None" ] && [ -n "${old}" ]; then
      aws iam delete-policy-version --policy-arn "${arn}" --version-id "${old}" >/dev/null 2>&1 || true
    fi
    aws iam create-policy-version --policy-arn "${arn}" \
      --policy-document "file://$(winpath "${file}")" --set-as-default >/dev/null
    echo "[INFO] 갱신: policy/${name}" >&2
  else
    aws iam create-policy --policy-name "${name}" \
      --policy-document "file://$(winpath "${file}")" >/dev/null
    echo "[INFO] 생성: policy/${name}" >&2
  fi
  printf '%s' "${arn}"
}

case "${1:-help}" in
  show)
    echo "[INFO] 사용자=${USER_NAME}  계정=***${ACCOUNT_ID: -4}"
    echo "--- 부착된 관리형 정책 ---"
    aws iam list-attached-user-policies --user-name "${USER_NAME}" \
      --query 'AttachedPolicies[].PolicyArn' --output text | tr -d '\r' | tr '\t' '\n'
    echo "--- 인라인 정책 ---"
    aws iam list-user-policies --user-name "${USER_NAME}" --output text
    ;;

  attach)
    render
    CORE_ARN="$(put_policy "${PREFIX}-core" "${RENDER_DIR}/deploy-core.json")"
    APP_ARN="$(put_policy "${PREFIX}-app" "${RENDER_DIR}/deploy-app.json")"
    for arn in "${CORE_ARN}" "${APP_ARN}"; do
      aws iam attach-user-policy --user-name "${USER_NAME}" --policy-arn "${arn}"
      echo "[OK] 부착: ${arn##*:}"
    done
    echo
    echo "[NEXT] 기존 정책이 아직 붙어 있는 상태다. 먼저 동작을 확인한다:"
    echo "       cd infra/terraform && ./apply.sh plan"
    echo "       그 다음에만 detach 를 돌린다."
    ;;

  free-slots)
    # ⚠️ IAM 사용자당 관리형 정책은 **10개까지**다. 세션 8에서 이미 10개가 붙어 있어
    # `attach`가 LimitExceeded로 실패했다 — "붙이고 나서 뗀다"가 성립하지 않는다.
    #
    # 해법: **유효 권한을 하나도 잃지 않는** 중복 정책부터 뗀다. 아래 둘은 각각
    # 함께 붙어 있는 FullAccess의 부분집합이라, 떼어도 할 수 있는 일이 줄지 않는다.
    #   - AmazonEC2ContainerRegistryPowerUser ⊂ AmazonEC2ContainerRegistryFullAccess
    #   - CloudWatchLogsReadOnlyAccess        ⊂ CloudWatchLogsFullAccess
    # 그래서 "붙이기 전에 잃는 것이 없다"는 안전 속성이 유지된다.
    REDUNDANT=(
      "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser"
      "arn:aws:iam::aws:policy/CloudWatchLogsReadOnlyAccess"
    )
    mkdir -p "${RENDER_DIR}"
    LOG="${RENDER_DIR}/detached-$(date +%Y%m%d-%H%M%S).txt"
    for arn in "${REDUNDANT[@]}"; do
      if aws iam list-attached-user-policies --user-name "${USER_NAME}" \
           --query 'AttachedPolicies[].PolicyArn' --output text | grep -qF "${arn}"; then
        aws iam detach-user-policy --user-name "${USER_NAME}" --policy-arn "${arn}"
        echo "${arn}" >> "${LOG}"
        echo "[OK] 분리(중복): ${arn##*:}"
      else
        echo "[SKIP] 붙어 있지 않다: ${arn##*:}"
      fi
    done
    # `set -e` 아래에서 `[ -f ... ] && echo` 를 그냥 쓰면 파일이 없을 때 종료된다
    # (둘 다 SKIP인 경우). if 로 쓴다.
    if [ -f "${LOG}" ]; then echo "[INFO] 롤백용 목록: ${LOG}"; fi
    echo "[NEXT] bash infra/iam/apply_least_privilege.sh attach"
    ;;

  detach)
    mkdir -p "${RENDER_DIR}"
    # ⚠️ `--output text`는 Windows에서 `\r\n`으로 끝난다. `tr '\t' '\n'`만 하면
    # **마지막 원소에만 `\r`가 남아** 그 ARN 하나가 "ARN ... is not valid"로 거부된다
    # (session-08에서 실제로 8개 중 마지막 1개만 실패했다 — 권한 문제로 오인하기 쉽다).
    mapfile -t ATTACHED < <(aws iam list-attached-user-policies --user-name "${USER_NAME}" \
                              --query 'AttachedPolicies[].PolicyArn' --output text \
                              | tr -d '\r' | tr '\t' '\n')
    KEEP_RE="policy/${PREFIX}-(core|app)$"

    # ⚠️ **IAM 권한을 주는 정책은 마지막에 뗀다.**
    # `IAMFullAccess`를 중간에 떼면 **그 시점 이후의 `iam:DetachUserPolicy`가 사라져
    # 루프가 멈춘다.** session-08에서 뒤의 3개가 성공한 것은 IAM 전파 지연 덕분이지
    # 설계가 옳아서가 아니다 — 전파가 빨랐으면 중간에 끊겼을 것이다.
    #
    # ⚠️ 이것은 **휴리스틱이다.** 어떤 관리형 정책이 `iam:DetachUserPolicy`를 주는지
    # 확실히 알려면 정책 문서를 읽어야 하는데, 최소 권한 정책에는 `iam:GetPolicyVersion`이
    # 없다(일부러 뺐다). 이름으로 거른다 — 새로운 이름 패턴은 여기에 추가해야 한다.
    IAM_GRANTING_RE="(IAM|Admin|ActionsWithAWSResourceControl)"
    NORMAL=() LAST=()
    for arn in "${ATTACHED[@]}"; do
      [ -z "${arn}" ] && continue
      [[ "${arn}" =~ ${KEEP_RE} ]] && continue
      if [[ "${arn##*/}" =~ ${IAM_GRANTING_RE} ]]; then LAST+=("${arn}"); else NORMAL+=("${arn}"); fi
    done
    TO_DETACH=("${NORMAL[@]}" "${LAST[@]}")
    if [ ${#TO_DETACH[@]} -eq 0 ]; then echo "[INFO] 뗄 것이 없다."; exit 0; fi
    echo "[INFO] 분리 대상 (IAM 권한을 주는 것은 마지막으로 미룬다):"
    for arn in "${NORMAL[@]}"; do [ -n "${arn}" ] && echo "  ${arn}"; done
    for arn in "${LAST[@]}"; do [ -n "${arn}" ] && echo "  ${arn}   <- 마지막 (IAM 권한)"; done
    LOG="${RENDER_DIR}/detached-$(date +%Y%m%d-%H%M%S).txt"
    printf '%s\n' "${TO_DETACH[@]}" > "${LOG}"
    echo "[INFO] 롤백용 목록: ${LOG}"
    echo "⚠️  분리 후에는 이 사용자가 스스로 정책을 다시 붙일 수도, 고칠 수도 없다"
    echo "    (루트 계정 콘솔 필요). 루트 로그인 수단이 살아 있는지 먼저 확인할 것."
    # 비대화형 셸(Claude Code의 `!` 실행 등)에서는 stdin이 없어 read 가 EOF 로 끝난다.
    # 그때는 CONFIRM=yes 를 명시적으로 넘겨야 진행한다 — 기본값을 통과로 두지 않는다.
    if [ "${CONFIRM:-}" = "yes" ]; then
      ans=yes
    elif [ -t 0 ]; then
      read -r -p "계속하려면 yes 를 입력: " ans
    else
      echo "[FAIL] 대화형 입력이 불가능하다. 의도한 것이면 CONFIRM=yes 를 붙여 다시 실행할 것." >&2
      exit 1
    fi
    [ "${ans}" = "yes" ] || { echo "중단."; exit 1; }
    for arn in "${TO_DETACH[@]}"; do
      aws iam detach-user-policy --user-name "${USER_NAME}" --policy-arn "${arn}"
      echo "[OK] 분리: ${arn##*:}"
    done
    echo
    echo "[NEXT] 진짜 검증: cd infra/terraform && ./apply.sh plan"
    echo "       'No changes' 가 나오면 최소 권한만으로 도는 것이다."
    ;;

  rollback)
    shift
    [ $# -gt 0 ] || { echo "사용: rollback <정책ARN...>" >&2; exit 2; }
    for arn in "$@"; do
      aws iam attach-user-policy --user-name "${USER_NAME}" --policy-arn "${arn}"
      echo "[OK] 재부착: ${arn##*:}"
    done
    ;;

  *)
    sed -n "2,32p" "${BASH_SOURCE[0]}"
    ;;
esac
