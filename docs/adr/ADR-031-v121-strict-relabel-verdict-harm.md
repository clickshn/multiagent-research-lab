# ADR-031: v1.2.1 판정 — 메타를 다 채운 코퍼스에서도 strict + 선택 도구는 해악(D5)이다. 도구 기본값은 off를 유지한다

- **Status:** Accepted (2026-10-16)
- **승인 메모:** session-27 — 판정은 사전 등록 `9454c23`(사용자 승인 후 커밋)의 규칙으로 기계적으로 나왔다. "해악이면 off 유지"는 사전 등록 §10에 측정 전에 적혀 있었다. 기본값을 바꾸지 않으므로 새 사용자 결정이 필요하지 않다
- **Date:** 2026-10-16
- **Decision:** 사전 등록 판정은 **해악**이다(D5: 층 C "근거 없음 유지" 7/7 → 6/7, GS-027).
  메타가 없는 문서를 없앤 코퍼스(37건 전부 `gemma-4-31B-it` 재추출)에서도 Researcher `tech_domain` 선택 도구의 기본값을 **off로 유지**한다.
  필터 기본 정책은 strict 그대로다(ADR-029·030 결론 유지). 코퍼스는 새 export(`ontology-20261002-114500`)로 **교체한 상태를 유지**한다 — 무필터 기본 경로의 검색은 이전과 바이트 동일하다(340/340).
- **Scope:** multiagent-research-lab (코퍼스 메타 · 선택 도구 기본값 · v1.2.1)
- **Decision Source:** Human(사전 등록 승인) + 사전 등록 규칙. 라벨 감사는 **Claude(Claude Code)의 독립 감사이고 사람의 감사가 아니다.**

---

## Context

### Problem

ADR-029(T3a, strict)와 ADR-030(N1, null-통과)은 둘 다 해악이었고, 두 ADR 모두 근본 원인을 **온톨로지 메타가 없는 문서 14건**으로 적었다.
ADR-030 Review Trigger는 "메타를 보강하면 다시 잰다"였다. 생산자가 37건 전체를 단일 모델로 재추출했다(생산자 ADR-026).
v1.2.1의 질문은 **"메타 부재를 없애면 strict + 선택 도구가 무해해지는가"**다.

### Constraints

- **바뀐 변수는 코퍼스 메타 하나다(ADR-002).** 정책(strict)·선택 도구·프롬프트·후속 노드는 T3a와 같다.
  메타는 어떤 LLM 입력에도 들어가지 않으므로(ADR-024) 라벨은 필터를 거쳐서만 결과에 닿는다.
- ⚠️ 라벨 **두 묶음**이 함께 바뀌었다: (i) 메타가 없던 14건에 라벨이 생겼고, (ii) 원래 있던 23건이 Opus → gemma로 바뀌었다
  (생산자 session-20 §4.1: `tech_domains` 완전 일치 9/23). 이 측정으로 둘을 나눌 수 없다.
- 층의 케이스 집합은 v1.2와 같다. 층 B의 뜻만 "v1.2.1에서 메타가 처음 붙은 앵커"로 다시 선언했다(사전 등록 §2.1, ADR-022 Amendment).
- 라벨의 정확도는 사람이 확인하지 않았다. Claude가 14건(+ 층 A 앵커 4건의 바뀐 값)을 감사했다(사전 등록 §1).
- n=14/9/7, 조건당 1회. 결론의 범위는 현재 인덱스·코퍼스 37건(전부 gemma 메타)·임베딩 핀·`gemma-4-31B-it`·**이 라벨러**로 한정한다.

## Decision

### Selected

- **Technology:** 추가 의존성 없음.
- **Architecture:** 코드 경로 변경 없음. 도구 기본 off(`RESEARCH_TECH_DOMAIN_TOOL`), 필터 기본 strict.
- **Implementation:**
  - 코퍼스: 새 export 반입, 옛 JSONL은 `data/corpus-archive/`(ADR-019 Amendment). `f2ab348`
  - 분석: `analyze_t3_selection.py --oracle`(기본값 무변경, 재인덱싱 전 T3a 회귀 동일). 새 라벨 오라클 `docs/eval/v1.2.1-oracle.json`
  - 해석: `scripts/interpret_v121_labels.py`(사전 등록 §8 분류) — 판정과 무관
  - 동일성 관문: `scripts/check_index_meta_v121.py`(I2·I3)

