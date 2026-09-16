"""검색 결과 본문의 간접 인젝션 방어 (ADR-009).

Researcher·Verifier·Writer 프롬프트에는 **우리가 쓰지 않은 텍스트**(검색된 문서
본문)가 그대로 들어간다. 문서에 "이전 지시를 무시하고 ..."가 적혀 있으면 모델은
그것을 지시로 읽을 수 있다. 사용자가 직접 입력하는 인젝션보다 발견하기 어렵다 —
공격 문자열이 프롬프트가 아니라 **코퍼스**에 있기 때문이다.

## 지우지 않는다

의심 문자열을 삭제하는 방식은 쓰지 않았다. 우리 코퍼스에는
*AgentHarm: A Benchmark for Measuring Harmfulness of LLM Agents*,
*AgentLAB: Benchmarking LLM Agents against Long-Horizon Attacks* 처럼
**공격 기법을 정상적으로 서술하는 논문**이 실제로 들어 있다. 패턴 삭제는 이런
문서를 훼손해 검색 품질을 떨어뜨리면서, 공격자에게는 우회(동의어·다국어·인코딩)
여지를 남긴다. 비용은 확실하고 효과는 불확실한 거래다.

대신 두 가지만 한다.

1. **경계를 위조할 수 없게 만든다.** 문서 본문에 우리가 쓰는 구분자와 같은 문자열이
   있으면 무력화한다. 이건 결정적으로 막을 수 있는 유일한 부분이다 — 경계가 뚫리면
   모델이 데이터와 지시를 구분할 근거 자체가 사라진다.
2. **표시하고 기록한다.** 지시문처럼 보이는 패턴을 탐지해 플래그로 남긴다. 차단이
   아니라 **관측**이 목적이다. 막지 못한 것도 보이면 대응할 수 있다.

나머지(모델이 경계를 존중하느냐)는 프롬프트 지시에 의존하며, **보장이 아니다.**
`tests/security/`에 방어 후에도 뚫리는 케이스를 그대로 남겨둔 이유다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 신뢰할 수 없는 데이터를 감싸는 구분자. 본문에 이 문자열이 나타나면 경계를
# 위조할 수 있으므로 아래에서 무력화한다.
FENCE_OPEN = "<<<UNTRUSTED_DOCUMENT>>>"
FENCE_CLOSE = "<<<END_UNTRUSTED_DOCUMENT>>>"

# 지시문처럼 보이는 패턴. **차단용이 아니라 탐지·기록용이다.**
# 완전하지 않으며 완전해질 수도 없다 (동의어·다국어·인코딩 우회).
_INJECTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("ignore_previous", r"(이전|위의|앞의)\s*(모든\s*)?(지시|명령|지침|프롬프트)"),
    ("ignore_previous", r"ignore\s+(all\s+)?(previous|prior|above)\s+(instruction|prompt|rule)"),
    ("disregard", r"disregard\s+(all\s+)?(previous|prior|the\s+above)"),
    ("system_prompt_exfil", r"(시스템\s*프롬프트|system\s*prompt)"),
    ("role_override", r"(너는\s*이제|지금부터\s*너는|you\s+are\s+now|from\s+now\s+on)"),
    ("instruction_marker", r"(^|\n)\s*(system|assistant|user)\s*:", ),
    ("new_instruction", r"(새로운?\s*(지시|규칙)|new\s+instructions?)"),
    # 명령형 어미까지 거리를 두고 잡는다. 첫 판(session-05)은 "무조건 출력"처럼
    # 붙어 있는 형태만 잡아서, **우리가 직접 쓴 페이로드 10건 중 3건을 놓쳤다.**
    # 그 사실이 이 목록에 방어를 맡기면 안 되는 이유다.
    ("output_override", r"(무조건|반드시|오직)[^.\n]{0,80}(출력|응답|답변|판정|반환)"),
    ("citation_suppression", r"(출처|doc_id|인용)[^.\n]{0,30}(쓰지\s*말|금지|생략|떼)"),
    ("preapproved", r"(사전\s*승인|이미\s*검증|검증되었|pre-?approved)"),
    ("tool_escalation", r"(모든\s*문서|전체\s*인덱스|all\s+documents|sources\s*=)"),
    ("exfiltration", r"(\.env|VLLM_BASE|api[_\s-]?key|환경\s*변수)"),
)

_COMPILED = tuple(
    (name, re.compile(pattern, re.IGNORECASE | re.MULTILINE))
    for name, pattern in _INJECTION_PATTERNS
)


@dataclass(frozen=True)
class SanitizedText:
    """무력화된 본문 + 무엇이 탐지됐는지."""

    text: str
    flags: tuple[str, ...] = ()

    @property
    def suspicious(self) -> bool:
        return bool(self.flags)


def detect_injection(text: str) -> tuple[str, ...]:
    """지시문 패턴을 탐지해 이름 목록을 돌려준다 (중복 제거, 순서 유지)."""
    found: list[str] = []
    for name, pattern in _COMPILED:
        if name not in found and pattern.search(text or ""):
            found.append(name)
    return tuple(found)


def sanitize_document_text(text: str) -> SanitizedText:
    """검색된 문서 본문을 프롬프트에 넣기 전에 통과시키는 함수.

    본문을 지우지 않는다. 경계 위조만 확실히 막고 나머지는 표시한다.
    """
    raw = text or ""
    flags = list(detect_injection(raw))

    cleaned = raw
    for fence in (FENCE_OPEN, FENCE_CLOSE):
        if fence in cleaned:
            # 구분자를 그대로 두면 모델이 보는 경계가 문서 내용으로 조작된다.
            cleaned = cleaned.replace(fence, "[구분자 제거됨]")
            if "fence_forgery" not in flags:
                flags.append("fence_forgery")

    return SanitizedText(text=cleaned, flags=tuple(flags))


def wrap_untrusted(text: str, *, label: str = "") -> str:
    """본문을 신뢰할 수 없는 데이터 경계로 감싼다.

    모델에게 "여기서부터 여기까지는 자료이지 지시가 아니다"를 구조로 알려준다.
    프롬프트 문장만으로 같은 말을 하는 것보다, 경계가 눈에 보이는 편이 낫다.
    """
    sanitized = sanitize_document_text(text)
    header = FENCE_OPEN if not label else f"{FENCE_OPEN} ({label})"
    return f"{header}\n{sanitized.text}\n{FENCE_CLOSE}"
