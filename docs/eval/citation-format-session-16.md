# 인용 형식 전/후 대조 — session-16 (ADR-024)

> **이 문서는 baseline이 아니다.** Researcher 프롬프트의 유사도 점수 노출
> (session-13 §6.2)이 그대로이고, 여기서는 Researcher를 거치지 않고 top-k 전부를
> 인용으로 취급한다. **인용 형식 외의 어떤 수치도 읽지 마라.**

| 항목 | 값 |
| --- | --- |
| LLM 호출 | **0건** (검색만 — 임베딩은 로컬) |
| 임베딩 모델 | `intfloat/multilingual-e5-small` |
| 코퍼스 | 37건 |
| top-k | 4 |
| `PROMPT_VERSION` | `2026-09-16.1` — **변경 없음** (ADR-024) |
| 임베딩 콜드 로딩 | 5.43s (질의 지연이 아니다) |

## 무엇이 바뀌었나

검색 결과의 온톨로지 메타(종류·기술 영역·발행일)를 `RetrievedChunk` → `Citation`으로
실어 Writer 단계까지 보내고, 보고서 끝에 **코드가 렌더링한 출처 표**로 붙인다.
모델의 입력과 본문은 바뀌지 않는다.

## GS-001 — 층 A (`anchor_meta=present`)

질의: LLM 멀티에이전트 협업 시스템을 설계하고 평가할 때 어떤 어려움이 보고되는가?

**전 (session-15까지):** 본문에 붙는 문서 ID가 전부였다.

```
[news:c076df17a63066ea] [arXiv:2602.03128v1] [arXiv:2406.00215v3] [arXiv:2412.05449v1]
```

**후 (session-16):**

## 출처

| # | doc_id | 위치 | 종류 | 기술 영역 | 발행일 |
| ---: | --- | --- | --- | --- | --- |
| 1 | news:c076df17a63066ea | feed_excerpt | Community/Discussion | Application/Product, LLM | 2026-09-16 |
| 2 | arXiv:2602.03128v1 | abstract | 미확인 | 미확인 | 2026-02-03 |
| 3 | arXiv:2406.00215v3 | abstract | 미확인 | 미확인 | 2024-05-31 |
| 4 | arXiv:2412.05449v1 | abstract | Paper | Agent, Eval/Governance, LLM | 2024-12-06 |

> `미확인`은 **온톨로지 export를 거치지 않은 문서**라는 뜻이다 (ADR-022 층 B — 스냅샷 arXiv). `없음`(값이 실제로 비어 있음)과 다르다.
> 발행일은 스냅샷에도 있으므로 두 경우 모두 표시된다.
> 이 표는 코드가 검색 결과에서 그대로 렌더링한 것이며 모델을 거치지 않는다 (ADR-024).

모델이 받는 근거 블록: `sha256:f57ba733796343d6` (메타 제거본 `sha256:f57ba733796343d6`) — **동일**

## GS-003 — 층 B (`anchor_meta=missing`)

질의: 제조 현장에 LLM 기반 멀티에이전트 시스템을 적용한 연구에는 어떤 것이 있는가?

**전 (session-15까지):** 본문에 붙는 문서 ID가 전부였다.

```
[arXiv:2405.16887v2] [arXiv:2406.01893v2] [news:42f467857601213c] [arXiv:2602.03128v1]
```

**후 (session-16):**

## 출처

| # | doc_id | 위치 | 종류 | 기술 영역 | 발행일 |
| ---: | --- | --- | --- | --- | --- |
| 1 | arXiv:2405.16887v2 | abstract | 미확인 | 미확인 | 2024-05-27 |
| 2 | arXiv:2406.01893v2 | abstract | 미확인 | 미확인 | 2024-06-04 |
| 3 | news:42f467857601213c | feed_excerpt | Community/Discussion | Hardware/Chip, Inference/Serving, LLM | 2026-09-16 |
| 4 | arXiv:2602.03128v1 | abstract | 미확인 | 미확인 | 2026-02-03 |

> `미확인`은 **온톨로지 export를 거치지 않은 문서**라는 뜻이다 (ADR-022 층 B — 스냅샷 arXiv). `없음`(값이 실제로 비어 있음)과 다르다.
> 발행일은 스냅샷에도 있으므로 두 경우 모두 표시된다.
> 이 표는 코드가 검색 결과에서 그대로 렌더링한 것이며 모델을 거치지 않는다 (ADR-024).

모델이 받는 근거 블록: `sha256:894beda07f213220` (메타 제거본 `sha256:894beda07f213220`) — **동일**

## GS-011 — 층 A (`anchor_meta=present`)

질의: 모델이 보상을 속이는 행동을 내부 표현으로 탐지할 수 있다는 연구가 있는가?

**전 (session-15까지):** 본문에 붙는 문서 ID가 전부였다.

```
[news:3dd7208e3f2a1dde] [news:1047cd7bf162d6e0] [arXiv:2609.19101v1] [news:42f467857601213c]
```

**후 (session-16):**

## 출처

| # | doc_id | 위치 | 종류 | 기술 영역 | 발행일 |
| ---: | --- | --- | --- | --- | --- |
| 1 | news:3dd7208e3f2a1dde | feed_excerpt | Paper | Agent, Reasoning | 2026-09-16 |
| 2 | news:1047cd7bf162d6e0 | feed_excerpt | Community/Discussion | Safety/Alignment, Agent | 2026-09-16 |
| 3 | arXiv:2609.19101v1 | abstract | Paper | Safety/Alignment, Eval/Governance, LLM | 2026-09-16 |
| 4 | news:42f467857601213c | feed_excerpt | Community/Discussion | Hardware/Chip, Inference/Serving, LLM | 2026-09-16 |

> `미확인`은 **온톨로지 export를 거치지 않은 문서**라는 뜻이다 (ADR-022 층 B — 스냅샷 arXiv). `없음`(값이 실제로 비어 있음)과 다르다.
> 발행일은 스냅샷에도 있으므로 두 경우 모두 표시된다.
> 이 표는 코드가 검색 결과에서 그대로 렌더링한 것이며 모델을 거치지 않는다 (ADR-024).

모델이 받는 근거 블록: `sha256:af4a0e1dd507941e` (메타 제거본 `sha256:af4a0e1dd507941e`) — **동일**

## 층 구성 — 무엇이 `미확인`으로 표시됐나

| 케이스 | 메타 보유 | 미확인(층 B) |
| --- | ---: | ---: |
| GS-001 | 2 | 2 |
| GS-003 | 1 | 3 |
| GS-011 | 4 | 0 |
| **합계** | **7** | **5** |

`미확인`은 온톨로지 export를 거치지 않은 문서다 (스냅샷 arXiv 14/37건, ADR-022 층 B).
**빈칸으로 두지 않는 이유**: 빈칸이면 "등급이 없는 문서"와 "등급을 아직 모르는
문서"가 같아 보이고, 그 구별이 ADR-022가 층을 나눈 이유 그 자체다.

## LLM 입력 불변 확인

**전 케이스에서 동일하다.** 온톨로지 메타는 출처 표에만 쓰이고 모델 입력에는 들어가지 않는다 — 그래서 `PROMPT_VERSION`을 올리지 않았고, 전/후 비교가 "인용 형식 하나만 바뀌었다"는 조건을 만족한다.
