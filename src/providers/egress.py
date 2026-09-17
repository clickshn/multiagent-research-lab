"""외부 LLM 벤더 엔드포인트 차단 (ADR-021).

이 프로젝트의 제약은 "폐쇄망"이 아니라 **외부 LLM 벤더 비의존**이다(세션 2에 용어를
정정했다 — `docs/security/owasp-notes.md` §0). 기준은 네트워크 도달 가능성이 아니라
**데이터가 어느 벤더의 모델로 향하는가**다. 그래서 이 모듈이 검사하는 것은 방화벽이
아니라 **목적지 호스트**다.

Session 0.5a에서 이 규칙이 깨졌다. 승인 게이트가 비용·건수·모델만 묻고 "대상
엔드포인트가 `VLLM_BASE`인가"를 묻지 않아, 규칙이 있는 상태로 위반이 통과했다.
그래서 규칙을 문서가 아니라 **코드와 훅 두 곳에서 집행**한다.

⚠️ **이 모듈은 거부 목록(denylist)이다. 구조상 불완전하다.**
새 벤더나 사설 프록시 호스트는 여기 없으므로 통과한다. 실제 허용 목록 역할을 하는 것은
**프로바이더 계층이 `VLLM_BASE` 하나만 읽는다**는 설계(ADR-003)이고, 이 모듈은 그
설계가 환경변수 교체 한 줄로 무너지는 것을 막는 2차 방어선이다. 목록을 늘리는 것으로
이 한계가 없어지지 않는다는 점을 전제로 쓴다.

`.claude/hooks/check-external-llm.sh`가 같은 목록을 **독립적으로 복제**해서 들고 있다.
참조가 아니라 복제인 이유는 훅이 Python 임포트 없이 도는 셸 스크립트이기 때문이고,
두 목록이 갈라지는 것은 `tests/test_external_llm_gate.py`가 대조해서 막는다.
"""

from __future__ import annotations

# 호스트 접미사. `host == h` 또는 `host.endswith("." + h)`이면 일치로 본다.
EXTERNAL_LLM_HOSTS: tuple[str, ...] = (
    "api.anthropic.com",
    "api.openai.com",
    "openai.azure.com",
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "api.mistral.ai",
    "api.cohere.ai",
    "api.cohere.com",
    "api.groq.com",
    "api.together.xyz",
    "api.perplexity.ai",
    "api.deepseek.com",
    "api.x.ai",
    "openrouter.ai",
    "api.upstage.ai",
    "api.moonshot.cn",
    "open.bigmodel.cn",
    "api.z.ai",
    "dashscope.aliyuncs.com",
    "clovastudio.stream.ntruss.com",
    "clovastudio.apigw.ntruss.com",
)

# 호스트 **부분 문자열**. 리전이 호스트 중간에 들어가 접미사로 못 잡는 것들
# (예: `bedrock-runtime.ap-northeast-2.amazonaws.com`).
EXTERNAL_LLM_HOST_SUBSTRINGS: tuple[str, ...] = (
    "bedrock-runtime.",
    "bedrock-agent-runtime.",
)


class ExternalEndpointError(RuntimeError):
    """외부 LLM 벤더 엔드포인트가 설정됐다. 호출 전에 막는다."""


def host_of(url: str) -> str:
    """URL에서 호스트만 뽑는다. 스킴·포트·경로·인증정보를 버린다.

    파싱 실패를 예외로 만들지 않는다 — 이 함수는 검사 경로에 있고, 여기서 터지면
    **검사가 없는 것과 같은 상태**가 되기 때문이다. 이상한 입력은 빈 문자열이 되어
    "일치 없음"이 되는데, 그건 이 함수가 아니라 `load_settings()`의 형식 검사가 잡는다.
    """
    rest = url.split("://", 1)[-1]
    authority = rest.split("/", 1)[0]
    # user:pass@host 형태에서 호스트만.
    authority = authority.rsplit("@", 1)[-1]
    return authority.split(":", 1)[0].strip().lower()


def match_external_vendor(url: str) -> str | None:
    """외부 LLM 벤더로 알려진 호스트면 일치한 패턴을 돌려준다. 아니면 None."""
    host = host_of(url)
    if not host:
        return None
    for suffix in EXTERNAL_LLM_HOSTS:
        if host == suffix or host.endswith("." + suffix):
            return suffix
    for fragment in EXTERNAL_LLM_HOST_SUBSTRINGS:
        if fragment in host:
            return fragment
    return None


def assert_internal_endpoint(url: str, *, field: str = "VLLM_BASE") -> None:
    """외부 LLM 벤더 호스트면 `ExternalEndpointError`를 던진다.

    에러 메시지에 URL 전체를 넣지 않는다 — `VLLM_BASE`는 시크릿이고
    (`docs/governance.md` "시크릿 취급"), 진단에 필요한 것은 **어느 벤더인가**뿐이다.
    """
    matched = match_external_vendor(url)
    if matched is None:
        return
    raise ExternalEndpointError(
        f"{field}가 외부 LLM 벤더 호스트를 가리킵니다 (일치: {matched}). "
        "이 프로젝트는 외부 LLM 벤더 비의존이 전제입니다 (ADR-021). "
        "KT Cloud AI Nexus vLLM 엔드포인트 외의 호출은 사용자 승인 전에는 금지입니다."
    )
