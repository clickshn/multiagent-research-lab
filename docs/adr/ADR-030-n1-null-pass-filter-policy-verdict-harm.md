# ADR-030: v1.2-N1 판정 — null-통과 필터 정책도 해악(D5)이다. 도구 기본값은 off를 유지하고 필터 기본 정책은 strict로 둔다

- **Status:** Accepted (2026-10-01, session-24 — 사용자 결정)
- **Date:** 2026-10-01
- **Decision:** 사전 등록 판정은 **해악**이다(D5: 층 C "근거 없음 유지" 7/7 → 6/7, GS-027).
  그래서 Researcher `tech_domain` 선택 도구의 기본값을 **off로 유지**한다(ADR-029 결론 유지).
  `retrieval.search`의 필터 정책 인자(`filter_policy`)는 남기되 **기본값은 strict**다.
  null-통과는 실험용(`bench --filter-policy null-pass`)으로만 쓴다.
- **Scope:** multiagent-research-lab (`src/tools/retrieval.py` 필터 정책 · `src/orchestrator/nodes.py` 배선 · v1.2-N1)
- **Decision Source:** Human. 판정 규칙은 사전 등록 `5122e97`이다(정의·임계값은 T3a `ce86b04` + Amendment 1 그대로). "해악이면 off 유지"는 사용자가 측정 전에 지시했다.

---

## Context

### Problem

ADR-029(T3a)는 에이전트가 고른 strict 필터를 해악(D4: 층 B 5/9 → 0/9)으로 판정했다.
원인은 **strict 정책 × 층 B 정답 문서의 메타 부재**였다. N1의 질문은 하나다.
**"메타가 없는 문서를 필터에 통과시키면(null-통과) 해악이 사라지는가."** 그 밖의 조건은 T3a와 같다.

### Constraints

- **바뀐 변수는 필터 정책 하나다(ADR-002).** 선택 프롬프트·스키마·선택 시점(1회차만, D1)·후속 노드는 그대로다.
- ⚠️ **선택 프롬프트의 서술이 null-통과에서는 엄밀하지 않다.**
  - 프롬프트는 "고른 영역으로 문서 검색이 **제한된다.** 그 영역으로 분류되지 않은 문서는 후보에서 빠진다"고 말한다.
  - null-통과에서는 **분류되지 않은(메타 없는) 문서는 빠지지 않는다.**
  - 그래도 프롬프트를 고치지 않았다. 고치면 변수가 둘이 되어, 결과 변화가 어느 쪽에서 왔는지 가를 수 없다.
  - 결과적으로 선택 호출의 입력은 T3a와 같다. 층별 선택값 분포(A·B·C)도 T3a와 같았다.
- **메타가 없는 문서는 14건이다(코퍼스 37건 중).**
  - 정의는 `tech_domains`가 비어 있는 문서다. 현재 인덱스에서는 `has_ontology=False`와 같은 집합이다.
  - 14건 모두 스냅샷 arXiv다.
  - 층 B 9케이스의 정답은 전부 이 14건 안에 있고(10건 사용), 층 A 정답은 하나도 없다.
  - 그래서 null-통과에서는 어떤 필터 값을 골라도 생존자가 +14건이다. 예: `Agent` 7 → 21.
- n=14/9/7이다. 결론의 범위는 현재 인덱스, 코퍼스 37건(메타 없는 14건), 임베딩 핀, `gemma-4-31B-it`로 한정한다.

## Decision

### Selected

- **Technology:** 추가 의존성 없음.
- **Architecture:** `ChromaRetriever.search(..., filter_policy="strict" | "null_pass")`
  - 기본값은 strict다(ADR-027 경로 그대로).
  - null_pass는 `값 ∈ tech_domains` **또는** `tech_domains`가 빈 문서를 남긴다. 생존자 순서는 무필터 순위 그대로다.
- **Implementation:**
  - 스코프와 노드는 null_pass일 때만 정책을 하위 호출과 span에 싣는다.
  - bench `--filter-policy`가 결과 JSON에 정책을 기록한다.
  - `compare_bench_runs`는 정책이 다르면 거부한다(`--allow-policy-mismatch`로만 허용).
  - 분석 스크립트에는 정책 인자만 추가했다(정의·임계값 무변경).
  - 1단계 재생 스크립트는 `scripts/replay_n1_null_pass.py`다.
  - 기본값을 바꾸는 코드 변경은 없다.

## Rationale

1. **D5 해악이다.**
   - 층 C "근거 없음 유지"가 7/7 → 6/7이다. 임계값은 1건이다.
   - 사전 등록 §6은 D3·D4·D5 중 하나라도 해악이면 종합 판정을 해악으로 한다.
