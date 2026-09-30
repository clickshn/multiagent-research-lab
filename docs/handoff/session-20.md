# Session 20 핸드오프 — v1.2-P1 Researcher·Verifier 노드 안 동시 호출

- **날짜:** 2026-09-30
- **범위:** v1.2-P1. 바뀐 변수는 **"노드 안 LLM 호출의 동시 실행 여부" 하나**. 그래프 구조·프롬프트·판정 로직 불변,
  `Send` 미사용, **`PROMPT_VERSION 2026-09-30.1` 그대로**. governance.md · `.claude/rules/` · 훅 수정 없음.
- **ADR:** ADR-026 신규 → **Accepted** (§10 결정 후 Amendment)
- **테스트:** `python -m pytest tests/ -q` → **256 passed** (224 → 246 측정 전후 → 256 §10 결정 반영)
- **LLM 호출:** vLLM(`VLLM_BASE`, `endpoint_fp=1897f0ecc081`, S0b·S0c와 같은 지문)만. **논리 호출 실측 624건**
  (경로 확인 30 + off 297 + on 297). 승인 상한 1,320 이내. 비용 0원(할당분). 외부 벤더 0건. 유료 리소스·IaC 0건.
- **시크릿 검사:** `scan_local_secrets.py` 0건(양성 대조 통과), 커밋 diff 대조 0건 (커밋마다)

## 커밋 순서 (코드·판정 기준 → 결과 → 문서)

| 커밋 | 내용 |
|---|---|
| `c07a5a1` | 코드: 노드 안 동시 호출 · 검색 락 · `input_hash` · span 키 · JSONL 쓰기 락 · 설정 · bench 옵션 + **판정 스크립트 `compare_p1_concurrency.py` (측정 전)** + 테스트 |
| `89b31fb` | 관찰 스크립트 `compare_trace_outputs.py` — **측정 후 추가**(토큰 6 차이 설명용, 판정 밖) |
| `6323e91` | 결과: 경로 확인 · off · on · 판정 JSON · 관찰 JSON |
| `12626f5` | ADR-026 + README §8.1 한 줄 (+ §8 문서 표 범위 "ADR-001 ~ ADR-026") |
| `65e62d8` | 이 핸드오프 (초판) |
| `668b207` · `2421898` · `58208b8` | §10 결정 1 · 2 · 3 (코드, LLM 호출 0건) |
| (이 커밋) | ADR-026 Accepted + Amendment · README 행 정정 · 이 핸드오프 §10 |

push 하지 않았다.

---

## 1. 🔑 지연 — off vs on (동시간 대조, 같은 세션, off → on 연달아)

| | **off** (순차) | **on** (상한 4) |
|---|---:|---:|
| 실행 시각 | 16:03 – 16:09 | 16:09 – 16:14 |
| wall p50 (n=30, 워밍업 제외) | **11.89s** | **9.56s** (−20%) |
| wall p95 | **20.83s** | **14.40s** (−31%) |
| 평균 · 최대 | 11.50 · 28.09s | 8.45 · 18.52s |
| 케이스별 on/off 비 중앙값 (범위) | — | **0.731** (0.617 – 0.932) |
| bench 전체 | 345.6s | 254.0s |
| Researcher · Verifier 호출당 지연 | 0.60 · 1.15s | 0.76 (+26%) · 1.22s |
| 동시성 상한 / 실효 | 4 / 1 | 4 / 4 |

- **상한이 걸리는 곳:** Outliner(1.15s)·Writer(3.97s)가 직렬로 남아 질의당 약 5.1s가 줄지 않는다. Researcher 호출당 지연 증가는 서버 경합.
- S0c(13:47~)와는 **지연을 비교하지 않는다** — 시각이 달라 부하 조건이 다르다.

## 2. 로직 동일성 — **결함 0**

- Outliner 출력 **30/30 동일** → Outliner 변동(시간 간)으로 뺀 케이스 0.
- 1회차 Researcher 호출 입력 해시 **99/99 동일**. 트레이스로 보면 **전 호출 297/297 입력 동일**(전 노드·전 회차).
- 호출 수 노드별 동일: O 30 · R 161 · V 76 · W 30 (S0c와도 같음). off 토큰 313,047 = S0c와 정확히 같다.
- off 경로 = S0c 코드 경로: HEAD(S0c) `nodes.py`와 가짜 입력으로 state·호출 순서·검색 이벤트 순서 전부 일치(일회성 대조).

## 3. 배칭 비결정성 — **판정 지표 0건** / 텍스트 층 15건(관찰)

