#!/usr/bin/env bash
#
# Fargate 태스크 1회 실행 + **콜드 스타트 실측** (ADR-013 / session-07)
#
# 왜 스크립트인가: 콜드 스타트를 `time`으로 재면 CLI 왕복과 폴링 간격이 섞인다.
# ECS가 태스크마다 기록하는 타임스탬프(createdAt / pullStartedAt / pullStoppedAt /
# startedAt / stoppedAt)를 읽으면 **이미지 풀 시간만 따로** 떼어낼 수 있다.
# 3.46GB 이미지가 34.86s 작업 앞에 붙는다는 것이 세션 6의 미검증 항목이었다.
#
# ⚠️ 이 스크립트는 `aws` CLI를 쓴다. `.claude/settings.json`의 deny 규칙상
# **에이전트가 아니라 사람이 실행한다.** Claude Code에서는 `! bash infra/run_task.sh ...`.
#
# 사용:
#   bash infra/run_task.sh                      # 헬스체크 (태스크 정의 기본 CMD)
#   bash infra/run_task.sh index                # 인덱스 구축
#   bash infra/run_task.sh research "질의"       # 리서치 1회
#
set -euo pipefail

# ⚠️ Git Bash(MSYS)는 슬래시로 시작하는 인자를 Windows 경로로 변환한다.
# `--name /multiagent-research-lab/vllm_base`가 `C:/Program Files/Git/...`로 바뀌어
# 전달되는 것을 session-07에서 실제로 겪었다. 로그 그룹 이름(`/ecs/...`)도 같은 모양이라
# 여기서 끈다. Linux/macOS에서는 이 변수가 무시되므로 넣어도 무해하다.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

REGION="${AWS_REGION:-ap-northeast-2}"
CLUSTER="${CLUSTER:-multiagent-research-lab}"
TASKDEF="${TASKDEF:-multiagent-research-lab-orchestrator}"
LOG_GROUP="/ecs/multiagent-research-lab/orchestrator"
TF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/terraform" && pwd)"
# ⚠️ terraform.exe는 Windows 바이너리라 MSYS 경로(`/c/...`)를 해석하지 못한다
# ("Error handling -chdir option: ... cannot find the path"). Git Bash에서는 변환한다.
if command -v cygpath >/dev/null 2>&1; then
  TF_DIR="$(cygpath -m "${TF_DIR}")"
fi

# 네트워크 설정은 Terraform 출력에서 가져온다 — 서브넷/SG ID를 손으로 적지 않는다.
NETCFG="$(terraform -chdir="${TF_DIR}" output -raw network_config)"

# MSYS 경로를 Windows 바이너리에 넘길 때 변환한다. cygpath가 없으면(리눅스/맥) 그대로 둔다.
winpath() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}

# 태스크 하나의 타임스탬프를 읽어 콜드 스타트를 분해해 출력한다.
# 이미 끝난 태스크도 읽을 수 있다 — ECS가 중지된 태스크를 약 1시간 보관하므로,
# 측정에 실패했다고 태스크를 또 돌릴 필요가 없다(= 또 과금할 필요가 없다).
report_task() {
  local task_arn="$1"
  local tmpd out
  tmpd="$(mktemp -d)"
  out="${tmpd}/task.json"

  aws ecs describe-tasks --cluster "${CLUSTER}" --tasks "${task_arn}" \
    --region "${REGION}" --output json > "${out}"

  python - "$(winpath "${out}")" <<'PY'
import json, sys
from datetime import datetime

tasks = json.load(open(sys.argv[1]))["tasks"]
if not tasks:
    print("  [FAIL] 해당 태스크를 찾을 수 없다 (ECS는 중지된 태스크를 약 1시간만 보관한다)")
    sys.exit(1)
d = tasks[0]

def t(key):
    v = d.get(key)
    if not v:
        return None
    if isinstance(v, str):
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    return datetime.fromtimestamp(v)

def gap(a, b):
    ta, tb = t(a), t(b)
    return f"{(tb - ta).total_seconds():>7.2f}s" if ta and tb else "      —"

print()
print("=" * 64)
print("콜드 스타트 실측 (ECS 타임스탬프 기준)")
print("=" * 64)
print(f"  프로비저닝  createdAt     -> pullStartedAt : {gap('createdAt','pullStartedAt')}")
print(f"  이미지 풀   pullStartedAt -> pullStoppedAt : {gap('pullStartedAt','pullStoppedAt')}  <- ECR 862MB")
print(f"  기동        pullStoppedAt -> startedAt     : {gap('pullStoppedAt','startedAt')}")
print(f"  --- 콜드 스타트 합계      createdAt->startedAt : {gap('createdAt','startedAt')}")
print(f"  실행        startedAt     -> stoppedAt     : {gap('startedAt','stoppedAt')}")
print(f"  === 전체    createdAt     -> stoppedAt     : {gap('createdAt','stoppedAt')}")
print()
print(f"  lastStatus={d.get('lastStatus')}  stopCode={d.get('stopCode')}")
print(f"  stoppedReason={d.get('stoppedReason')}")
for c in d.get("containers", []):
    print(f"  container={c.get('name')} exitCode={c.get('exitCode')} reason={c.get('reason','-')}")
print(f"  cpu={d.get('cpu')} memory={d.get('memory')}")
print("=" * 64)
PY
  rm -rf "${tmpd}"
}