## Rationale

1. **D5 해악이다.** 층 C "근거 없음 유지"가 7/7 → 6/7(GS-027)이다. 임계값은 1건이다. 사전 등록 §7은 D3·D4·D5 중 하나라도 해악이면 종합 판정을 해악으로 한다.
2. **메타 부재가 사라지자 strict의 층 B 해악(D4)은 사라졌다.** 층 B 검색 해악 0/12, e2e 5/9 → **7/9**(Δ +2). T3a(0/9) 대비 +7은 **라벨 커버리지 효과**다.
3. **그러나 GS-027의 해악은 메타 부재가 원인이 아니었다.** N1과 같은 문서·같은 경로로 재현됐다:
   - GS-027(negative, 근접 유인 — 골든셋 rationale이 "`arXiv:2405.16887`이 강하게 끌린다"고 적어 둔 케이스)의 #0 항목("LLM 에이전트 기반 공장 설비 제어 사례")은 `Agent`를 골랐다.
   - 필터가 무필터 2·3·4위(`news:42f467…`·`news:c076df…`·`news:0252e9…`, `Agent` 라벨 없음)를 지웠고, **`Agent` 라벨이 새로 붙은** `arXiv:2405.16887v2`(층 B GS-003의 정답)가 3위로 올라와 인용됐다.
   - N1에서는 "메타가 없어서" 통과했고, v1.2.1에서는 "`Agent`가 붙어서" 통과했다. **문서를 끌어올린 것은 두 경우 모두 필터의 재배열이다.**
   - 감사는 이 문서의 `Agent`를 **맞음**으로 판정했다. 즉 라벨이 틀려서 생긴 해악이 아니다. **정확한 라벨이 같은 해악을 재현한다.**
4. **해석 규칙(사전 등록 §8)의 결과:** 층 A·B 검색 해악 항목 0 → L− 0 · S 0 · U 0, L+ 0. 층 C 하락은 해악 항목 없이 생긴 e2e 뒤집힘이므로
   규칙대로 **"필터가 후보 풀을 바꾼 것이 Researcher·Verifier에 준 영향"**으로 적고, 라벨 정확도로 귀속하지 않는다.
5. 사전 등록 §10: 해악이면 도구 기본 off를 유지한다.

## Evidence

- **동일성 관문 (재인덱싱 직후, LLM 0건)** — 전부 통과
  - I1 p1 검색어 재생 **340/340** (p1-on 161 · p1-off 161 · pathcheck 18 — 문서 ID·순위·**점수까지** 허용오차 0). `v1.2.1-identity-replay-p1.json`
  - I2 계약 §12.2-13: GeekNews 10건이 `lang="ko"`로 조회 10건 — **생산자 자체검사 13번 PENDING → PASS**. `v1.2.1-index-meta-check.json`
  - I3 37건 전부 메타, `gemma-4-31B-it`, `prompt_sha256 25b17f44…`
  - I4 분석기 양성 대조 통과(새 인덱스·새 오라클). `v1.2.1-analysis-positive-control.json`
