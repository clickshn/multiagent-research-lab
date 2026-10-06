# ADR-029: v1.2-T3 판정 — 에이전트가 고른 tech_domain 필터는 해악(D4)이다. 도구 기본값은 off로 두고, 필터는 사용자가 명시할 때만 건다

- **Status:** Accepted (2026-10-01)
- **승인 메모:** session-23 — 사용자 결정
- **Date:** 2026-10-01
- **Decision:** 사전 등록 판정이 **해악**이다(D4: 층 B e2e 5/9 → 0/9, Δ −5건). 그래서 Researcher `tech_domain` 선택 도구의
  기본값을 **off로 유지**한다(`RESEARCH_TECH_DOMAIN_TOOL` 비움 = off). **`tech_domain` 필터는 사용자가 명시할 때만 건다.**
  에이전트가 스스로 고르게 하지 않는다.
- **Scope:** multiagent-research-lab (`src/orchestrator/nodes.py` 선택 도구 · `src/providers/config.py` 도구 설정 · v1.2-T3)
- **Decision Source:** Human (판정 규칙은 사전 등록 `ce86b04` + Amendment 1 `49d0b66`. "해악이면 off 유지, 결론은 '필터는 사용자가 명시할 때만 건다'"는 사용자가 측정 전에 지시)

---

## Context

### Problem

v1.2-T2(ADR-028)는 Researcher가 1회차 항목마다 `tech_domain` strict 필터 값을 고르게 배선했다. 기본값은 off다.
T3의 질문은 하나다. **"에이전트가 고른 필터를 켜 두면 결과를 해치는가."** 판정은 측정 전에 등록한 기준(D1~D7)을 그대로 쓴다.

### Constraints

- **판정 규칙은 데이터를 보기 전에 고정했다.**
  - 사전 등록: `docs/plans/v1.2-preregistration.md` `ce86b04`. Amendment 1(`49d0b66`)은 적격 분모 서술만 정정했고(top-5 → top-4) 정의와 임계값은 바꾸지 않았다.
  - 분석 스크립트(`scripts/analyze_t3_selection.py`, `dce0039`)는 본측정 전에 커밋했다.
  - 이 스크립트를 세 가지로 검증했다: p1-on 자기 대조(해악 0), T2 소표본 3건, 양성 대조(변조 사본에 심은 해악 전부 검출).
- 층 합산 수치를 만들지 않는다(ADR-022). 층 B는 건수 차이로 적는다.
- n=14/9/7이다. 결론의 범위는 현재 인덱스, 코퍼스 37건, 임베딩 핀, `gemma-4-31B-it`로 한정한다(사전 등록 §7).
- **층 B 해악은 사전 등록 §3.4가 예고한 구조다.**
  - 층 B 정답 문서(스냅샷 arXiv)에는 온톨로지 메타가 없다. 그래서 strict 필터에서 **반드시** 탈락하고, 무해한 선택은 기권("없음")뿐이다.
  - 실제로는 층 B 32항목 중 **기권 0건**이었다. 선택기는 "이 항목이 어느 도메인인가"에 답하고, 정답 문서에 메타가 있는지는 알 수 없다(§3.6 "반" 근거와 같은 이유).
  - 그 결과 층 B 9케이스는 **전 항목이 근거 없음**으로 끝났다(`some_topic_covered=false` 9/9).

## Decision

### Selected

- **Technology:** 변경 없음. 선택 도구 코드(json_schema 선택, ADR-028)와 strict 필터 경로(ADR-027)는 그대로 둔다.
- **Architecture:** 에이전트가 필터 값을 고르는 경로는 **기본 off**다. 필터는 사용자가 값을 명시한 검색에만 건다.
- **Implementation:**
  - 기본값을 바꾸는 코드 변경은 없다(T2부터 off).
  - `--tech-domain-tool on`과 `RESEARCH_TECH_DOMAIN_TOOL=on`은 실험용으로 남긴다.

## Rationale

1. **D4 해악이다.**
   - 층 B e2e가 5/9 → 0/9(Δ −5건)이다. 임계값은 −2건이다.
   - pass→fail 케이스는 GS-002 · GS-003 · GS-004 · GS-005 · GS-019이고, 사전 등록 §6.1은 하나라도 해악이면 종합 판정을 해악으로 한다.
