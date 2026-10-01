# Session 24 핸드오프 — v1.2-N1 null-통과 필터 정책 → **판정 해악(D5)**

- **날짜:** 2026-10-01
- **범위:** v1.2-N1. 진행 순서는 사전 등록 → 구현 → 1단계 검색 재생(관문) → 2단계 e2e → 판정 → ADR-030이다.
  - 바뀐 변수는 필터 정책(strict → null-통과) 하나다.
  - 선택 프롬프트는 바꾸지 않았다. 프롬프트의 "검색이 **제한된다**"는 null-통과에서 엄밀하지 않다. 이 점은 ADR-030 Constraints에 적었다.
  - 선택 프롬프트, governance.md, `.claude/rules/`, 훅은 바꾸지 않았다. README는 수치 한 줄만 넣었다(최종 정리는 T3b).
- **ADR:** ADR-030 신규 → **Accepted** · ADR-027 Amendment(`filter_policy`) (§9)
- **테스트:** `python -m pytest tests/ -q -rf` → **340 passed, 실패 0**
  - 315 → +25. 신규 `tests/test_filter_policy.py` 25건이다. 기존 bench 결과 테스트에는 `filter_policy` assert 1줄을 추가했다.
  - 전체 실행은 2회 했고, 두 번 다 실패 0이었다.
- **LLM 호출:** **401건**(N1 e2e만). 목적지는 전부 `VLLM_BASE`(`***.ktcloud.com`, fp `1897f0ecc081`)다.
  - 승인 게이트 6항목을 제시하고 사용자 승인을 받았다.
  - 외부 벤더 호출 0건, 유료 리소스·IaC 0건, 캐시 끔. 1단계 재생과 회귀 확인은 LLM 0건이다.
- **시크릿 검사:** `scan_local_secrets.py` 0건(양성 대조 통과, 543 파일). 커밋마다 diff 대조 0건이었다.

## 커밋 순서 (push 하지 않았다)

| 커밋 | 내용 |
|---|---|
| `5122e97` | **사전 등록** `docs/plans/v1.2-n1-preregistration.md` — 세션 첫 커밋, 재생·e2e 전. 사용자 요청으로 두 가지를 추가했다: 1단계의 성격(구조상 해악 0, 구현 확인 단계), 1단계 양성 대조 |
| `103b643` | **구현** — `filter_policy` 인자 · 스코프·노드·그래프 배선 · bench `--filter-policy` · `compare_bench_runs` 정책 거부 · 분석 스크립트 인자 · `scripts/replay_n1_null_pass.py` · 테스트 — 재생 전 |
| `36600f9` | 1단계 결과 `docs/eval/n1-replay-null-pass.json` |
| `eb222ca` | 본측정 `bench-v1.2-n1-null-pass.json` + 판정 `n1-analysis-v1.2-n1-null-pass.json` |
| `d56f3de` | ADR-030 (Proposed) |
| `05f50d5` | README 수치 한 줄 |
| (이 커밋) | 이 핸드오프 |

---

## 1. 메타가 없는 문서 — **14건** (코퍼스 37건 중)

- **정의:** `tech_domains`가 빈 문서. v1.1 프로브 `pass`의 `has_meta`와 같다. 현재 인덱스에서는 `has_ontology=False`와 **같은 집합**이다(어긋나는 문서 0건).
- **구성:** 14건 모두 스냅샷 arXiv다.
  - **층 B 9케이스의 정답은 전부 이 안에 있다**(10건 사용).
  - 층 A 정답은 0건이다.
  - 정답이 아닌 4건: `2406.00215v3` · `2410.09024v3` · `2410.09114v2` · `2512.02230v1`
- **영향:** null-통과에서는 어떤 필터 값이든 생존자가 strict보다 +14건이다(예: `Agent` 7 → 21).
- 전체 목록은 사전 등록 §2에 있다.

## 2. 구현 (`103b643`) — strict 경로 불변 확인

