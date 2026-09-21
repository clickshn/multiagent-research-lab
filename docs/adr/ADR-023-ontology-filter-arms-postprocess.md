# ADR-023: 온톨로지 필터는 프로브 후처리 arm으로 배선하고, 필터 값은 골든셋 선언에서만 끌어온다

- **Status:** Proposed
- **Date:** 2026-09-21
- **Decision:** `release_type` / `tech_domain` 필터를 `scripts/probe_retrieval.py`의
  **후처리 arm**으로 배선한다(`--arm` × `--null-policy` 2×2). 각 케이스에 걸 **필터 값은
  골든셋 `targets_ontology`의 사전 선언에서 기계적으로 유도**하고 측정 시점에 고르지 않는다.
  점수 간격은 **앵커와 negative에 같은 필터 값을 걸어** 재고, `score_gap_canonical`
  (hardest / mean / n / golden_set_version)로 스크립트가 직접 낸다.
- **Scope:** multiagent-research-lab (`scripts/probe_retrieval.py` / Session 3 필터 판정 설계)
- **Decision Source:** Human

---

## Context

### Problem

ADR-022가 Session 3의 보고 형식(층 A/B/C 3표 + strict·null-통과 2 arm)을 고정했지만,
**필터를 실제로 어떻게 거는지는 정해져 있지 않았다.** 특히 두 가지가 비어 있었다.

1. **필터 값을 누가 고르는가.** `tech_domain=Reasoning`처럼 케이스마다 축이 다른데,
   그 값을 측정 시점에 고르면 필터에 유리한 축을 고를 여지가 생긴다. ADR-022가
   `anchor_meta`를 **측정 전에** 골든셋에 박아 넣은 것과 같은 문제가 필터 값에도 있다.
2. **점수 간격을 필터 arm에서 어떻게 재는가.** 필터는 점수를 바꾸지 않고 후보만 지운다.
   따라서 **positive만 거르고 negative를 안 거르면 간격은 arm과 무관하게 항상 같은 값**이
   나온다 — 구성상 상수라 아무것도 재지 못한다. 실제로 첫 구현이 그랬고,
   5개 arm 전부에서 간격이 소수점까지 동일하게 나와서 발견했다.

동시에 ADR-005 Amendment 2의 후속 체크박스("`score_gap_canonical`을 표기 순서대로 낸다")가
"Session 3에서 필터 arm을 배선할 때 함께 넣는다"로 예약돼 있었다.

### Constraints

- **`src/tools/retrieval.py` 변경 0줄**(ADR-022 Decision 6, Implementation). 재인덱싱도 없다.
- `tech_domains`는 `|` 연결 문자열이고 Chroma 1.5.5 메타데이터 연산자에 부분 일치가 없다
  (ADR-020 Rationale) → DB 레벨 필터가 불가능하다.
- ADR-002 "한 번에 하나만 바꾼다" — 이 세션에서 프롬프트·임베딩·코퍼스를 함께 바꾸지 않는다.
- 외부 LLM 벤더 호출 0건, LLM 호출 0건 (governance 승인 게이트).

## Decision

### Selected

- **Technology:** 추가 의존성 없음. 기존 `ChromaRetriever`를 그대로 호출하고
  k=코퍼스 크기 전수를 받아 **프로브 스크립트 안에서** 거른다.
- **Architecture:** `--arm {none,release_type,tech_domain}` × `--null-policy {strict,pass}`.
  arm은 **서로 다른 실행·서로 다른 결과 파일**이다. 한 실행이 한 조건만 측정한다.