- **e2e `bench-v1.2.1-strict-relabel`** (`2260efb`)
  - **조건:** 30건 × 1회, 도구 on, strict, parallel on(4), `--require-trace`, 캐시 끔, 인덱스 queue 37 == 문서 37(clean)
  - **측정 유효:** 무결성 문제 0, `selection_error` 0, 스키마 위반 0, `FilterValueError` 0, 엔드포인트 오류 0, 1회차 Outliner 항목 30/30 p1-on과 같음
  - **엔드포인트:** `VLLM_BASE`(`***.ktcloud.com`, fp `1897f0ecc081`), 논리 호출 410건(기대 ≈400, 상한 810), 0원(할당분)

  | 지표 | 층 A (n=14) | 층 B (n=9) | 층 C (n=7) |
  |---|---|---|---|
  | **해악 항목 / 적격** | **0/33** | **0/12** | — |
  | 해악 케이스 / 적격 | 0/12 | 0/5 | — |
  | 이득 항목 | 12 | 10 | — |
  | **e2e** p1-on → v1.2.1 | 12/14 → **14/14** (Δ +2) | 5/9 → **7/9** (Δ +2건) | **근거 없음 유지 7/7 → 6/7** |
  | 뒤집힘 | f→p GS-010 · GS-023 | f→p GS-007 · GS-025 | **p→f GS-027** |
  | (관찰) T3a → v1.2.1 | 14/14 → 14/14 | 0/9 → 7/9 | 7/7 → 6/7 |
  | (관찰) N1 → v1.2.1 | 13/14 → 14/14 (GS-010) | 7/9 → 7/9 | 6/7 → 6/7 |
  | 정확 / 호환 일치 (새 오라클·새 라벨) | 18/45 / **45/45** | — / **32/32** | — |
  | 기권 | 0/45 | 0/32 | 3/22 |

  - D3 아님(0) · D4 아님(+2) · **D5 해악(하락 1)** → **종합 해악**
  - D7 수치 조건(층 A f→p ≥ 2)은 충족하지만, D7은 해악이 아닐 때만 적는다 → 개선 관찰 **아님**
  - **구조적 예측(사전 등록 §7.1) 확인:** 1회차 선택이 T3a와 **29/30 케이스에서 같았다**(GS-001 #1만 `Eval/Governance` → `Agent`, 둘 다 호환). 층 A·B 해악 0은 예측대로다.
  - 층 B fail은 GS-006 · GS-020이다(p1-on·N1에서도 fail).
- **Cost (기록만):** 호출 297 → 410(+113: `researcher_select` +99, `verifier` +27, `researcher` −13), 토큰 +63,430, wall p50 9.56 → 11.10s · p95 14.40 → 17.73s
- **라벨 감사 (Claude 독립 감사, 사람 아님, `docs/eval/v1.2.1-label-audit.json`):** `Agent` **11/14**(틀림 0 · 애매 3), 정답 문서 10건 중 `Agent` **8/10**(애매 2: GS-006 · GS-003의 #14), `release_type` 8/14(`Benchmark/Report` 5건 중 맞음 0 — 대학 벤치마크 논문), 나머지 도메인 값 19/27(틀림 2). 층 A 앵커 4건 보충 감사에서 새로 붙은 값 3건이 틀림(`Inference/Serving`·`Data/Synthetic`·`Reasoning`).

## Alternatives

### 도구 기본 on (strict)

- **Pros:** 층 A 14/14 · 층 B 7/9(둘 다 p1-on 대비 +2), 검색 해악 0
- **Cons:** 층 C negative 1건 하락(GS-027 — 근접 유인 문서를 근거로 인용)
- **Rejected because:** 사전 등록 D5 해악 판정이다.

### 라벨을 고친다 (감사 결과 반영)

- **Rejected because:** GS-027의 원인 문서 라벨은 감사상 **맞다.** 고칠 대상이 아니다. 그리고 라벨 수정은 측정 대상을 자동 라벨러에서 사람(이 경우 Claude) 라벨로 바꾼다(생산자 ADR-026 Alternatives).

### GS-027을 negative에서 빼거나 채점을 항목 단위로 바꾼다

- **Rejected because:** 측정 뒤에 골든셋·채점을 바꾸면 사후 선택이다(ADR-022). 관찰로만 남긴다(Risks).

### 현행 유지 (도구 기본 off, 필터 기본 strict, 새 코퍼스 유지) — **채택**

- **Pros:** 기본 경로가 p1-on과 같다(무필터 재생 340/340, 메타는 LLM 입력에 없다). 출처 표에서 `미확인`이 사라진다(37건 전부 라벨).
- **Cons:** 층 A·B의 +2를 기본 경로에서 쓰지 않는다. 출처 표의 `release_type`은 감사상 정확도가 낮다(위 Evidence).

## Consequences

### Positive

- **ADR-029·030의 원인 진술을 고친다.** "근본 원인은 메타 없는 14건"은 층 B 해악(D4)에 대해서만 맞았다. 메타를 채우자 D4는 사라졌다.
  층 C 해악(D5, GS-027)은 메타 유무와 무관하게 **필터가 근접 유인 문서를 끌어올리는 경로**에서 나온다 — N1과 v1.2.1에서 같은 문서로 재현됐다.
- 세 정책·코퍼스 조합(T3a · N1 · v1.2.1)이 전부 같은 기준(p1-on)과 같은 판정 규칙으로 기록됐다.

### Negative

- 출처 표(ADR-024)가 이제 37건 전부에 `release_type`을 표시한다. 감사상 `Benchmark/Report` 5/5가 정의와 어긋나므로 **표시값을 근거 등급으로 읽으면 안 된다.**

### Risks

- **GS-027은 근접 negative다.** 인용된 항목("공장 설비 제어 사례")은 그 문서 초록(물리 shopfloor 실험)이 실제로 뒷받침한다. 질의 수준("안전 사고")에서는 근거가 없다.
  골든셋 채점은 "모든 항목이 근거 없음"이어야 negative 유지로 본다. 이 채점이 Outliner의 하위 항목 분해와 맞는지는 **확인하지 않았다** — 바꾸려면 측정 전 사전 등록이 필요하다.
- 층 C 하락은 **1건(n=7)** 이다. 일반화하지 않는다. 다만 같은 케이스·같은 문서로 **두 번째** 관측이다(N1, v1.2.1).
- 라벨 정확도는 Claude의 감사이고, 23건 중 19건(층 A 앵커 9건 포함)은 감사하지 않았다. `news:0252e9…`의 `Agent` 탈락(Opus에는 있었다)은 감사하지 않았다 — 이 탈락이 GS-027에서 `arXiv:2405.16887v2`를 4위에서 3위로 올렸지만, N1에서는 4위로도 인용됐으므로 필요조건은 아니다.
- 결론은 이 라벨러(gemma + v4 프롬프트)의 라벨에 대한 것이다. 사람 라벨이나 다른 모델 라벨에서는 이어지지 않는다.

## Reversibility

- **Reversible:** Yes
- **Rollback:** 코드 기본값 변경이 없다. 코퍼스를 v1.2로 되돌리려면 `f2ab348`을 되돌리고 `build_index.py --reset`(또는 `var/chroma-backup-v1.2-20261016-104338/` 복원).
- **Migration Cost:** Low

## Review Trigger

- 선택 도구를 기본 on으로 바꾸려는 제안이 나올 때 — ADR-009 Review Trigger와 함께 본다.
- 골든셋 negative 채점(항목 단위 vs 질의 단위)이나 근접 negative 설계를 바꿀 때 — GS-027을 다시 잰다.
- 사람 라벨 감사가 이뤄지거나 라벨러가 바뀔 때.

## References

- **Related ADR:** ADR-029(T3a, 해악 D4) · ADR-030(N1, 해악 D5) · ADR-027(strict 배선) · ADR-028(선택 도구) · ADR-024(메타는 LLM 입력 밖) · ADR-022(층 분리, Amendment) · ADR-019(export 교체, Amendment) · ADR-002 · 생산자 ADR-026 · ADR-027
- **Documentation:**
  - 사전 등록 `docs/plans/v1.2.1-preregistration.md` — `9454c23`
  - 계약 문구(S3·S4·§3.3) `2ab074f` · 분석 `--oracle` · 반입·동일성 `f2ab348` · 본측정 `2260efb`
  - 판정 `docs/eval/v1.2.1-analysis-strict-relabel.json` · 해석 `docs/eval/v1.2.1-interpretation.json` · 감사 `docs/eval/v1.2.1-label-audit.json`

## AI/ML Details

- **Model:** `gemma-4-31B-it` (KT Cloud vLLM, `VLLM_BASE`), temperature 0. 선택 호출은 json_schema, max_tokens 64. 라벨러도 같은 모델(생산자, 프롬프트 `extract_ontology.v4.md`).
- **Evaluation:** 골든셋 2.0, 30건(층 A 14 · B 9 · C 7). 해악은 v1.2.1 트레이스 `search_text`를 무필터로 재생해 쟀다.

### Evaluation

| Metric | Before (p1-on) | After (v1.2.1) | Target |
| ------ | -----: | ----: | -----: |
| 층 A e2e pass | 12/14 | 14/14 | 해악 케이스 < 2 (D3) |
| 층 B e2e pass | 5/9 | 7/9 | Δ > −2건 (D4) |
| 층 C 근거 없음 유지 | 7/7 | 6/7 | 하락 0 (D5) |
| 층 A 해악 항목 | — | 0/33 | — |
| 층 B 해악 항목 | — | 0/12 | — |
| 논리 호출 | 297 | 410 | 기록만 |
