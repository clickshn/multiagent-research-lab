# ADR-025: Researcher 후보 프롬프트의 유사도 점수 노출 — 제거 / 정규화 / 경고 중 택일

- **Status:** Proposed
- **Date:** 2026-09-30
- **Decision:** **미정 — 3안 비교 초안이다.** Researcher 후보 프롬프트에 실리는 절대 유사도
  (`유사도 0.xxx`)를 (가) 제거 / (나) 질의 내부 정규화 / (다) 유지 + 경고 문구 중 어느 것으로
  처리할지 사용자가 결정한다. 이 문서는 선택지와 근거만 정리하며 코드는 바뀌지 않았다.
- **Scope:** multiagent-research-lab (`src/orchestrator/nodes.py` `_format_candidates` / `prompts.py` Researcher 프롬프트)
- **Decision Source:** Human

---

## Context

### Problem

Researcher는 조사 항목 하나와 검색 후보 top-k(기본 4)를 받아, 항목을 **실제로 뒷받침하는**
후보만 고른다. 후보 헤더에 Chroma 절대 유사도가 그대로 실린다:

```
nodes.py:197    f"[{index}] doc_id={chunk.doc_id} (유사도 {chunk.score:.3f})\n"
```

이 절대값은 근거 적합도의 신호로 **오도적이다.** 정답 문서의 점수가 무관 질의(negative)의
최고점보다 낮은 경우가 흔하다 (Evidence). 모델이 "0.80은 낮다"고 읽으면 정답 후보를 버리는
방향으로 기운다.

같은 함정의 확인 이력:

- session-13 §6.2 — 점수 경로 전수 점검. **점수를 모델에게 보여주는 곳은 이 한 곳뿐이다.**
  검색은 점수 컷오프 없이 top-k만 자르고(`retrieval.py`), Verifier 근거 블록
  (`_format_evidence`)에는 점수가 없다.
- ADR-024 Rationale 2 — `release_type`을 Writer에 보여주지 않은 이유가 같은 함정이다.

**점수가 실제로 Researcher의 선택을 바꾸는지는 측정된 적이 없다.** 아래 Evidence는 "점수가
오도적인 값이다"까지이고, "모델이 그 값을 따른다"는 가설이다. 효과를 확인하려면 LLM 호출이
필요하다 (Implementation).

### Constraints

- **세 안 모두 Researcher의 LLM 입력을 바꾼다 → `PROMPT_VERSION`이 올라간다.** (다)도
  예외가 아니다(시스템 프롬프트에 문구가 추가된다). 어느 안이든 session-08 / v1.1 파이프라인
  비교선이 끊긴다.
- **한 번에 하나만 바꾼다** (ADR-002). 점수 처리와 다른 하네스 변경(P1 병렬화, T2 tools 등)을
  같은 구간에 넣으면 지표 변화의 원인을 가를 수 없다.
- **순서 충돌이 있다.** session-13 §6.2는 "파이프라인 재측정(`bench_golden.py`)보다 먼저
  결정한다 — 그렇지 않으면 오도적 신호가 섞인 값을 baseline으로 굳힌다"고 적었다. v1.2 S0는
  "하네스는 v1.1 그대로"로 baseline을 먼저 잰다. 두 방침 중 하나를 고르는 것이 이 결정의 일부다
  — 점수 노출 상태의 baseline을 **의도적으로** 남기고(before) 이 ADR을 적용한 뒤(after)를 재면
  오히려 효과 측정이 되지만, 그러려면 baseline에 "점수 노출 상태"라는 조건을 명기해야 한다.
- 필터가 운영 경로에 배선되면 점수 분포가 바뀐다 (session-14: 필터 적용 시 최난도 negative
  0.8303 → 0.7965). 정규화 방식은 **그 분포 위에서** 정해야 한다.

## Decision

### Selected

- **Technology:** 해당 없음 (프롬프트 형식 결정)
- **Architecture:** **미정.** (가)/(나)/(다) 중 택일 또는 조합 — 사용자 결정 대기
- **Implementation:** 결정 전까지 `_format_candidates`·`PROMPT_VERSION` 변경 없음

## Rationale