- **Implementation:**

  1. **필터 값의 출처 (사전 선언, 기계적 유도).**
     - `release_type` arm: 앵커의 `targets_ontology.release_type`을 그대로 쓴다.
     - `tech_domain` arm: `filter_axis`가 `tech_domains=X` 형태로 축을 선언했으면 그 값.
       선언이 없으면 앵커의 `tech_domains` 중 **코퍼스 내 선택도가 가장 높은(빈도가 가장
       낮은) 도메인**, 동률이면 **선언 순서가 앞선 것**.
     - 둘 다 없으면(층 B) `None` — 필터 값 자체가 선언되지 않는다.
     - 유도된 값과 그 **출처(`value_source`)를 케이스마다 결과 JSON에 남긴다.**
  2. **층 B의 "구조적 재현율 0"의 정확한 형태.** 필터 값이 선언되지 않은 케이스는
     strict에서 **매치 대상이 존재할 수 없으므로** 생존자가 0이 된다. null-통과에서는
     메타 결측 문서(14건)만 남는다. 이것은 측정 결과가 아니라 **정의**다.
  3. **negative의 주 순위에는 필터를 걸지 않는다.** negative는 앵커가 없어 자기 필터 값을
     선언할 수 없다. 골든셋 GS-008도 "필터와 무관하게 최고 점수 자체가 관측치"라고 적었다.
  4. **점수 간격만은 예외로 negative도 건다 — 앵커와 *같은* 값으로.** 각 negative에 대해
     arm에 등장한 모든 필터 값별 최고 점수(`top_score_by_filter_value`)를 함께 내고,
     간격 계산에서는 **앵커가 돌았던 값**을 골라 쓴다. 양쪽을 같은 조건에 두지 않으면
     간격은 상수가 된다.
  5. **`score_gap_canonical`을 스크립트가 낸다.** 키 순서가 곧 ADR-005 Amendment 2의
     표기 순서다: `hardest` → `mean` → `n` → `golden_set_version`. 사람이 하나만 적는
     경로를 없애려고 **파생값을 따로 내지 않고 한 덩어리로** 내며, `display` 문자열
     (`"0.0286(최난도) / 0.0502(평균), negative n=7"`)까지 스크립트가 만든다.
     앵커는 둘이다 — 연속성 앵커 **GS-005**(ADR-005 §4, 층 B라 strict arm에서는 소실)와
     필터 arm용 층 A 앵커 **GS-013**. **앵커가 소실되면 다른 앵커로 갈아타지 않고
     `null` + 사유를 낸다** (갈아타는 순간 "관대한 쪽으로 수렴"이 다시 일어난다).
  6. **출신 × 길이 분류는 `probe_length_vs_language.py`에서 import한다.** 복사하면 두
     스크립트가 조용히 어긋나고, 그러면 필터 전/후 밀어내기 분해가 같은 잣대의 값이 아니게 된다.

## Rationale

1. **필터 값을 골든셋에서 유도하면 "필터에 유리한 축 고르기"가 구조적으로 불가능해진다.**
   ADR-022가 `anchor_meta`로 막은 것과 같은 방어선을 필터 값에도 세운 것이다. 유도 규칙이
   기계적이므로 재현되고, `value_source`가 케이스별로 남으므로 감사된다.
2. **`selectivity가 가장 높은 도메인`을 기본 규칙으로 둔 것은, 선언된 축을 덮어쓰지
   않으면서 나머지를 자동으로 채우기 위해서다.** 실제로 이 규칙은 골든셋이 `filter_axis`로
   명시한 6건과 충돌하지 않고, GS-018의 "Data/Synthetic은 코퍼스 유일(1/23)" 같은
   설계 메모와도 일치한다. 선택도 바닥 arm(GS-015 = `Eval/Governance` 13/23)은 골든셋이
   **일부러** 선언한 값이므로 규칙이 그것을 덮지 않는다.
3. **간격 계산에서 양쪽에 같은 필터를 거는 것이 유일하게 의미 있는 비교다.** 필터는 점수가
   아니라 후보 집합을 바꾸므로, 비교 대상도 같은 후보 집합 위에 있어야 한다. 한쪽만 걸면
   "간격이 안 움직였다"가 측정 결과가 아니라 산술적 항등식이 된다.
4. **후처리 필터는 `retrieval.py`를 지키는 선택이기도 하다.** 필터를 검색 코드에 넣으면
   이 세션이 "필터 하나만 바꾼다"는 조건(ADR-002)을 잃고, Chroma의 부분 일치 부재
   (ADR-020)를 우회하려고 재인덱싱까지 끌고 들어가게 된다.

## Evidence

