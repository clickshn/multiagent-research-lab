#!/bin/bash
# PreToolUse(Bash|PowerShell) — 외부 LLM 벤더 호출을 도구 계층에서 차단한다 (ADR-021).
#
# 왜 있는가. Session 0.5a에서 계약 검증용 추출 30건이 외부 Anthropic API로 나갔다.
# 승인 게이트는 목적·모델명·건수·예상 비용을 물었고 전부 정확히 답변됐지만,
# **"대상 엔드포인트가 VLLM_BASE인가"는 아무도 묻지 않았다.** 게이트가 있었고
# 통과했는데, 게이트가 확인해야 할 것을 확인하지 않았다.
# 그래서 규칙을 문서가 아니라 도구 계층에 둔다 (docs/governance.md "승인 게이트").
#
# 종료 코드 2 = 도구 호출 차단 + stderr를 모델에게 전달.
#
# ⚠️ 이 훅은 **모든 Bash/PowerShell 호출마다** 돈다. check-deploy-approval.sh와
# 같은 이유로 파서 폴백을 두고, **파서가 하나도 없으면 통과가 아니라 차단**이다.
#
# ⚠️ **이 훅이 막지 못하는 것 — 알고 쓴다.** 상세는 `.claude/rules/external-llm.md`.
#   - 명령줄에 벤더 흔적이 없는 스크립트 실행 (`python some_script.py`)
#   - 에이전트 자신의 모델 호출(하네스 계층) · Agent/Task 서브에이전트
#   - MCP 도구를 경유하는 네트워크 호출
#   훅은 **명령 문자열만** 본다. 이것은 결함이 아니라 적용 범위이고,
#   코드 계층(`src/providers/egress.py`)이 나머지 절반을 맡는다.
set -uo pipefail

input=$(cat)