- `ChromaRetriever.search(..., filter_policy="strict" | "null_pass")`
  - 기본값은 strict다.
  - 알 수 없는 정책은 `FilterValueError`로 막는다. 질의 전에 멈춘다.
  - `retrieval_filter` span의 `null_policy`에 정책을 기록한다. null_pass일 때만 `returned_without_meta`를 싣는다.
- 스코프와 노드는 **null_pass일 때만** 정책을 하위 호출과 span에 싣는다. 그래서 strict의 하위 인자, span, 항목 기록은 T3a와 같다.
- bench:
  - `--filter-policy {strict,null-pass}`. null-pass는 도구 on에서만 허용한다.
  - 결과 JSON과 트레이스 메타에 `filter_policy`를 기록한다.
  - 귀속 검사가 필터 span의 정책을 대조한다.
- `compare_bench_runs`: 정책이 다르면 기본 거부한다. `--allow-policy-mismatch`로만 허용하고, 키가 없으면 strict로 읽는다.
- `analyze_t3_selection.py`: `--filter-policy` · `--preregistration` 인자만 추가했다. 정의와 임계값은 바꾸지 않았다.
- **strict 회귀 (LLM 0건, 산출물은 스크래치패드 — 커밋 안 함):**

  | 대조 | 결과 |
  |---|---|
  | T1 프로브 strict(`--filter-impl retrieval`) vs `probe-retrieval-v1.2-t1-tech-domain-strict-retrieval.json` | **바이트 동일** (sha256 `ddd5d137…`) |
  | T1 무필터 프로브 vs `probe-retrieval-v1.2-t1-nofilter.json` | **바이트 동일** (sha256 `25371588…`) |
  | p1-on · p1-off · p1-pathcheck 트레이스 재생 | **340/340** |
  | T3a 재분석(strict 기본) vs 커밋된 `t3-analysis-v1.2-t3-tool-on.json` | `filter_policy` 키 1개 추가 외 **동일** |

## 3. 1단계 — 검색 재생 (`36600f9`, LLM 0건) → **관문 통과**