- **Experiment:** 골든셋 v2.0(30건) · 코퍼스 37건 · `top_k=4` · LLM 호출 0건 ·
  `LLM_CACHE` 꺼짐 · 임베딩 `intfloat/multilingual-e5-small` @ `614241f622f5` ·
  `extraction_model` = `claude-opus-5` 23/23(교체 없음 확인).

  층 A(n=14) ko 재현율 / 기대 문서 평균 순위:

  | arm | ko 재현율 | ko 평균 순위 | en 재현율 | 층 B(n=9) ko |
  |---|---:|---:|---:|---:|
  | 무필터 baseline | 12/14 = 85.7% | 3.86 | 12/14 = 85.7% | 3/9 = 33.3% |
  | `release_type` strict | 13/14 = 92.9% | 1.57 | **14/14 = 100%** | **0/9 = 0%** |
  | `release_type` null-통과 | 13/14 = 92.9% | 2.21 | 12/14 = 85.7% | 7/9 = 77.8% |
  | **`tech_domain` strict** | **14/14 = 100%** | **1.36** | **14/14 = 100%** | **0/9 = 0%** |
  | `tech_domain` null-통과 | 13/14 = 92.9% | 2.00 | 12/14 = 85.7% | 7/9 = 77.8% |

- **Benchmark:** 층 A 앵커(GS-013) 기준 점수 간격 — 앵커·negative 동일 필터.
  무필터 `-0.0052(최난도) / 0.0118(평균)` → `tech_domain` strict
  **`0.0286(최난도) / 0.0502(평균)`** (ko, 골든셋 v2.0, negative n=7).
  `release_type` strict는 `-0.0006 / 0.0202`로 거의 움직이지 않는다.
- **Production Data:** ADR-022 Rationale이 예측한 null 희석률이 실측과 **정확히** 일치했다 —
  `release_type=Paper` 생존 11 → 25건(14/25 = 56.0%), `tech_domain=Reasoning` 생존
  2 → 16건(14/16 = **87.5%**). null-통과를 주 규칙으로 쓰지 않은 근거가 수치로 재현됐다.
- **Cost:** 추가 비용 0원. 네트워크 호출 0건, 새 의존성 0개.

## Alternatives

### 필터를 `src/tools/retrieval.py`의 Chroma `where` 절로 배선한다

- **Pros:** 운영 경로와 측정 경로가 같아진다. 후처리와 달리 top-k를 DB가 잘라준다.
- **Cons:** `tech_domains`가 `|` 연결 문자열이라 Chroma 1.5.5에 부분 일치 연산자가 없다
  (ADR-020 Rationale). 배선하려면 스키마를 바꿔 재인덱싱해야 하고, 그러면 이 세션이
  필터 외의 변수를 하나 더 바꾸게 된다(ADR-002).
- **Rejected because:** ADR-022 Decision 6이 이미 후처리로 정해 두었고, 재인덱싱은
  "한 번에 하나만 바꾼다"를 깨뜨린다.
- **Recheck if:** 2단계 220건 반입으로 재인덱싱이 어차피 필요해지는 시점.

### negative에도 각 arm의 필터를 걸어 주 순위까지 필터한다

- **Pros:** positive와 negative의 처리가 완전히 대칭이 된다.
- **Cons:** negative에는 앵커가 없어 필터 값을 선언할 수 없다. 값을 고르는 순간 그 축은
  우리가 사후에 만든 것이 되고, 골든셋의 사전 선언 방어선 밖으로 나간다.
- **Rejected because:** 주 순위는 사전 선언을 지키는 쪽을 택했다. **다만 버리지 않았다** —
  점수 간격에 한해 **앵커와 동일한 값**으로 걸어 쓴다(Decision 4). 그 값은 앵커가
  선언한 것이라 사후 선택이 아니다.

### 앵커가 필터에 소실되면 다른 앵커로 대체해 간격을 낸다

- **Pros:** 모든 arm에서 간격 칸이 비지 않는다.
- **Cons:** 대체 앵커는 "남아 있는 것" 중에서 고르게 되므로, 살아남은 쪽이 항상
  더 좋아 보이는 선택 편향이 생긴다. ADR-005 Amendment 2가 막으려던 "대표값 하나를
  관대한 쪽으로 수렴시키는" 실패와 같은 형태다.