extract_command() {
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$input" | jq -r '.tool_input.command // empty'
    return 0
  fi
  # 순서가 `python` 먼저인 것은 취향이 아니라 측정 결과다. 이 머신에서 `python3`은
  # pyenv-win shim이라 호출당 약 1.9s가 들고 `python`은 약 0.2s다. 훅은 **모든**
  # Bash/PowerShell 호출마다 도므로 이 차이가 세션 전체에 곱해진다.
  #
  # `command -v`로 **있는지**만 보지 않고 **실제로 도는지**까지 본다. pyenv-win shim은
  # PATH에 잡히지만 bash에서 실행하면 "cannot execute"로 죽는다 — 있는 것과 도는 것이
  # 다르다. 여기서 그걸 구분하지 않으면 훅이 모든 호출을 차단해 Bash 전체가 막힌다.
  local out
  for py in python python3 py; do
    command -v "$py" >/dev/null 2>&1 || continue
    if out=$(printf '%s' "$input" | "$py" -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command","") or "")' 2>/dev/null); then
      printf '%s' "$out"
      return 0
    fi
  done
  return 1
}

if ! command=$(extract_command); then
  echo "check-external-llm.sh: jq도 python도 없어 명령을 검사하지 못했습니다. 게이트를 열지 않습니다." >&2
  exit 2
fi

[ -z "$command" ] && exit 0

# --- 매칭 대상 -------------------------------------------------------------
#
# (A) 벤더 호스트. `src/providers/egress.py`의 EXTERNAL_LLM_HOSTS를 **복제**한 것이다.
#     참조가 아니라 복제인 이유는 이 훅이 Python 임포트 없이 도는 셸 스크립트이기
#     때문이고, 두 목록이 갈라지는 것은 tests/test_external_llm_gate.py가 막는다.
VENDOR_HOSTS='api\.anthropic\.com|api\.openai\.com|openai\.azure\.com|generativelanguage\.googleapis\.com|aiplatform\.googleapis\.com|api\.mistral\.ai|api\.cohere\.(ai|com)|api\.groq\.com|api\.together\.xyz|api\.perplexity\.ai|api\.deepseek\.com|api\.x\.ai|openrouter\.ai|api\.upstage\.ai|api\.moonshot\.cn|open\.bigmodel\.cn|api\.z\.ai|dashscope\.aliyuncs\.com|clovastudio\.(stream|apigw)\.ntruss\.com|bedrock-runtime\.|bedrock-agent-runtime\.'

# (B) 벤더 SDK 임포트 — 인라인 실행(`python -c`, `node -e`)을 잡는다.
VENDOR_SDK='(^|[^[:alnum:]_])(import[[:space:]]+(anthropic|openai|cohere|mistralai|groq|google\.generativeai)|from[[:space:]]+(anthropic|openai|cohere|mistralai|groq|google)[[:space:].]|require\(.(openai|anthropic)|@anthropic-ai/|langchain_(openai|anthropic|google_genai))'

# (C) 벤더 API 키를 명령줄에서 주입하는 형태. 값이 무엇이든 **키 이름만으로** 잡는다.
VENDOR_KEY='(ANTHROPIC|OPENAI|AZURE_OPENAI|GEMINI|GOOGLE|MISTRAL|COHERE|GROQ|DEEPSEEK|TOGETHER|XAI|OPENROUTER|UPSTAGE|PERPLEXITY|MOONSHOT|DASHSCOPE)_API_KEY[[:space:]]*='

# (D) 외부 모델을 부르는 CLI. 단어 경계로 잡는다 — 첫 토큰이거나 ; & | ( 또는 공백 뒤.
#     `.claude/hooks/...` 같은 경로는 앞이 `.`이라 걸리지 않는다.
#     ⚠️ `llm`(simonw CLI)은 **일부러 넣지 않았다.** 이 레포에 `src/providers/llm.py`,
#     `-k llm` 같은 문자열이 흔해 오탐이 게이트를 신뢰할 수 없게 만든다.
#     오탐이 잦은 패턴은 결국 게이트를 끄게 만들므로, 넣지 않는 편이 안전하다.
VENDOR_CLI='(^|[;&|(`$][[:space:]]*|[[:space:]])(claude|codex|gemini|aichat)[[:space:]]'

# (E) 생산자 파이프라인(ai-news-ontology) 실행. 읽기(cat/ls/grep)는 통과시키고
#     **실행기와 함께 나타날 때만** 잡는다. 실제 위반이 난 코드가 거기 있다.
EXEC='(python3?|py|uv|poetry|pipenv|pdm|hatch|pytest)'
PRODUCER_RUN="(^|[[:space:];&|(])${EXEC}([[:space:]]|\$).*ai-news-ontology|ai-news-ontology.*(^|[[:space:];&|(])${EXEC}([[:space:]]|\$)|-m[[:space:]]+(extraction|export)([[:space:].]|\$)"

matched=""
if echo "$command" | grep -qE "$VENDOR_HOSTS"; then matched="벤더 엔드포인트 호스트"; fi
if [ -z "$matched" ] && echo "$command" | grep -qE "$VENDOR_SDK"; then matched="벤더 SDK 임포트"; fi
if [ -z "$matched" ] && echo "$command" | grep -qE "$VENDOR_KEY"; then matched="벤더 API 키 주입"; fi
if [ -z "$matched" ] && echo "$command" | grep -qE "$VENDOR_CLI"; then matched="외부 모델 CLI 실행"; fi
if [ -z "$matched" ] && echo "$command" | grep -qE "$PRODUCER_RUN"; then matched="생산자 추출 파이프라인 실행"; fi

[ -z "$matched" ] && exit 0

# CLAUDE_PROJECT_DIR이 없으면 cwd 기준. 훅은 레포 루트에서 도는 것이 전제다.
approval="${CLAUDE_PROJECT_DIR:-.}/.claude/external-llm-approved"
if [ -f "$approval" ]; then
  exit 0
fi

cat >&2 <<MSG
[차단] 외부 LLM 벤더 호출로 보이는 명령입니다 (일치: ${matched}).

이 프로젝트는 **외부 LLM 벤더 비의존**이 전제입니다 (ADR-021, docs/governance.md).
LLM 호출의 대상 엔드포인트는 \`.env\`의 VLLM_BASE(KT Cloud AI Nexus vLLM) 하나뿐입니다.

진행하려면 승인 게이트 6항목을 **사용자에게** 제시하고 승인을 받으세요.
1번을 건너뛰지 마세요 — Session 0.5a의 위반이 정확히 그 항목의 부재였습니다.

  1. 대상 엔드포인트가 VLLM_BASE인가? (아니오면 승인 대상이 아니라 규칙 위반입니다)
  2. 나가는 데이터의 범위 (공개 / 합성 / 사내 / 고객)
  3. 목적
  4. 모델명
  5. 건수
  6. 예상 비용

승인을 받았다면 그때 \`.claude/external-llm-approved\`를 만들고 진행하고,
끝나면 지워서 다음 호출에 다시 승인을 받게 하세요.

오탐이면(문서에 예시 문자열을 쓰는 경우 등) Write/Edit 도구로 파일을 쓰세요.
명령줄에 트리거 문자열을 넣지 않는 것이 회피가 아니라 정상 경로입니다.
MSG
exit 2
