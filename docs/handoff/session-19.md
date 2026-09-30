# Session 19 핸드오프 — v1.2-S0c Researcher 점수 노출 제거 → 재측정 → README §4 교체

- **날짜:** 2026-09-30
- **범위:** v1.2-S0c. 파이프라인 변경은 `_format_candidates` 한 줄 + `PROMPT_VERSION`뿐.
  `scripts/bench_golden.py` 변경 **0줄**. governance.md · `.claude/rules/` · 훅 수정 없음.
- **ADR:** ADR-025 — Amendment(판정 규칙 교체, S0c 측정 전) + **Status Accepted (무해 확인)**
- **`PROMPT_VERSION`:** `2026-09-16.1` → **`2026-09-30.1`** (점수 제거)
- **테스트:** `python -m pytest tests/ -q` → **224 passed** (223 → +1)
- **LLM 호출:** vLLM(`VLLM_BASE`, `endpoint_fp=1897f0ecc081`, S0b와 같은 지문)만. **논리 호출 실측 623건**
  (run3 296 + 경로 확인 30 + S0c 297). 승인 상한 1,386 이내. 토큰 665,194. 비용 0원(할당분).
  외부 API·벤더 호출 0건. 유료 리소스·IaC 변경 0건.
- **시크릿 검사:** `scan_local_secrets.py` 0건(양성 대조 통과), 커밋 diff 대조 0건 (커밋마다)

## 커밋 순서 (판정 기준 → 기준선 → 계산 방법 → 코드 → 결과 → README)

| 커밋 | 내용 |
|---|---|
| `d855242` | ADR-025 Amendment — 판정 규칙 교체. **S0c·run3 결과 파일이 없을 때** 커밋 |
| `74d01e7` | run3 결과(기존 프롬프트) + run1↔run3 대조 — **프롬프트 변경 전** |
| `457758f` | `scripts/compare_adr025_pairs.py` — 판정 계산. **S0c 측정 전** |
| `77e60bf` | 코드: 후보 헤더 점수 제거 + `PROMPT_VERSION` + 테스트 |
| `47d2719` | 결과: S0c · 경로 확인 · 짝 비교 JSON + ADR-025 Accepted |
| `9b5ee21` | README §4 교체 |
| (이 커밋) | 이 핸드오프 |

push 하지 않았다.

---

## 1. 🔑 판정 — **무해 확인** (ADR-025 Amendment 기준, 사후 변경 없음)

| 지표 | run3 (점수 노출) | S0c (제거) | run1 병기 | 해악 문턱 |
|---|---:|---:|---:|---:|
| 1차: 위험군 1회차 짝 — 적격 / hit | 15 / 14 | 15 / 14 | 15 / 14 | — |
| 1차: 감소 · 증가 · **순감소** | — | 0 · 0 · **0** | 0 · 0 · 0 | ≥ 2 |
| pass/fail 순 뒤집힘 (A · B · C) | — | **0** (0/14 · 0/9 · 0/7) | 0 | ≥ 2 |
| 2차: 전 회차 조건부 선택률 (분자/분모) | 15/17 | 15/17 | 15/17 | 판정 안 함 |

- **짝 성립:** 1회차 (케이스, 항목) 쌍과 후보 `doc_id` 목록이 **30건 전부** run3과 같다 → 짝 비교 중단 조건 해당 없음.
- **1회차 Researcher 호출 99건 전부(위험군 밖 포함) `supporting`이 같다.** "관찰"로 적을 1건 차이도 없다.
- **재시도 호출은 62건 중 3건 다르다** (전부 위험군 밖, 판정 지표 밖 — 기록만):
  GS-001 `[]` → 정답 1건 · GS-003 `[]` → 정답 1건 · GS-016 정답 1건 → `[]`.
  Verifier 호출이 +1/+1/−1 변했지만 `uncovered_count`·인용 집합·pass/fail이 같다.
- **GS-011 "모델의 보상 속임수(reward hacking/gaming) 정의 및 사례"**: 정답 `arXiv:2609.19101v1`이 1회차·재시도
  모두 후보 **1위**. run1·run2·run3(노출)·S0c(제거) **네 번 모두** `supporting = []`. **점수를 빼도 고르지 않는다** —
  미선택 원인은 점수가 아닐 가능성이 크다("정의" 항목을 문서가 뒷받침하지 않는다고 읽는 쪽). 확정하지 않았다.
- **한계:** 기준선 1회차 14/15 → 개선 여지 최대 1건. **효과 입증은 주장하지 않는다.** 해악이 아니므로 제거를 유지한다.