| 층 | off ↔ on (배칭) | S0c ↔ off (시간 간, 비교 기준) |
|---|---:|---:|
| 인용 집합 차이 | **0** / 30 | 0 / 30 |
| pass/fail 뒤집힘 | **0** | 0 |
| 1회차 supporting 차이 | **0** / 99 | 0 / 99 |
| (관찰) 입력 같고 **출력 텍스트** 다름 | **15** / 297 (R 5 · V 10) | 2 / 297 (V 2) |
| (관찰) 그중 구조 필드(`supporting`·`verdict`) 다름 | 0 | 0 |
| (관찰) 실행 내부 같은 입력 반복 31쌍 중 텍스트 다름 | 5 (off·on 각각) | 5 (S0c·off 각각) |

- 차이는 전부 `note`·`reason` 자유 문장. 토큰 313,047 → 313,041은 이것 때문이다.
- ⚠️ **순차에서도 같은 입력의 텍스트가 다를 수 있다**(실행 내부 5/31, 세 회차 모두 같은 5쌍 패턴). 동시 실행은 **회차 간** 텍스트 변동을 2 → 15로 늘렸다.
  원인(서버 배칭 구성 / prefix 캐시)은 **가설이고 확정하지 않았다.**
- 텍스트 층은 **측정 후** 추가한 관찰이라 판정에 쓰지 않는다(ADR-026 Evidence에 "관찰"로 명기).

## 4. 오류 · span-항목 귀속

- **엔드포인트 오류(최종 실패): off 0 · on 0** (429 0 · 타임아웃 0 · 기타 0). ⚠️ LiteLLM 내부 재시도(`max_retries=2`)로 흡수된 429는 span에 안 보인다.
- **span-항목 귀속 불일치: 경로 확인 3행 · off 30행 · on 30행 모두 0.** 검사: 각 항목의 `(revision, item_index)` → Researcher 호출 span의
  topic·`input_hash` 일치, 후보 doc_id가 그 span 프롬프트에 있음, supporting ⊆ 후보, span 수 = 호출 수.
  검사기는 변조 사본(해시 두 개 맞바꿈)으로 **양성 대조 통과**. on에서 **21/30행이 완료 순서로 쓰였고** 전부 올바르게 재짝지어졌다
  (경로 확인에서는 GS-013 1행).
- 경로 확인 3건(GS-006 10 · GS-013 12 · GS-028 8호출)은 항목별 후보·supporting이 **S0c와 완전히 같았다**. 검색 락 대기 최대 0.20s.
- 검사 스크립트는 scratchpad의 일회성 코드다(커밋 안 함). 필요하면 bench에 넣는 것을 검토 — §7.

## 5. 결과 경로 — **T2·T3 비교 기준은 `bench-v1.2-p1-on.json`**

| 파일 | 내용 |
|---|---|
| `docs/eval/bench-v1.2-p1-on.json` | **on 30건 × 1회 — T2·T3의 비교 기준** (`parallel: true`, 상한 4) |
| `docs/eval/bench-v1.2-p1-off.json` | off 30건 × 1회 (같은 세션, on 직전) |
| `docs/eval/bench-v1.2-p1-pathcheck.json` | 경로 확인 GS-006/013/028, on |
| `docs/eval/p1-concurrency-off-vs-on.json` | **판정** — 로직 · 배칭 비결정성 · 시간 간 · 지연 · 호출 · 오류 |
| `docs/eval/p1-trace-outputs-off-vs-on.json` | 관찰 — 호출 단위 텍스트 대조 (off ↔ on) |
| `docs/eval/p1-trace-outputs-s0c-vs-off.json` | 관찰 — 같은 대조 (S0c ↔ off) |

```
python scripts/bench_golden.py --label v1.2-p1-pathcheck --only GS-013 GS-006 GS-028 --parallel on --max-concurrency 4 --require-trace
python scripts/bench_golden.py --label v1.2-p1-off --parallel off --require-trace
python scripts/bench_golden.py --label v1.2-p1-on  --parallel on --max-concurrency 4 --require-trace
python scripts/compare_p1_concurrency.py docs/eval/bench-v1.2-p1-off.json docs/eval/bench-v1.2-p1-on.json --s0c docs/eval/bench-v1.2-s0c.json --out docs/eval/p1-concurrency-off-vs-on.json
python scripts/compare_trace_outputs.py docs/eval/bench-v1.2-p1-off.json docs/eval/bench-v1.2-p1-on.json --out docs/eval/p1-trace-outputs-off-vs-on.json
```