2. **원인은 선택 오류가 아니라 strict 정책과 층 B 메타 부재의 조합이다.**
   - 층 B 적격 12항목(5케이스)이 **전부** 해악이다. 무필터에서 정답이 1~4위였는데, 필터 뒤 후보에서는 사라졌다.
   - 해악 12항목은 **전부** `Agent`(필터 생존 7건)를 골랐다. 층 B의 다른 선택값(`Eval/Governance` 5 · `Safety/Alignment` 4)은 적격 항목이 아니었다.
   - 층 A에서는 같은 선택기가 **호환 45/45**를 냈다. 즉 선택기는 "합리적인 도메인"을 골랐다. 정답 문서 쪽에 그 도메인 표시가 없을 뿐이다.
3. **층 A 결과는 판정을 바꾸지 않는다.**
   - 층 A 해악은 0/33(0/12 케이스)이고, e2e는 12/14 → 14/14(fail→pass GS-010 · GS-023)다.
   - 그러나 D7 "개선 관찰"은 **해악이 아닐 때만** 적는다(§6 D7, §6.1). 그래서 기록하지 않는다. 아래 Evidence에 사후 관찰로만 남긴다.
4. 사용자가 측정 전에 "해악이면 off 유지, 결론은 '필터는 사용자가 명시할 때만 건다'"로 지시했다. 기본값을 켤지 묻는 분기(무해일 때)는 발동하지 않았다.

## Evidence

- **Experiment:** `bench-v1.2-t3-tool-on`. 조건은 30건 × 1회, 도구 on, parallel on(4), `--require-trace`, 캐시 끔, 인덱스 queue 37 == 문서 37(clean)이다.
  비교 기준 `bench-v1.2-p1-on`도 parallel on(4), 캐시 끔이다.
  - **측정 유효:** 무결성 문제 0건. 기록 후보 = 트레이스이고, 필터·무필터 재생 = 기록이다.
  - 귀속 불일치 0, `selection_error` 0, 스키마 위반 0, `FilterValueError` 0, 엔드포인트 오류 0.
  - 1회차 Outliner 항목은 30/30 케이스에서 p1-on과 같다.
  - 엔드포인트 `VLLM_BASE`(`***.ktcloud.com`, fp `1897f0ecc081`), 논리 호출 388건(상한 810), 비용 0원(할당분)
- **Benchmark (사전 등록 지표, 층별):**

  | 지표 | 층 A (n=14) | 층 B (n=9) | 층 C (n=7) |
  |---|---|---|---|
  | **해악 항목 / 적격 항목** | **0/33** | **12/12** | — |
  | 해악 케이스 / 적격 케이스 | 0/12 | **5/5** | — |
  | 이득 항목 (기록용) | 12 (6케이스) | 0 | — |
  | **e2e** (p1-on → T3) | 12/14 → 14/14 (Δ +2) | **5/9 → 0/9 (Δ −5건)** | 근거 없음 유지 7/7 → 7/7 |
  | 정확 일치 (항목 풀링, 기록만) | 20/45 (케이스 과반 6/14 관찰) | — | — |
  | 호환 일치 (항목 풀링) | **45/45** (케이스 과반 14/14) | — | — |
  | 기권 | 0/45 | **0/32** | 3/22 (모두 GS-008) |

- **Cost (기록만, 판정에 쓰지 않음):**
  - 호출: 297 → 388(+91)
    - `researcher_select` +99
    - `researcher` +4
    - `verifier` −12. 층 B가 후보 없이 근거 없음으로 끝나 검증 호출이 줄었다.
  - 토큰: 313,041 → 348,270(+35,229)
  - wall mean: 8.45 → 7.36s(−1.09s). 층 B·C 케이스가 짧게 끝난 효과다. **효율 개선이 아니다.**

## Alternatives

### 선택 도구 기본값 on

- **Pros:**
  - 층 A 해악 0, 호환 45/45, 층 A e2e +2(GS-010 · GS-023)
  - 층 A 이득 12항목 중 GS-010 · GS-023의 정답은 무필터 top-4 밖에 있었는데, 필터 뒤 1~3위로 들어왔다.
- **Cons:** 층 B e2e −5건. 메타가 없는 문서가 정답인 질의는 구조적으로 근거 없음이 된다.
- **Rejected because:** 사전 등록 D4 해악 판정이다. 판정이 해악이면 off를 유지한다는 사용자 지시도 있다.