## 2. 시간 간 변동 — 연속 실행과 같은 폭, **사실상 0**

| 비교 | 간격 | pass/fail 뒤집힘 | 인용 차이 | T · revision · 호출 변화 | 토큰 차이 |
|---|---|---:|---:|---:|---|
| 연속 run1 ↔ run2 (session-18) | 약 6분, 같은 프로세스 | 0 | 0 | 0 | 1건 (GS-013 2토큰) |
| **시간 간 run1 ↔ run3** | 약 27분, 별도 프로세스 | **0** | **0** | **0** | 1건 (GS-013 2토큰) |

- 1회차 짝(run1 ↔ run3)도 15쌍 전부 일치, 순감소 0.
- ⚠️ **같은 날 한 시간 안의 세 시점(13:47 · 13:53 · 14:14)이다.** 다른 날·다른 부하의 변동은 아직 재지 않았다.
- 파일: `docs/eval/bench-compare-v1.2-s0b-run1-vs-s0c-run3.json`

## 3. 결과 경로 — **P1 비교 기준은 `bench-v1.2-s0c.json`**

| 파일 | 내용 |
|---|---|
| `docs/eval/bench-v1.2-s0c.json` | **S0c 본측정 30건 × 1회 — 현재 코드. P1(다음 하네스 변경)의 비교 기준** |
| `docs/eval/bench-v1.2-s0c-run3-old.json` | 동시간 대조 — 기존 프롬프트(점수 노출) 30건 × 1회 |
| `docs/eval/bench-v1.2-s0c-pathcheck.json` | 경로 확인 GS-013 / GS-006 / GS-028 × 1회 (새 프롬프트) |
| `docs/eval/adr025-pairs-s0c-vs-run3.json` | **판정** — 짝 비교 · 순 뒤집힘 (`compare_adr025_pairs.py`) |
| `docs/eval/adr025-pairs-s0c-vs-s0b-run1.json` | 같은 계산, run1 기준 (병기) |
| `docs/eval/bench-compare-v1.2-s0b-run1-vs-s0c-run3.json` | 시간 간 변동 (`compare_bench_runs.py`) |

S0c 측정 조건: 골든셋 2.0 · `candidate_score_exposed: false` · `prompt_version: 2026-09-30.1` · `cache_enabled: false` ·
코퍼스 37 · `index_check` clean · `max_revisions=2` · `top_k=4` · temperature 0 · `--require-trace` (트레이스 없는 행 0).

```
python scripts/bench_golden.py --label v1.2-s0c-run3-old --require-trace          # 프롬프트 변경 전
python scripts/bench_golden.py --label v1.2-s0c-pathcheck --only GS-013 GS-006 GS-028 --require-trace
python scripts/bench_golden.py --label v1.2-s0c --require-trace
python scripts/compare_adr025_pairs.py docs/eval/bench-v1.2-s0c-run3-old.json docs/eval/bench-v1.2-s0c.json --out docs/eval/adr025-pairs-s0c-vs-run3.json
```

- P1은 S0c와 `prompt_version`이 같으면 `compare_bench_runs.py`로 바로 대조된다. P1이 프롬프트를 바꾸면
  대조를 거부한다(의도된 동작) — 그때는 `compare_adr025_pairs.py`처럼 전/후 전용 비교가 필요하다.

## 4. 호출 수 (S0c, 현재 코드)

| | run3 (점수 노출) | **S0c** |
|---|---:|---:|
| 논리 호출 / 회차 | 296 (O 30 · R 161 · V 75 · W 30) | **297** (O 30 · R 161 · **V 76** · W 30) |
| 질의당 평균 · p50 · p95 · 최대 | 9.87 · 9 · 13 · 16 | 9.90 · 9 · 14 · 15 |
| **항목당 호출** 평균 · p50 · p95 · 최대 | 2.98 · 2.67 · 4.0 · 4.0 | **2.99** · 2.67 · 4.0 · 4.0 |
| 청구 토큰 합 | 319,458 | **313,047 (−2.0%)** — Researcher 입력 221,516 → 214,432 (−3.2%) |
| 엔드포인트 요청 상한 (논리 × 3) | 888 | 891 |
| 파이프라인 wall p50 / p95 | 12.03s / 21.86s | 11.95s / 21.08s |

지연은 판정에 쓰지 않는다(서버 prefix 캐시 영향을 가를 수 없다). T=3 21 · T=4 9, revision 1회 4 · 2회 26 — 세 조건 동일.
재검색 새 근거 0건(재검색 인용 run3 8 → S0c 9).