- ⚠️ **기본값은 꺼짐이다.** T2·T3를 `v1.2-p1-on`과 비교하려면 `--parallel on --max-concurrency 4`(또는 `.env`에 `RESEARCH_PARALLEL=on`)를
  잊지 말 것. `compare_bench_runs.py`의 `CONDITION_KEYS`에 `parallel`이 **없다** — 조건이 달라도 대조를 거부하지 않는다. 결과 JSON의 `parallel`을 눈으로 확인할 것(§7).

## 6. 지시와 다르게 / 추가로 한 것

| 항목 | 내용 |
|---|---|
| 설정 위치 | `ConcurrencySettings`를 `src/providers/config.py`에 뒀다 — 상한이 묶인 것이 엔드포인트 동시 수용량이라서. `RESEARCH_PARALLEL` · `RESEARCH_MAX_CONCURRENCY` |
| 기본값 | **꺼짐**(캐시와 같은 이유, ADR-008). 지시에 기본 on/off가 없어 보수적으로 정했다 |
| 판정 스크립트 | `compare_p1_concurrency.py`를 측정 전에 코드 커밋에 함께 넣었다(별도 커밋 아님) |
| 관찰 스크립트 | `compare_trace_outputs.py`를 **측정 후** 추가 — off/on 토큰 6 차이를 설명하려고. 판정에는 넣지 않았다 |
| `run_research.py` | 같은 설정을 읽어 `max_concurrency`를 넘기도록 배선 |
| 테스트 기존 계약 | `item_records`가 키 없는 옛 트레이스는 파일 순서 그대로 — 기존 테스트 무변경 통과 |
| README | 요청한 §8.1 한 줄 외에 §8 문서 표의 범위 "ADR-001 ~ ADR-025" → "026"도 고쳤다 |
| Langfuse | `_LangfuseTrace` 스레드 안전성은 손대지 않았다(bench·클라우드는 JSONL만) — ADR-026 Risks |

## 7. 🔴 결정 대기 / 열린 것

| # | 항목 | 참조 |
|---|---|---|
| ① | **기본값을 켤지** — 지금 꺼짐. 켜면 ADR-026 Accepted. 지연 이득(p95 −31%) vs 텍스트 층 변동 증가(2 → 15/297, 판정 필드 영향 0) | ADR-026 |
| ② | `compare_bench_runs.py` `CONDITION_KEYS`에 `parallel` 추가 여부 — 넣으면 on/off 짝 대조를 그 스크립트로는 못 한다(P1 판정은 전용 스크립트라 무관) | §5 |
| ③ | span-항목 귀속 검사를 bench(`--require-trace`)에 넣을지 — 지금은 scratchpad 일회성 | §4 |
| — | 엔드포인트 동시 수용량 미확인(상한 4에서 오류 0, 한 시점). 내부 재시도로 흡수된 429 미계측 | ADR-026 Risks |
| — | 텍스트 층 비결정성 원인(배칭 / prefix 캐시) 미확정 | §3 |
| — | session-19에서 넘어온 것: `LITELLM_LOCAL_MODEL_COST_MAP` 미설정 · 다른 날 변동 미측정 · GS-011 "정의" 미선택 원인 · 컨테이너 테스트 재실행(README는 201건 시점) | session-19 §7 |

## 8. 다음 세션 진입 조건

- [ ] 결정 ①(기본값)
- [ ] T2·T3는 **`--parallel on --max-concurrency 4`**로, 비교 기준 = `docs/eval/bench-v1.2-p1-on.json`
- [ ] 한 번에 하나(ADR-002), 판정 기준은 측정 전에 등록
- [ ] 승인 게이트 6항목 — 1번 `VLLM_BASE`부터

## 9. 승인 게이트 / 상태

| 항목 | 상태 |
|---|---|
| 승인 | 경로 확인 → 동시간 대조(off → on), 각각 게이트 6항목 제시 후 사용자 승인 |
| 실측 호출 총계 | **논리 624건** (30 + 297 + 297) ≤ 1,320 · 0원(할당분) |
| `.claude/external-llm-approved` | 만들지 않음 — 목적지가 `VLLM_BASE`라 예외 대상 아님 |
| 인덱스 | 읽기 전용 검사 + 검색만(queue 37 == 문서 37 clean). 잠금 해제 확인 |

---

## 10. 결정 반영 (같은 세션, 사용자 결정 · **LLM 호출 0건**)

§7의 결정 대기 ①②③을 사용자가 결정했다. 재측정 없음 — 판정 결과(§1~§4)는 그대로다.

