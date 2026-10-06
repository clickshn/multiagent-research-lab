# ADR-028: tech_domain 선택은 json_schema 강제 출력으로 받는다 — 네이티브 tool calling을 쓰지 않는다

- **Status:** Accepted (2026-10-01)
- **승인 메모:** session-22 — 사용자 결정
- **Date:** 2026-10-01
- **Decision:** Researcher가 항목마다 `tech_domain` 필터 값을 고르는 호출은 OpenAI 호환
  `response_format={"type": "json_schema", "json_schema": {name, strict, schema}}`로 보낸다.
  스키마는 `{"tech_domain": enum}` 하나이고 enum = 통제어휘(정렬) + `"없음"`이다.
  **유효 파라미터 이름은 `response_format`이다** — vLLM 고유 파라미터(`extra_body`의 `guided_json` /
  `structured_outputs`)는 쓰지 않는다. 네이티브 tool calling(`tools`/`tool_choice`)도 쓰지 않는다.
- **Scope:** multiagent-research-lab (`src/providers/` · `src/orchestrator/nodes.py` · v1.2-T2)
- **Decision Source:** Human (방식 지정: json_schema 경로, tool calling 미사용) + 호출 판별(아래 Evidence)

---

## Context

### Problem

v1.2-T2는 에이전트가 검색 필터 값을 고르게 한다. 값이 통제어휘 밖이면 `FilterValueError`로
실행이 멈춘다(ADR-027). 그래서 **출력 값이 어휘 안에 있다는 것을 디코딩 단계에서 보장해야 한다.**
프롬프트 지시만으로는 보장되지 않는다.

### Constraints

- **서버가 파라미터를 존중하는지는 호출 없이는 알 수 없다.** KT Cloud vLLM 엔드포인트의 기동 옵션은
  우리가 볼 수 없다(session-17 §5.4).
- **생산자 레포(ai-news-ontology) ADR-019:** vLLM으로 옮길 때 **모르는 파라미터가 오류 없이 무시될 수 있다.**
  요청은 200으로 성공하고 출력도 그럴듯한데 강제는 걸리지 않은 상태가 조용히 생긴다.
  → "응답이 왔다"나 "JSON이 나왔다"로는 강제를 판별할 수 없다.
- 네이티브 tool calling은 vLLM에 `--enable-auto-tool-choice --tool-call-parser` 기동 옵션이 필요하다.
  tool calling을 쓰려면 프로바이더 응답 파싱(`tool_calls`)도 바꿔야 한다(session-17 §5.4). **이번 범위에서 쓰지 않는다**(사용자 지시).
- 프로바이더 계층 밖에서 모델을 부르지 않는다(ADR-003).

## Decision

1. **전달 경로 (session-22 `cdaad22`):** `LLMProvider.complete(..., response_format=None)`.
   `None`이면 payload·캐시 키·span 입력 해시에 **키 자체를 넣지 않는다.** 그래서 기존 호출은 바이트 단위로 그대로다.
   값이 있으면 셋 모두에 반영한다. 캐시 키에서 빠지면 스키마만 다른 호출이 서로의 응답을 받는다.
2. **파라미터:** `response_format` json_schema(`strict: true`, `additionalProperties: false`).
3. **스키마는 코드가 만든다:** `nodes.tech_domain_schema(vocab)`. 어휘는 manifest에서 읽고
   (복제 금지, 계약 §5), 정렬한 뒤 `"없음"`을 마지막에 붙인다.
4. **강제가 깨진 출력은 실행을 멈춘다.** JSON이 아니거나, 키가 다르거나, enum 밖이면 멈춘다.
   무필터로 폴백하지 않는다. 폴백하면 강제 실패가 기권처럼 보인다
   (사전 등록 `docs/plans/v1.2-preregistration.md` §4, `ce86b04`).

## Evidence — 강제 판별 (session-22, 3건, 사용자 승인)

`scripts/probe_schema_enforcement.py` → `docs/eval/t2-schema-enforcement.json`.
조건은 다음과 같다.
- 엔드포인트: `VLLM_BASE`(`***.ktcloud.com`, `endpoint_fp 1897f0ecc081`), 모델 `gemma-4-31B-it`
- `temperature 0`, 캐시 끔, 입력은 합성 문장(골든셋 미사용)
- 시스템 프롬프트가 **"JSON을 쓰지 마라. 자연어 한두 문장으로만 답한다"** 를 지시한다.
  JSON을 시키는 프롬프트에서는 스키마대로 나온 출력이 강제 덕인지 지시를 따른 덕인지 구별되지 않기 때문이다.