MODE="${1:-healthcheck}"
case "${MODE}" in
  report)
    # 이미 돈 태스크의 측정값만 다시 읽는다. 새 태스크를 만들지 않는다(과금 없음).
    TASK_REF="${2:?report 모드는 태스크 ID가 필요합니다: bash infra/run_task.sh report <task-id>}"
    echo "[INFO] 기존 태스크 리포트: ${TASK_REF}"
    report_task "${TASK_REF}"
    echo
    echo "[INFO] 로그:"
    aws logs tail "${LOG_GROUP}" --since 2h --region "${REGION}" --format short || true
    exit 0
    ;;
  healthcheck) OVERRIDES="" ;;
  index)
    OVERRIDES='{"containerOverrides":[{"name":"orchestrator","command":["python","scripts/build_index.py"]}]}'
    ;;
  research)
    QUERY="${2:?research 모드는 질의가 필요합니다: bash infra/run_task.sh research \"질의\"}"
    OVERRIDES="$(QUERY="$QUERY" python -c '
import json, os
print(json.dumps({"containerOverrides":[{"name":"orchestrator",
      "command":["python","scripts/run_research.py",os.environ["QUERY"]]}]}))')"
    ;;
  *) echo "사용: $0 {healthcheck|index|research \"질의\"|report <task-id>}" >&2; exit 2 ;;
esac

echo "[INFO] 모드=${MODE} 클러스터=${CLUSTER} 리전=${REGION}"

RUN_ARGS=(ecs run-task
  --cluster "${CLUSTER}"
  --task-definition "${TASKDEF}"
  --launch-type FARGATE
  --network-configuration "${NETCFG}"
  --count 1
  --region "${REGION}"
  --query 'tasks[0].taskArn' --output text)
[ -n "${OVERRIDES}" ] && RUN_ARGS+=(--overrides "${OVERRIDES}")

TASK_ARN="$(aws "${RUN_ARGS[@]}")"
TASK_ID="${TASK_ARN##*/}"
echo "[INFO] 태스크 시작: ${TASK_ID}"
echo "[INFO] 종료 대기 중... (Fargate는 태스크마다 이미지를 새로 받는다 — 노드 캐시가 없다."
echo "       즉 **모든 실행이 콜드 스타트다.** ECR 862MB를 매번 당겨온다.)"

# wait는 최대 10분. 그보다 오래 걸리면 report 모드로 따로 읽는다.
aws ecs wait tasks-stopped --cluster "${CLUSTER}" --tasks "${TASK_ARN}" --region "${REGION}" \
  || echo "[WARN] wait 타임아웃 — 나중에: bash infra/run_task.sh report ${TASK_ID}"

report_task "${TASK_ARN}"

echo
echo "[INFO] 로그:"
aws logs tail "${LOG_GROUP}" --since 20m --region "${REGION}" --format short || true