| # | 결정 | 결과 | 커밋 |
|---|---|---|---|
| ① | **동시 실행 기본값 켬(상한 4)**, `--parallel off`로 끌 수 있게 유지, ADR-026 Accepted. 근거: 판정 지표 차이 0 · 입력 동일 297/297 · p95 −31% · 기본을 켜 두면 T2·T3에서 플래그 누락으로 조건이 섞일 위험이 없다 | `RESEARCH_PARALLEL` 비워두면 on, `0/false/no/off`면 off, **그 외 값은 `ConfigError`**(오타 거부). 노드 팩토리 인자 기본은 1 그대로(순차 응답에 의존하는 기존 테스트 때문) — 실행 진입점이 설정을 읽는다. 플래그 없는 bench가 on·실효 4로 도는 테스트 추가 | `668b207` |
| ② | `compare_bench_runs.py`: `parallel`이 다르면 기본 거부, `--allow-parallel-mismatch`일 때만 비교 | `parallel` 키가 없는 옛 결과는 순차(`false`)로 읽는다 → **S0c ↔ off는 대조되고 S0c ↔ on은 거부된다.** 결과에 `parallel`·`parallel_mismatch_allowed`. `compare_p1_concurrency.py`는 허용 인자를 넘긴다 — P1 판정 재계산 결과 불변 확인. 실파일로 off ↔ on 기본 거부(종료 2) 확인 | `2421898` |
| ③ | span 귀속 검사기를 레포에 커밋, `--require-trace`에 포함, 불일치 행이면 부분 결과 없이 멈춤. 변조 사본 테스트 | `bench_golden.attribution_problems` + 행의 `attribution_problems`(None = 검사 불가, [] = 없음) + `scripts/check_attribution.py`(저장 결과 재검사). 아래 참고 | `58208b8` |

**③ 세부**

- 검사 항목: 호출 span 키 중복 · 후보 있는 항목마다 호출 span 정확히 1개 · 후보 없는 항목엔 없음 · topic·`input_hash` 일치 ·
  후보 doc_id가 호출 프롬프트에 **후보 순서대로** · supporting ⊆ 후보.
- ⚠️ **span 입력이 4,000자에서 잘린다.** 실측 on 결과에 잘린 Researcher 프롬프트가 **7건** 있었다. 잘림 표식(`...<Nchars>`)이
  있을 때만 뒤쪽 후보 누락을 허용한다 — 이 규칙 없이 넣었으면 본측정 결과가 오탐으로 거부됐다. 표식 없이 빠지면 불일치로 센다(테스트 있음).
- 재검사: `python scripts/check_attribution.py docs/eval/bench-v1.2-p1-{pathcheck,off,on}.json` → **3 · 30 · 30행 불일치 0.**
  S0c는 `item_index`가 없어 **검사 불가**(종료 2) — 의도된 동작.
- 실측 on 결과의 변조 사본: 해시 맞바꿈 2건 · 후보·supporting 맞바꿈 2건 **모두 검출.**
- 테스트: 깨끗한 동시 실행 0건 · 해시 맞바꿈 · 후보 맞바꿈 · topic · 고아 span · 중복 · 후보 밖 supporting · 잘림 허용 경계 ·
  bench가 `EXIT_TRACE`로 멈추고 결과 파일을 남기지 않음 · `--require-trace` 없으면 행에 기록만 · CLI와 bench 판정 일치.
- session-20 초판 §4의 scratchpad 일회성 검사기는 이것으로 대체됐다.

**§7 갱신:** ①②③ **종료.** 남은 것 — 엔드포인트 동시 수용량(상한 4, 한 시점만 확인) · 내부 재시도로 흡수된 429 미계측 ·
텍스트 층 비결정성 원인 미확정 · Langfuse 스레드 안전성 미검증 · session-19에서 넘어온 4건.

**§8 갱신 — 다음 세션 진입 조건**

- [ ] T2·T3는 **기본값(on, 상한 4)으로** 잰다 — 플래그 불필요. 결과 JSON `parallel: true`, `effective_concurrency: 4` 확인
- [ ] 비교 기준 = `docs/eval/bench-v1.2-p1-on.json`. `compare_bench_runs.py`로 바로 대조된다(둘 다 on). off 결과와 대조하려면 `--allow-parallel-mismatch`
- [ ] `--require-trace`가 이제 귀속 검사까지 한다 — 불일치면 멈춘다(부분 결과 없음)
- [ ] 한 번에 하나(ADR-002), 판정 기준은 측정 전에 등록, 승인 게이트 6항목 — 1번 `VLLM_BASE`부터