1. 절대 유사도는 **질의 간 비교가 성립하지 않는 값**인데, 모델은 한 질의 안의 네 숫자만 보고
   그것을 절대 척도로 읽을 수 있다. 한국어 질의에서 정답 23건 중 14건이 무관 질의 최고점보다
   낮다 — "높다/낮다"의 절대 기준이 이 임베딩에 없다.
2. 점수 경로는 이 한 곳뿐이다 (session-13 §6.2). 고칠 지점이 국소적이라 되돌리기 비용이 낮다.
3. 영향은 언어에 따라 다르다 (ko 14/23, en 4/23). 점수 노출의 해악이 한국어 질의 쪽에
   몰린다는 뜻이고, 이 프로젝트의 골든셋은 전부 한국어 질의다.

## Evidence

- **Experiment:** `docs/eval/probe-retrieval-session-16-after-chunk-metadata.json` (필터 없음,
  코퍼스 37, `embeddings_queue` = 37, e5-small @ ADR-011 고정 리비전). positive 23건의
  `best_expected_score`를 negative 7건의 최고점과 대조 (2026-09-30 재집계, LLM 호출 0건):

  | 언어 | 최난도 negative 최고점 | 정답 점수가 그보다 낮은 positive | 층 A | 층 B | 그중 **top-4 안에 있는** 것 |
  |---|---:|---:|---:|---:|---|
  | ko | **0.8303** (GS-028) | **14/23** | 8/14 | 6/9 | **6건** — GS-011(3위 0.8116), **GS-013(1위 0.8251)**, GS-015(4위 0.8285), **GS-017(1위 0.8124)**, GS-018(2위 0.8074), GS-021(3위 0.8040) |
  | en | 0.8522 | 4/23 | 4/14 | 0/9 | 2건 — GS-010(2위 0.8473), GS-024(1위 0.8483) |

  - **top-4 안의 6건이 직접 위험군이다.** Researcher가 실제로 보는 후보에 정답이 들어 있는데,
    그 점수가 무관 질의의 1위 점수보다 낮다. 그중 2건(GS-013·017)은 **1위**다 — 질의 내부
    순위로는 가장 강한 후보인데 절대값은 "낮아 보인다".
  - top-4 밖의 8건은 Researcher가 애초에 보지 못하므로 점수 노출과 무관하다(검색 재현율 문제).
  - 층 분리: ADR-022에 따라 층 A·B를 나눠 적었다. n=14/9이므로 1건 ≈ 7%p/11%p다.
- **Cost:** 결정 자체는 0원. 효과 검증(Implementation)은 vLLM 할당분 호출.

## Alternatives

### (가) 점수를 빼고 순위만 보여준다

- **Pros:** 가장 단순하다. 오도적 절대값이 모델에 도달하는 경로가 원리적으로 사라진다.
  후보는 이미 점수순이므로 `[1]`~`[4]` 번호가 순위 정보를 그대로 담는다.
- **Cons:** 후보 간 간격 정보(1위와 2위가 0.001 차이인지 0.05 차이인지)도 함께 사라진다.
  모델이 신뢰도 신호를 잃는다.
- **Rejected because:** 미결 (사용자 결정 대기)

### (나) 질의 내부 정규화값으로 바꿔 보여준다

- **Pros:** 질의 내부의 상대적 간격은 남기면서 절대 척도로 읽힐 여지를 줄인다.
- **Cons:** 질의 간 비교는 여전히 불가하다. **k=4에서는 정규화 방식이 불안정하다** —
  min-max는 4위를 항상 0.0으로 만들어 "무관"으로 읽힐 위험이 새로 생기고, z-score는 n=4라
  분산 추정이 흔들린다. 1위 대비 차(`score − top`)가 가장 무난하나 설계 선택이 하나 더 늘어난다.
  정규화 방식 자체가 새 변수가 된다.
- **Rejected because:** 미결 (사용자 결정 대기)
- **Recheck if:** 필터가 운영 검색 경로에 배선되어 점수 분포가 바뀔 때 (session-13 §6.2
  "필터 적용 후 분포를 먼저 봐야 한다", session-14 0.8303 → 0.7965)

### (다) 그대로 두고 "절대값을 신뢰도로 읽지 말라"고 명시한다