- **Rejected because:** 빈 칸이 편향된 숫자보다 낫다. `null` + `unavailable_reason`을 낸다.

## Consequences

### Positive

- 필터 판정이 **재현 가능**해졌다. 필터 값·생존자 수·제거된 문서의 출신 구성이 전부
  결과 JSON에 남아, 다음 세션이 원자료를 다시 만들지 않고 검증할 수 있다.
- ADR-005 Amendment 2의 표기 규칙이 **사람 기억에서 도구로** 옮겨졌다. 간격을 하나만
  적는 경로가 스크립트 레벨에서 사라졌다.
- `retrieval.py` 변경 0줄이 유지됐다 — 운영 검색 경로는 이 세션에서 건드리지 않았다.

### Negative

- **필터 값이 앵커에서 나오므로 오라클이다.** 질의로부터 필터 값을 고르는 단계(플래너)는
  이 측정에 **포함되지 않았다.** 따라서 위 개선폭은 **필터 효과의 상한**이며, 실제
  파이프라인 이득은 이보다 작다. 이 한계는 결과 JSON의 `filter_composition.note`에도 적었다.
- arm마다 결과 파일이 하나씩 늘어난다(이번 세션 5개).

### Risks

- **층 A는 n=14다.** 1건이 약 7%p이므로 **방향은 읽되 크기는 신뢰하지 않는다**
  (ADR-022 Consequences, ADR-005 Amendment §5).
- 선택도가 가장 높은 도메인을 고르는 규칙은 **코퍼스 분포에 의존한다.** 2단계 220건이
  들어오면 같은 케이스에서 다른 축이 선택될 수 있고, 그러면 arm 간 연속 비교가 끊긴다.
  그때는 선택된 축을 골든셋에 `filter_axis`로 고정해야 한다.

## Implementation

- [x] `scripts/probe_retrieval.py` — `--arm` / `--null-policy`, 필터 값 유도, 층별 집계
- [x] `score_gap_canonical` (hardest / mean / n / golden_set_version) — ADR-005 Amendment 2 후속
- [x] 걸러진 문서의 출신 구성 · 필터 후 밀어내기 재분해 · ko 단문 잔존 수 출력
- [x] 5개 조건 측정 (`docs/eval/probe-retrieval-session-14-*.json`)
- [x] `src/tools/retrieval.py` 변경 0줄 유지
- [ ] **플래너가 질의에서 필터 값을 고르는 경로** — 오라클을 걷어내야 실제 이득을 안다
- [ ] 테스트: 프로브 스크립트에는 아직 단위 테스트가 없다 (기존 177건 기준선 유지)

## Reversibility

- **Reversible:** Yes
- **Rollback:** `--arm none`이 기본값이라 아무 인자도 주지 않으면 session-13 baseline과
  같은 측정이다. 실제로 그렇게 재확인했다 — 케이스별 순위·점수가 전건 일치했다.
- **Migration Cost:** Low — 프로브 스크립트 1개. 코퍼스·인덱스·운영 코드 변경 없음.

## Review Trigger

- **2단계 220건 반입으로 재인덱싱이 필요해지는 시점** — 필터를 Chroma `where` 절로
  배선할지 다시 본다. 동시에 선택도 기반 축 선택 규칙이 새 분포에서도 같은 축을
  고르는지 확인한다.

## References

- **Related ADR:** ADR-022(층화·후처리 필터 경계·null 희석률 예측), ADR-005 + Amendment 2
  (간격 표기 규칙과 후속 체크박스), ADR-020(온톨로지 메타 적재 경계 · Chroma 부분 일치 부재),
  ADR-002(한 번에 하나만 바꾼다), ADR-018(출력 계약 §8.2 필터 배선 경계),
  ADR-012 Amendment(언어 효과)
- **Documentation:** `docs/handoff/session-14.md`,
  `docs/eval/probe-retrieval-session-14-{baseline-nofilter,release-type-strict,release-type-nullpass,tech-domain-strict,tech-domain-nullpass}.json`,
  `docs/handoff/session-13.md` §5·§6.1