### 현행 유지 (기본 off, 에이전트 선택 없음) — **채택**

- **Pros:** p1-on 경로와 입력 해시가 297/297 같다(session-22). 층 B 5/9를 보존한다.
- **Cons:** 층 A에서 관찰된 이득(fail→pass 2건)을 얻지 못한다.

## Consequences

### Positive

- 기본 경로가 p1-on과 같게 유지된다. 비교선이 끊기지 않는다.
- "메타가 없는 문서를 strict 필터가 지운다"는 위험이 실측 수치(층 B 12/12 해악)로 고정됐다.

### Negative

- 층 A에서 필터가 정답을 끌어올린 효과(GS-010 · GS-023)를 기본 경로에서 쓰지 않는다.
- 선택 도구 코드는 기본 off 상태로 남는다. 유지 비용이 있다.

### Risks

- **사후 관찰(사전 등록 기준 밖, 판정에 쓰지 않음):**
  - 층 A fail→pass 2건은 D7 수치 조건을 충족한다. 하지만 종합 판정이 해악이라 "개선 관찰"로 적지 않는다.
  - 층 C 기권 3건은 모두 GS-008의 3항목이다.
  - GS-028은 3항목 모두 생존 1건(`Inference/Serving`)으로 좁혀졌는데도 근거 없음을 유지했다.
  - GS-002는 인용 문서(`arXiv:2412.05449v1`)가 기록됐는데 전 항목이 근거 없음이었다. 원인은 확인하지 않았다.
- 층 A 효과를 "필터가 낫다"로 일반화하면 안 된다. 사전 등록 §6 D7은 효과 입증이 아니라고 정했다. n=14다.
- 사용자가 필터를 명시하는 경로에서도 **메타가 없는 문서는 strict 정책상 탈락한다.** 사용자가 이 사실을 모르고 필터를 걸면 같은 해악이 생길 수 있다.

## Reversibility

- **Reversible:** Yes
- **Rollback:** `RESEARCH_TECH_DOMAIN_TOOL=on` 또는 bench `--tech-domain-tool on`. 코드 변경이 필요 없다.
- **Migration Cost:** Low

## Review Trigger

- 대화에 명시된 재검토 조건은 없다.

## References

- **Related ADR:** ADR-028(json_schema 선택) · ADR-027(strict 필터 배선) · ADR-022(층 분리) · ADR-025 Amendment(최소 차이 하한) · ADR-002(한 번에 하나)
- **Documentation:**
  - 사전 등록 `docs/plans/v1.2-preregistration.md` — `ce86b04`(등록), `49d0b66`(Amendment 1)
  - 분석 스크립트·검증 `scripts/analyze_t3_selection.py`, `docs/eval/t3-analysis-{validate-p1-on,validate-t2-pathcheck,positive-control}.json` — `dce0039`
  - 본측정·판정 `docs/eval/bench-v1.2-t3-tool-on.json`, `docs/eval/t3-analysis-v1.2-t3-tool-on.json` — `4ac79bf`

## AI/ML Details

- **Model:** `gemma-4-31B-it` (KT Cloud vLLM, `VLLM_BASE`), temperature 0. 선택 호출은 `response_format` json_schema, max_tokens 64다.
- **Evaluation:** 골든셋 2.0, 30건(층 A 14 · B 9 · C 7). 해악은 트레이스 `search_text`를 무필터로 재생해 쟀다. LLM 재호출은 없다.
- **Inference:** 1회차 항목당 선택 1회, 재시도는 같은 값을 재사용한다(D1). 1회차 99항목, 재사용 66항목.

### Evaluation

| Metric | Before | After | Target |
| ------ | -----: | ----: | -----: |
| 층 A e2e pass | 12/14 | 14/14 | 해악 케이스 < 2 (D3) |
| 층 B e2e pass | 5/9 | 0/9 | Δ > −2건 (D4) |
| 층 C 근거 없음 유지 | 7/7 | 7/7 | 하락 0 (D5) |
| 층 A 해악 항목 | — | 0/33 | — |
| 층 B 해악 항목 | — | 12/12 | — |
| 논리 호출 | 297 | 388 | 기록만 |