- **양성 대조(본 재생보다 먼저):**
  - 층 A 적격 케이스 GS-001 · GS-011의 1회차 항목 전부와 대조군 GS-010#0에 비호환 선택값을 심었다.
  - 심은 적격 **6항목 중 6항목을 해악으로 검출**했다(GS-001 #1·#2, GS-011 #0~#3). 해악 케이스는 정확히 2개였다.
  - 대조군은 비검출이었고, 관문은 "e2e 하지 않음"으로 판정했다. → **통과**
- **본 재생:** T3a 1회차 선택값을 그대로 썼다. strict 재생 = T3a 기록 99/99(무결성 위반 0).

  | | 층 A | 층 B |
  |---|---|---|
  | **null-통과 해악** | **0/33 (0/12 케이스)** | **0/12 (0/5)** |
  | (T3a strict) | 0/33 | 12/12 |
  | 이득 항목 | 9 (strict 12) | 8 (strict 0) |

- 해악 0은 사전 등록 §3의 구조적 예측과 같다. 정답이 필터를 통과하면 순위는 내려가지 않는다.
- **관찰:**
  - **층 A 밀어냄은 GS-010 1회차 3항목이다.** 정답은 strict에서 1위였다. 메타 없는 4건(`2406.00215v3` · `2401.07324v3` · `2404.01023v1` · `2406.01893v2`/`2602.03128v1`)이 정답을 top-4 밖으로 밀어냈다.
  - 그래서 T3a의 층 A 이득 케이스 중 **GS-010을 잃었다.** GS-001 · 015 · 017 · 021 · 023은 유지됐다.
  - top-4에 들어온 메타 없는 문서는 층 A 66건(정답 0), 층 B 80건(정답 24), 층 C 41건이다.

## 4. 2단계 — e2e `v1.2-n1-null-pass` (`eb222ca`)

- **조건:** 도구 on, null-통과, parallel on(4), `--require-trace`, 캐시 끔, 인덱스 37 == 37 clean, PROMPT_VERSION `2026-10-01.1`
- **정지 규칙 발동 0:**
  - `selection_error` 0, 스키마 위반 0, `FilterValueError` 0, 엔드포인트 오류 0
  - 귀속·무결성·정책 기록 문제 0
  - 1회차 Outliner 항목은 30/30 케이스에서 p1-on과 같다.
- **호출 401**(기대 ≈396, 상한 810): outliner 30 · researcher 150 · researcher_select 99 · verifier 92 · writer 30
- 층별 선택값 분포는 T3a와 같다(A·B·C 모두).

## 5. 🔴 판정 — **해악 (D5)** (기준 p1-on, 사전 등록 `5122e97`)

| 지표 | 층 A (n=14) | 층 B (n=9) | 층 C (n=7) |
|---|---|---|---|
| **해악 항목 / 적격** | **0/33** | **0/12** | — |
| 해악 케이스 | 0/12 | 0/5 | — |
| 이득 항목 | 9 | 8 | — |
| **e2e** p1-on → N1 | 12/14 → **13/14** (Δ +1) | 5/9 → **7/9 (Δ +2건)** | 근거 없음 유지 7/7 → **6/7** |
| 뒤집힘 | f→p GS-023 | f→p GS-007 · GS-025 | **p→f GS-027** |
| (관찰) T3a → N1 | 14/14 → 13/14 (GS-010 p→f) | 0/9 → 7/9 | 7/7 → 6/7 |
| 호환 / 정확 일치 | 45/45 / 20/45 | — | — |
| 기권 | 0/45 | 0/32 | 3/22 (GS-008) |

- D3 아님(0) · D4 아님(+2) · **D5 해악(하락 1)** → **종합 해악.**
- D7은 해당하지 않는다. 판정이 해악이고, 층 A fail→pass도 1건뿐이다.
- **GS-027 (negative):** #0 항목 "LLM 에이전트 기반 공장 설비 제어 사례"는 `Agent`를 골랐다.
  - 필터가 무필터 2·3위(메타 있는 무관 문서 `news:42f467…` · `news:c076df…`)를 지웠다.
  - 그 자리로 메타 없는 `arXiv:2405.16887v2`(LLM 기반 멀티에이전트 제조 시스템, 층 B GS-003의 정답)가 4위로 올라왔다.
  - Researcher가 이 문서를 근거로 인용했다. p1-on·T3a의 GS-027 후보에는 없던 문서다.
- **GS-002 (사용자 요청, 재현 여부만):** null-통과에서는 **재현되지 않았다.**
  - **pass**이고, 인용은 `arXiv:2602.03128v1`(정답), `some_topic_covered=true`다.
  - T3a의 "인용은 있는데 전 항목 근거 없음"(`arXiv:2412.05449v1`) 형태는 나오지 않았다. 원인은 분석하지 않았다.

### 결정 (사용자 사전 지시대로 — 질문 없음)

- **도구 기본 off 유지. 필터 기본 정책도 strict 유지.** 기본값을 켤지 묻는 분기(무해일 때)는 발동하지 않았다.
- 기본값을 바꾸는 코드 변경은 없다.

## 6. 사후 관찰 (판정에 쓰지 않음)

- **strict와 null-통과는 해악이 나는 층이 다르다.**
  - strict는 메타 없는 정답을 지운다 → 층 B
  - null-통과는 메타 없는 무관 문서를 끌어올린다 → 층 C, 그리고 층 A GS-010 밀어냄
- 메타 없는 14건이 전부 에이전트·멀티에이전트 계열이다. 그래서 `Agent`를 고른 항목에서 희석이 크다. 이 결론은 코퍼스 구성에 강하게 의존한다.
- 증가분(p1-on 대비, 기록만): 호출 +104(select +99 · verifier +16 · researcher −11), 토큰 +60,209, wall mean +1.06s

## 7. 🔴 결정 대기 / 열린 것

| # | 항목 | 참조 |
|---|---|---|
| ① | **ADR-030 Status** — Proposed. Accepted로 올릴지 | ADR-030 |
| ② | **기본값 결정 대기: 없음.** 판정이 해악이라 사용자 사전 지시대로 off · strict를 유지했다 | §5 |
| ③ | ADR-027에 `filter_policy` 인자 추가를 Amendment로 남길지. 기본 strict라 ADR-027의 결정은 유효하다. 지금은 ADR-030에만 적었다 | ADR-027 |
| ④ | README ADR 수 표기(현재 30건) — T3b에서 함께 고친다(session-23 §6 ②에서 이월) | README |
| ⑤ | 사용자 명시 필터 경로의 메타 부재 위험 고지(session-23 §6 ④). N1으로 null-통과는 대안이 아니라 **다른 해악을 가진 정책**임이 확인됐다 | ADR-029·030 Risks |
| ⑥ | GS-002 T3a 원인 미확인(session-23 §6 ③). null-통과에서는 재현되지 않았다 | §5 |
| — | 이어지는 것: T3b(README 최종 정리) · 메타 보강(층 B 스냅샷 export) 시 strict·null-통과 재측정(ADR-030 Review Trigger) · ECR 이미지 빌드 시점 · `where` 경로 HNSW 완전성 | session-22 §8 |

## 8. 다음 세션 권장

- **T3b(README 최종 정리)를 권한다.** T3a·N1 판정이 모두 확정됐고(해악 D4 · 해악 D5), 재측정할 이유가 없다.
  T3b에서 반영할 것:
  - 두 판정
  - 층별 표
  - "필터는 사용자가 명시할 때만, 기본 strict"
  - strict / null-통과 위험 대비
  - ADR 수
- 필터 경로를 다시 열려면 정책보다 **데이터 쪽**(층 B 메타 보강)이 다음 변수 후보다. 이것도 새 변수이므로 새 사전 등록이 먼저다(ADR-002).

---

## 9. 결정 반영 (같은 세션, 사용자 결정 · **LLM 호출 0건**, `d23678e`)

| # | 결정 | 결과 |
|---|---|---|
| ① | **ADR-030 Accepted** | Status를 `Accepted (2026-10-01, session-24 — 사용자 결정)`로 바꿨다. 재측정은 없고 §5 결과 그대로다 |
| ③ | **ADR-027에 Amendment를 남긴다** | `filter_policy` 인자(`strict` 기본 · `null_pass`, v1.2-N1)를 Amendment로 적었다. 헤더 `Amended:` 줄, strict 불변 확인(T1 바이트 동일 · p1 재생 340/340), N1 판정(해악 D5)에 따른 기본 strict 유지, Review Trigger 추가, References에 ADR-029·030을 넣었다. 결정 자체(strict 전수 후처리)는 그대로다 |
| — | **GS-002 재현 여부 (사용자 요청 — 재현 여부만, 원인 분석 안 함)** | **재현되지 않았다.** null-통과 e2e에서 GS-002는 **pass**다. 인용은 정답 `arXiv:2602.03128v1`이고, `some_topic_covered=true` · `expected_doc_cited=true`다. 1회차 4항목 중 3항목(#1·#2·#3)이 정답을 supporting으로 골랐다(#0은 supporting 없음). T3a(strict)의 "인용은 있는데(`arXiv:2412.05449v1`) 전 항목 근거 없음" 형태는 null-통과에서 나오지 않았다 |

§7 갱신: ①③ 해소. ②는 해당 없음(기본값 결정 대기 없음). ④⑤⑥은 그대로 열려 있다. ⑥(GS-002 T3a 원인)은 미확인으로 남는다 — null-통과에서 재현되지 않는다는 사실만 추가됐다.

세션 종료. push 하지 않았다.