## 5. README 변경 요약 (`9b5ee21`)

- **§4 전체 교체** — v1.1 조건(코퍼스 37 · 골든셋 v2.0 30건, 층별)으로:
  §4.1 워밍업 분리 · §4.2 지연·노드별 분해(S0c) · §4.3 호출·비용(노출 vs 제거) · §4.4 층별 채점 ·
  §4.5 점수 노출 제거 판정 + 자연 변동(연속·시간 간). 모든 표에 조건·n·측정 시각을 붙였다.
- **§4.6 이력** — v1.0(session-08, n=9) 지연·캐시·인젝션 방어 비용을 축약 보존. "직접 비교 금지" 명기.
- 상단 **상태 문구 "v1.2 기준선 완료"** 블록 추가(session-16 블록은 그대로 두고 그 아래 v1.0 경고를 대체).
- §0 표: 지연·비용·품질 행을 v1.2 값으로, 테스트 201 → 224(컨테이너 재실행은 201건 시점이라고 명기),
  ADR 24 → 25건 · Amendment 13 → 14건.
- §5.9 해소 표기, §8.1에 ADR-025 행 추가, §8 문서 표 "ADR-001 ~ ADR-025".

## 6. 지시와 다르게 / 추가로 한 것

| 항목 | 내용 |
|---|---|
| 프롬프트 "새 버전 파일" | 현 구조상 점수는 `nodes.py` `_format_candidates`, 버전은 `prompts.py`에 있어 새 파일로 분리할 수 없었다. **사용자 결정: 제자리 수정.** 이전 버전은 git 이력(`40ba46f`)으로 복원 |
| bench 테스트 2건 | `tests/test_bench_golden.py`가 S0b 상태(`candidate_score_exposed=True`)를 고정하고 있어 기대값을 뒤집었다. bench 코드는 무변경 |
| `compare_adr025_pairs.py` | 판정 계산을 측정 전에 고정하려고 새로 만들었다(읽기 전용, LLM 호출 없음). S0b run1↔run2로 핸드오프 값(15/14, 뒤집힘 0)과 일치 확인 |
| 짝 불일치 범위 | Amendment에 "위험군 밖 1회차 불일치는 기록만, 1차 지표 계산은 막지 않는다"로 적었다(측정 전). 실제로는 30건 전부 일치해 적용되지 않았다 |

## 7. 🔴 결정 대기 / 열린 것

| # | 항목 | 참조 |
|---|---|---|
| — | `LITELLM_LOCAL_MODEL_COST_MAP` 미설정 — 임포트 시 원격 가격표 조회 가능성. 이번에도 바꾸지 않음 | session-18 §5 |
| — | 다른 날·다른 부하에서의 변동 미측정 (이번 시간 간 변동은 27분 간격) | §2 |
| — | GS-011 "정의" 항목 미선택 원인 (점수 아님이 확인됨) | §1 |
| — | 컨테이너 안 테스트 재실행 (README는 201건 시점이라고 적음) | §5 |

- **session-13 §6.2 결정 대기 ①(점수 노출)은 ADR-025 Accepted로 종료.**
- session-18 결정 대기 ④는 (b)로 닫혔다(Amendment `d855242`).

## 8. 다음 세션 진입 조건

- [ ] 다음 하네스 변경(P1 등)은 **한 번에 하나** (ADR-002). 비교 기준 = `docs/eval/bench-v1.2-s0c.json`
- [ ] 판정 기준을 측정 전에 등록 (이번과 같은 순서)
- [ ] 승인 게이트 6항목 — 1번 `VLLM_BASE`부터

## 9. 승인 게이트 / 상태

| 항목 | 상태 |
|---|---|
| 승인 | run3 → 경로 확인 → S0c 본측정, 각각 게이트 6항목 제시 후 사용자 승인 |
| 경로 확인 | GS-006 T=4 → 10 · GS-013 T=3 → 12 · GS-028 T=3 → 8 (S0b와 동일), `candidate_score_exposed=false`, 항목 기록 있음 |
| 실측 호출 총계 | **논리 623건** (296 + 30 + 297) ≤ 1,386 · 토큰 665,194 · 0원(할당분) |
| `.claude/external-llm-approved` | 만들지 않음 — 목적지가 `VLLM_BASE`라 예외 대상 아님 |
| 인덱스 | 읽기 전용 검사 + 검색만. 잠금 해제 확인 |