2. **해악의 경로는 사전 등록 §2가 경고한 null 희석 그대로다.**
   - GS-027(negative, "LLM 에이전트 기반 공장 설비 제어 사례")의 #0 항목은 `Agent`를 골랐다.
   - 메타 없는 `arXiv:2405.16887v2`("A Large Language Model-based multi-agent manufacturing system…", 층 B GS-003의 정답)가 후보 4위로 들어왔고, Researcher가 그것을 근거로 인용했다.
   - 이 문서는 p1-on(무필터)과 T3a(strict)의 GS-027 후보에는 없었다.
   - null-통과는 메타 없는 문서를 **필터 값과 무관하게** 모든 필터 생존자에 넣는다. 그래서 필터가 무관한 문서를 걸러주지 못할 뿐 아니라, 무필터에서는 top-4에 들지 못하던 문서를 끌어올린다.
   - 끌어올림의 구조: 필터는 다른 도메인의 **메타 있는** 문서(GS-027 #0에서는 무필터 2·3위 `news:42f467…`·`news:c076df…`)를 지워 자리를 비운다. 메타 없는 문서는 지워지지 않으므로 그 빈자리로 올라온다.
3. **층 B 해악은 사라졌다. 그러나 판정을 바꾸지 않는다.**
   - 층 B 검색 해악은 12/12 → 0/12이고, e2e는 p1-on 5/9 → 7/9(Δ +2, fail→pass GS-007 · GS-025)다.
   - D4 기준에서는 해악이 아니다. 그러나 D5가 해악이므로 종합 판정은 해악이다.
   - D7("개선 관찰")은 **해악이 아닐 때만** 적는다. 게다가 층 A fail→pass는 1건(GS-023)이라 수치 조건도 충족하지 않는다.
4. 사용자가 측정 전에 "해악이면 off 유지"를 지시했다. 기본값을 켤지 묻는 분기(무해일 때)는 발동하지 않았다.

## Evidence

- **1단계 — 검색 재생 (LLM 0건, `n1-replay-null-pass.json`, `36600f9`)**
  - **입력:** T3a 1회차 선택값과 `search_text`를 그대로 썼다. strict 재생은 T3a 기록과 99/99 같았다(무결성).
  - **양성 대조:**
    - GS-001·GS-011의 1회차 항목 전부와 대조군 GS-010#0에 호환되지 않는 값을 심었다.
    - 심은 적격 6항목을 **6/6 해악으로 검출**했고, 해악 케이스는 정확히 2개였다.
    - 대조군은 검출하지 않았고, 관문은 "e2e 하지 않음"으로 판정했다. → 통과
  - **결과:** 해악 층 A **0/33(0/12 케이스)**, 층 B **0/12(0/5)**. 사전 등록 §3의 구조적 예측과 같다. → **관문 통과**
  - **관찰:**
    - 층 B 이득은 8항목이다.
    - 층 A 이득은 strict 12 → 9항목으로 줄었다. **GS-010을 잃었다.** 1회차 3항목 모두에서 메타 없는 문서 4건이 정답(strict 1위)을 top-4 밖으로 밀어냈다.
    - top-4에 들어온 메타 없는 문서는 A 66건(정답 0), B 80건(정답 24), C 41건이다.
- **2단계 — e2e `bench-v1.2-n1-null-pass` (`eb222ca`)**
  - **조건:** 30건 × 1회, 도구 on, null-통과, parallel on(4), `--require-trace`, 캐시 끔, 인덱스 queue 37 == 문서 37(clean)
  - **측정 유효:**
    - 무결성 문제 0, 귀속 불일치 0(필터 span 정책 대조 포함)
    - `selection_error` 0, 스키마 위반 0, `FilterValueError` 0, 엔드포인트 오류 0
    - 1회차 Outliner 항목은 30/30 케이스에서 p1-on과 같다.
  - **엔드포인트:** `VLLM_BASE`(`***.ktcloud.com`, fp `1897f0ecc081`), 논리 호출 401건(기대 ≈396, 상한 810), 0원(할당분)

  | 지표 | 층 A (n=14) | 층 B (n=9) | 층 C (n=7) |
  |---|---|---|---|
  | **해악 항목 / 적격** | **0/33** | **0/12** | — |
  | 해악 케이스 / 적격 | 0/12 | 0/5 | — |
  | 이득 항목 | 9 | 8 | — |
  | **e2e** p1-on → N1 | 12/14 → 13/14 (Δ +1) | **5/9 → 7/9 (Δ +2건)** | **근거 없음 유지 7/7 → 6/7** |
  | 뒤집힘 | f→p GS-023 | f→p GS-007 · GS-025 | **p→f GS-027** |
  | (관찰) T3a → N1 | 14/14 → 13/14 (GS-010 p→f) | 0/9 → 7/9 | 7/7 → 6/7 |
  | 정확 / 호환 일치 | 20/45 / **45/45** | — | — |
  | 기권 | 0/45 | 0/32 | 3/22 |

  - D3 아님(층 A 해악 케이스 0) · D4 아님(+2) · **D5 해악(하락 1)** → **종합 해악**
- **Cost (기록만):** 호출 297 → 401(+104: `researcher_select` +99, `verifier` +16, `researcher` −11), 토큰 +60,209, wall mean +1.06s

## Alternatives

### null-통과 + 도구 기본 on

- **Pros:** 층 B e2e +2(5/9 → 7/9), 층 B 검색 해악 0, 층 A 해악 0
- **Cons:** 층 C negative 1건 하락(GS-027 — 무관한 메타 없는 문서를 근거로 인용). T3a보다 층 A 이득이 줄었다(GS-010 상실).
- **Rejected because:** 사전 등록 D5 해악 판정이다. 판정이 해악이면 off를 유지한다는 사용자 지시도 있다.

### 선택 프롬프트를 null-통과에 맞게 고친다

- **Rejected because:** 이번 측정에서는 변수가 둘이 된다(ADR-002). 고치려면 새 사전 등록이 필요하다.

### 현행 유지 (도구 기본 off, 필터 기본 strict) — **채택**

- **Pros:** 기본 경로가 p1-on과 같다(입력 해시 297/297, session-22). strict 경로는 이번 변경 뒤에도 T1 프로브 바이트 동일, p1 재생 340/340이다.
- **Cons:** 층 B 회복(+2)과 층 A 이득을 기본 경로에서 쓰지 않는다.

## Consequences

### Positive

- strict와 null-통과의 해악이 **서로 다른 층에서** 실측으로 고정됐다.
  - strict는 메타 없는 정답을 지운다 → 층 B
  - null-통과는 메타 없는 무관 문서를 끌어올린다 → 층 C
- 필터 정책이 결과·트레이스·대조 도구에 기록된다. 정책이 다른 회차를 섞어 비교하는 실수를 도구가 막는다.

### Negative

- `filter_policy` 인자와 null-통과 경로는 기본 off 상태로 남는다. 유지 비용이 있다.

### Risks

- **이 결론은 코퍼스 구성에 강하게 의존한다.** 메타 없는 문서 14건이 전부 "멀티에이전트·에이전트 벤치마크" 계열이라 `Agent`를 고른 항목에서 희석이 특히 크다. 메타 없는 문서의 비율·주제가 바뀌면 결론은 이어지지 않는다.
- 층 C 하락은 **1건(n=7)** 이다. 사전 등록 D5는 1건이라도 해악으로 정했으므로 판정은 해악이다. 그러나 이것을 "null-통과가 negative를 일반적으로 깨뜨린다"로 일반화하지 않는다.
- **사후 관찰(판정에 쓰지 않음):**
  - GS-002는 null-통과에서 **pass**다(인용 `arXiv:2602.03128v1` = 정답, `some_topic_covered=true`). T3a의 "인용은 있는데 전 항목 근거 없음"(`arXiv:2412.05449v1`)은 **재현되지 않았다.** 원인은 분석하지 않았다.
  - 층 B fail은 GS-006 · GS-020이다(p1-on에서도 fail).
  - 층 A GS-010은 1단계에서 예고된 밀어냄 그대로 fail이다(p1-on에서도 fail, T3a에서는 pass).
- ADR-029의 Risk "사용자가 필터를 명시하는 경로에서도 메타 없는 문서는 strict로 탈락한다"는 그대로 남는다. null-통과는 그 대안이 아니라 **다른 해악을 가진 정책**이다.

## Reversibility

- **Reversible:** Yes
- **Rollback:** `filter_policy` 기본값이 strict이므로 되돌릴 기본 동작 변경이 없다. 인자를 지우려면 `103b643`을 되돌린다.
- **Migration Cost:** Low

## Review Trigger

- 메타 없는 문서에 온톨로지 메타를 보강하거나(층 B 스냅샷 export), 메타 없는 문서의 비율이 바뀔 때 strict·null-통과를 다시 잰다.

## References

- **Related ADR:** ADR-029(T3a 판정 해악 D4) · ADR-027(strict 필터 배선) · ADR-028(json_schema 선택) · ADR-023(null-통과 프로브 arm · null 희석) · ADR-022(층 분리) · ADR-002(한 번에 하나)
- **Documentation:**
  - 사전 등록 `docs/plans/v1.2-n1-preregistration.md` — `5122e97`
  - 구현 `103b643` (정책 인자 · bench · 대조 · 분석 인자 · 1단계 재생 스크립트)
  - 1단계 `docs/eval/n1-replay-null-pass.json` — `36600f9`
  - 본측정·판정 `docs/eval/bench-v1.2-n1-null-pass.json`, `docs/eval/n1-analysis-v1.2-n1-null-pass.json` — `eb222ca`

## AI/ML Details

- **Model:** `gemma-4-31B-it` (KT Cloud vLLM, `VLLM_BASE`), temperature 0. 선택 호출은 json_schema, max_tokens 64다.
- **Evaluation:** 골든셋 2.0, 30건(층 A 14 · B 9 · C 7). 해악은 N1 트레이스 `search_text`를 무필터로 재생해 쟀다.

### Evaluation

| Metric | Before (p1-on) | After (N1) | Target |
| ------ | -----: | ----: | -----: |
| 층 A e2e pass | 12/14 | 13/14 | 해악 케이스 < 2 (D3) |
| 층 B e2e pass | 5/9 | 7/9 | Δ > −2건 (D4) |
| 층 C 근거 없음 유지 | 7/7 | 6/7 | 하락 0 (D5) |
| 층 A 해악 항목 | — | 0/33 | — |
| 층 B 해악 항목 | — | 0/12 | — |
| 논리 호출 | 297 | 401 | 기록만 |