| 호출 | 스키마 | 항목(합성) | 출력 | 판정 |
|---|---|---|---|---|
| control | 없음 | 에이전트 협업 프로토콜 설계 | `Agent. 여러 에이전트 간의 역할 분담과…` (자연어) | 프롬프트가 JSON을 막는다 → **판별력 있음** |
| enforce1 | json_schema | (같음) | `{"tech_domain": "Agent"}` | 지시를 어기고 스키마대로 → **강제됨** |
| enforce2 | json_schema | 교차로 신호 주기와 보행자 대기 | `{"tech_domain": "없음"}` | 기권 값도 enum 안에서 나옴 → **강제됨** |

- **결론: 이 엔드포인트는 `response_format` json_schema를 존중한다.** ②(`extra_body`) 시도는 필요 없었다. 호출은 3건이다.
- LiteLLM 1.101.0이 `openai/` 경로에서 `response_format`을 **변형 없이** 요청 본문에 싣는다는 것도 확인했다.
  일부 프로바이더 경로에서는 LiteLLM이 json_schema를 tool 호출로 바꿔 보낸다. 우리 경로에서는 그렇지 않다는 것을
  로컬 가짜 서버로 확인했다(`tests/test_response_format_path.py`).

⚠️ **범위:** n=3이다. 이 결과는 "강제가 걸린다"는 것을 보여 줄 뿐 "항상 걸린다"는 증명이 아니다.
그래서 Decision 4(위반 시 정지)를 유지한다. T3 본측정의 선택 호출 전부가 이 판별의 연장이다.
위반이 1건이라도 나오면 실행이 멈추고 그 사실이 기록된다.

## Alternatives

| 대안 | 기각 이유 |
|---|---|
| 네이티브 tool calling (`tools`, `tool_choice`) | 사용자 지시로 범위 밖. 서버 기동 옵션을 확인할 수 없고, 응답 파싱(`tool_calls`) 변경이 필요하다 |
| vLLM `extra_body` (`guided_json` / `structured_outputs` / `guided_choice`) | `response_format`이 강제됨을 확인했으므로 불필요하다. vLLM 버전마다 이름이 다르다(`guided_*`는 신버전에서 `structured_outputs`로 바뀜). 서빙 교체 시 ADR-019식 조용한 무시 위험이 더 크다 |
| 프롬프트 지시 + `_extract_json` 파싱 | 어휘 밖 값이 나오면 `FilterValueError`로 멈출 뿐 막을 수단이 없다. 강제가 아니다 |

## Consequences

- **Positive:** 선택 값이 어휘 안에 있다는 것이 디코딩 단계에서 보장된다. 프로바이더 계층의 OpenAI 호환 파라미터 하나로
  표현되므로 서빙을 바꿔도 같은 이름을 쓴다(ADR-003).
- **Negative:** 엔드포인트를 바꾸거나 vLLM을 업그레이드하면 강제가 **조용히 꺼질 수 있다**(생산자 ADR-019).
  Decision 4가 이를 실행 시점에 잡지만, 잡는 시점은 실패가 난 뒤다.
- **Neutral:** 캐시 키 형식은 바뀌지 않는다(`CACHE_FORMAT_VERSION` 1 유지). 스키마가 없는 호출의 키는 그대로다.

## Reversibility

높다. 선택 호출을 끄면(도구 off) 스키마를 쓰는 경로가 없어진다. 프로바이더 인자는 additive이고 기본값은 None이다.

## Review Trigger

- 엔드포인트(`endpoint_fp`)·모델·vLLM 버전이 바뀔 때 → `probe_schema_enforcement.py`를 다시 돌린다(3건)
- 선택 호출에서 스키마 위반 정지가 1건이라도 날 때
- 네이티브 tool calling이 범위에 들어올 때

## References

- ADR-003(프로바이더 경계) · ADR-027(필터 배선 B, `FilterValueError`) · ADR-002(한 번에 하나)
- **생산자 레포 ai-news-ontology ADR-019** — vLLM 이전 시 미지 파라미터 무시
- session-17 §5.4(`tools` 전달 경로 누락 3곳) · 사전 등록 `docs/plans/v1.2-preregistration.md` (`ce86b04`)
- `docs/eval/t2-schema-enforcement.json` · `scripts/probe_schema_enforcement.py` · `tests/test_response_format_path.py`