- **Pros:** 가장 싸다. 후보 헤더 형식이 그대로라 다른 코드 변경이 없다.
- **Cons:** 모델 순응에 의존한다 — 같은 채널(자연어)에서 숫자와 경고가 경쟁한다
  (`prompts.py` 신뢰 경계 주석과 같은 한계). 그리고 **`PROMPT_VERSION`은 (가)·(나)와 똑같이
  올라간다** — "싸다"는 구현 비용 얘기이지 비교선 비용이 아니다.
- **Rejected because:** 미결 (사용자 결정 대기)

## Consequences

### Positive

- 어느 안이든 결정되면 session-13부터 이월된 결정 대기 ①이 닫힌다.
- 적용 전/후를 같은 조건으로 재면 "점수 노출이 Researcher 선택을 바꾸는가"라는 가설을 처음으로
  검증할 수 있다.

### Negative

- `PROMPT_VERSION` 변경 → v1.1 / session-08 파이프라인 수치와 직접 비교가 끊긴다.
- (나)를 고르면 정규화 방식이라는 새 하네스 변수가 생긴다.

### Risks

- **효과가 측정되지 않은 결정이다.** 모델이 점수를 무시하고 있었다면 세 안 모두 효과 0이고,
  변동만 추가된다. temperature=0에서도 실행 간 출력이 달라진 관측이 있으므로
  (session-08 no-cache vs cache-cold, 9건 중 3건 completion 토큰 상이), **자연 변동 폭을 먼저
  재지 않으면 전/후 차이를 효과로 읽을 수 없다.**
- baseline 순서(Constraints)를 정하지 않고 진행하면 "점수 노출 상태"가 명기되지 않은 baseline이
  굳어진다.

## Implementation

- [ ] 사용자 결정: (가)/(나)/(다) 및 baseline 대비 순서
- [ ] `_format_candidates` 변경 + `PROMPT_VERSION` 갱신
- [ ] 테스트: 후보 블록에 절대 점수 문자열이 없는지(가·나) / 경고 문구가 있는지(다) 고정
- [ ] 효과 측정: 위 top-4 위험군 6건(GS-011·013·015·017·018·021)을 전/후 비교 — Researcher가
      정답을 `supporting`에 넣었는지. 자연 변동 폭(S0b) 측정 후에만 판정
- [ ] 문서: 결정 시 이 ADR의 Selected 갱신, session-13 §6.2 결정 대기 ① 종료 표기

## Reversibility

- **Reversible:** Yes
- **Rollback:** `_format_candidates` 한 줄과 `PROMPT_VERSION`을 되돌린다. 인덱스·State·계약 변경 없음.
- **Migration Cost:** Low (코드) — 단, 되돌려도 그 사이 측정한 수치는 다른 `PROMPT_VERSION`에 묶인다

## Review Trigger

- (나) 정규화 방식: 필터가 운영 검색 경로에 배선되어 점수 분포가 바뀔 때

## References

- **Related ADR:** ADR-002(모델 고정·한 번에 하나), ADR-005 Amendment 2(최난도 먼저 표기),
  ADR-006(셀 수 있는 것은 모델에게 묻지 않는다), ADR-022(층 분리), ADR-024 Rationale 2(같은 함정)
- **Documentation:** `docs/handoff/session-13.md` §3.1·§6.2, `docs/handoff/session-14.md`,
  `docs/eval/probe-retrieval-session-16-after-chunk-metadata.json`, `src/orchestrator/nodes.py:184-201`

## AI/ML Details

- **Model:** gemma-4-31B-it (vLLM, `VLLM_BASE`) — Researcher 노드, temperature 0.0
- **Evaluation:** top-4 위험군 6건의 `supporting` 포함 여부, 전/후. 층 A·B 분리 보고, n 병기
- **Inference:** 임베딩 `intfloat/multilingual-e5-small` (ADR-011 고정), top_k=4, 코사인 유사도 절대값

### Evaluation

| Metric | Before | After | Target |
| ------ | -----: | ----: | -----: |
| 위험군 6건 중 Researcher가 정답을 고른 수 (ko) | 미측정 | 미측정 | 미정 |
