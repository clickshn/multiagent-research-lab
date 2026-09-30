# Session 17 핸드오프 — v1.2-S0a 기준선 측정 준비 (조사만)

- **날짜:** 2026-09-30
- **범위:** v1.2-S0a. **코드 변경 0줄, LLM·API 호출 0건.** governance.md · `.claude/rules/` · 훅 수정 없음.
- **ADR:** **ADR-025**(신규, Proposed, 결정 미정) — `docs/adr/ADR-025-researcher-candidate-score-exposure.md`
- **로컬 확인(읽기 전용):** `var/chroma` `embeddings_queue` rows=37 / distinct_ids=37 / op2=37 → **queue == 문서 수, clean**
  (`index_guard.read_write_log`, 읽기 전용 sqlite).
- **설치 버전:** Python 3.14.0 / langgraph 1.1.4 / langgraph-checkpoint 4.0.1 / langchain-core 1.2.25 / litellm 1.101.0 / chromadb 1.5.5

---

## 0. ⚠️ 먼저 — 계획 문서를 저장하지 못했다

지시 1번의 `docs/plans/v1.2-plan.md`는 **만들지 않았다.** 지시문에 계획 본문 대신
`<v1.2 실행 계획(수정판) 전문>`이라는 자리표시자만 들어 있었고, 레포 안에도 해당 문서가 없다
(`docs/plans/` 디렉터리 없음, "v1.2 계획" 검색 0건). 내용을 추정해 채우면 없는 계획을 적게 되므로
비워 두었다. **다음 세션 첫 작업: 계획 전문을 받아 저장.** 아래 §1의 대조는 지시 2번에 나열된
측정 조건 7개를 기준으로 했다.

---

## 1. 하네스 점검 — 계획의 측정 조건 vs governance.md / `.claude/rules/`

**충돌은 없다. 빠진 것이 4개이고, 그중 2개는 코드로 강제되지 않는다.**

| 측정 조건 | governance.md | rules | 코드 집행 | 판정 |
|---|---|---|---|---|
| 캐시 끔 | ✅ "측정 방법론" (ADR-008) | — | ✅ `bench_golden.py:226` `get_provider(cache=args.cache)` — **`--cache`를 안 주면 `.env`의 `LLM_CACHE`와 무관하게 꺼짐**. 결과 JSON에 `cache_enabled` 기록 | 일치. 단 governance는 "`LLM_CACHE` 환경변수 확인"이라 적었는데 bench는 환경변수를 **무시**한다 — 서술 불일치(무해) |
| 콜드 로딩 분리 | ✅ "측정 방법론" | — | ⚠️ **부분.** 임베딩 워밍업만 분리(`bench_golden.py:71-87`). 첫 케이스 `wall_clock_s`에 **①Chroma 첫 질의(HNSW 로딩) ②`import litellm`**(`llm.py:117`, 호출 시점 임포트)이 섞인다. `llm_latency_s`는 임포트 후에 타이머를 켜므로 안전, **`wall_clock_s`·`other_latency_s`만 오염** | **빠짐(부분)** |
| queue == 문서 수 | ❌ 없음 | ❌ | ❌ bench는 `retriever.count()`만 출력(`:224`). 쓰기 로그 검사 없음 | **빠짐.** session-16 §3.3(운영 경로 2차 방어)이 아직 미결 |
| 동시 실행 금지 | ❌ 없음 (session-15 §7.2 / ADR-015 Risks에만) | ❌ | ❌ 잠금 없음 | **빠짐 — 사람의 기억에만 있다** |
| 층 분리 · n 병기 | ❌ 없음 (ADR-022 / ADR-005 Amd 2에만) | ❌ | ❌ bench 요약이 A+B+C 합산 pass/fail만 낸다(`:411-415`) | **빠짐 — 게다가 bench 출력이 ADR-022 위반 형태** |
| vLLM만 | ✅ 승인 게이트 · ADR-021 | ✅ `external-llm.md`, `security.md` | ✅ 훅 + `egress.py` | 일치 |
| 승인 · 소표본 | ⚠️ governance는 **할당 자원 호출을 승인 없이 허용**한다 | — | — | 계획이 governance보다 **엄격**하다. 충돌은 아니지만 **계획의 승인·소표본 규칙은 어떤 장치로도 집행되지 않는다** — 세션 운영으로 지켜야 한다 |

"최난도 먼저, 평균 나중" 표기 규칙도 governance에는 없고 ADR-005 Amd 2와 핸드오프에만 있다.
**governance.md는 수정하지 않았다(지시).** 반영 여부는 사용자 판단.

---

## 2. `bench_golden.py` × 골든셋 v2.0 — 변경 범위만 (수정 없음)

**결론: 스키마 수준에서는 돌아간다. 해석 수준에서 9건 가정이 남아 있다.** v2.0의 30건 모두
bench가 읽는 필드(`id`, `query`, `expect.{expect_uncovered, expected_doc_ids, must_mention}`)를
갖고 있어 크래시는 나지 않는다. 문제는 **무엇을 집계하고 무엇을 버리는가**다.

⚠️ **전제 정정:** 골든셋 v2.0은 **30건 전부 `language: ko`**다. "ko/en"의 en은 `golden-set-en.json`
(id → 영어 질의 매핑, `queries` 키)이며 그 파일 스스로 "**파이프라인은 이 파일을 읽지 않는다**, 검색 프로브에서만
쓴다"고 적혀 있다. 그리고 Writer 시스템 프롬프트가 "**한국어** 리서치 보고서"를 고정한다(`prompts.py:123`).

| # | 위치 | 9건 / v1.0 가정 | 필요한 변경 범위 |
|---|---|---|---|
| 1 | `bench_golden.py:54` | `GOLDEN_SET` 경로 고정, 한국어 파일만 | en 대조를 파이프라인에서 할지부터 결정. 한다면 `golden-set-en.json`(형식이 다름: `queries` dict) 로더 + 언어 인자 |
| 2 | `bench_golden.py:193-197` | `golden["cases"]`만 읽고 `version`을 버린다 | 결과 JSON에 `golden_set_version` 기록 (`probe_retrieval.py:252·483`이 이미 하는 방식) |
| 3 | `bench_golden.py:144-177` `_score` | `stratum`·`anchor_meta`를 보지 않는다 | 행(row)에 `stratum` 복사 |
| 4 | `bench_golden.py:161-167` `_score` | **`expect.min_citations`를 무시한다.** "기대 문서 1건 이상 적중"만 본다. GS-003은 `min_citations=2` | 판정 추가 여부 결정(판정 기준 변경 = 비교선 영향) |
| 5 | `bench_golden.py:411-415` 요약 `scoring` | **A+B+C 합산 pass/fail 1개** | 층별 집계 + n 병기. `probe_retrieval.py:467-503`의 `stratum_A`/`stratum_B` 패턴 재사용 가능. **이대로 30건을 돌리면 출력 자체가 ADR-022 위반 형태다** |
| 6 | `bench_golden.py:233` 루프 | 반복 1회. "30건×2회"를 지원하지 않는다 | `--repeat` 또는 외부에서 2회 실행 + 회차 라벨. 회차 간 대조 스크립트(`compare_probe_runs.py`와 같은 제외 목록 고정 방식) |
| 7 | `bench_golden.py:420` | `bench-{label}.json`을 `docs/eval/`에 **덮어쓴다** | `--label no-cache`로 돌리면 session-03 `bench-no-cache.json`이 사라진다. 라벨 규칙 또는 존재 시 거부 |
| 8 | `bench_golden.py:223-224` | 인덱스 무결성 확인 없음 | 기동 시 `index_guard.read_write_log` 1회 (session-16 §1.4가 제안한 형태, `retrieval.py` 무변경) |
| 9 | `bench_golden.py:216-220` | 임베딩만 워밍업 | Chroma 더미 질의 + `import litellm`을 워밍업 단계로 (§1 콜드 로딩) |
| 10 | `bench_golden.py:58-68` 주석 | "표본이 한 자릿수라" | n=30에서도 nearest-rank는 유효. 주석만 낡음 |
| 11 | `bench_golden.py:90-109` | 검색 지연을 JSONL 트레이스에서 되읽는다 — 로컬 트레이스가 꺼져 있으면 **조용히 0** | 트레이스 미존재 시 경고 |

**결정 대기 ①** — 위 중 어디까지 바꿀지 (§6).

---

## 3. 질의당 LLM 호출 수 — 코드 근거

### 3.1 구조

그래프(`graph.py:86-97`): outliner → researcher → verifier ⇄(retry) researcher → writer.

| 노드 | 호출 조건 | 근거 |
|---|---|---|
| Outliner | 항상 1회 | `nodes.py:244` |
| Researcher | 이번 회차 대상 항목마다 1회. 1회차 = outline 전체, 재시도 = **uncovered만** | `nodes.py:302-303, 308-330`. 검색은 점수 컷오프 없이 top-k만 자르므로 코퍼스 37건에서 **항상 후보 4건 → 항상 호출** |
| Verifier | **outline 전체** 중 누적 인용 ≥ `min_citations`(1)인 항목마다 1회. 0건 항목은 기계 판정(호출 없음) | `nodes.py:472-489` |
| Writer | outline이 비지 않으면 1회 (전 항목 근거 없음이어도 호출) | `nodes.py:539-563` |
| 라우터 | `revision >= max_revisions`면 종료. Researcher가 `revision+1` | `nodes.py:594`, `:346` |

⚠️ **`max_revisions`의 의미:** 기본 2 = **Researcher 최대 2회차(재시도 1회).** `max_revisions=0`과
`1`은 동작이 같다(1회차만). 이름과 달리 "재시도 횟수"가 아니다.

⚠️ **T(항목 수)의 상한은 코드에 없다.** "3~5개"는 Outliner 프롬프트 지시(`prompts.py:53`)일 뿐이고
`nodes.py:257-260`은 개수를 자르지 않는다. 아래 최대는 T ≤ 5를 **가정**한 값이다.

⚠️ **HTTP 재시도는 트레이스에 안 잡힌다.** `num_retries = max_retries = 2`(`llm.py:126`, `config.py:44`)
→ 논리 호출 1건이 엔드포인트 요청 **최대 3건**이 될 수 있다. `llm_calls`는 성공한 논리 호출만 센다
(실패 호출은 `_record`가 안 붙는다).

### 3.2 최소 · 기대 · 최대 (T = 항목 수, `max_revisions=2`)

| | 식 | T=3 | T=4 | T=5 |
|---|---|---:|---:|---:|
| **최소** (T≥1) | 2T + 2 | 8 | 10 | 12 |
| **최대** | 4T + 2 | 14 | 18 | **22** |

- 최소: 항목마다 최소 2호출(연구+검증, 또는 연구+재연구). 예: 1회차에 전부 covered, 또는 전부 인용 0건.
- 최대: 1회차 전부 인용 있음 → 전부 Verifier가 거절 → 전부 재연구 → 전부 재검증.
- (퇴화) outline 파싱 실패 → Outliner 1회만. 최소 1.
- **기대: T=4에서 ≈12.** 유일한 실측 근거는 session-08 `bench-session08-no-cache.json`
  (gemma-4-31B-it, 코퍼스 **16**, 골든셋 v1.0 **9건**): 호출 8–16, 평균 **12.0**, T 3–4,
  **9건 전부 재시도 발생(revision=2)**. 노드별 합계 Outliner 9 / Researcher 54(1회차 34 + 재시도 20) /
  Verifier 36(18+18) / Writer 9. ⚠️ 코퍼스·골든셋이 다르므로 **참고치다**, 30건 기대값이 아니다.

### 3.3 30건 × 2회 상한표 (60 실행)

| | 논리 호출 | 엔드포인트 요청(×3 재시도 상한) | 토큰(추정) | 벽시계(추정) |
|---|---:|---:|---:|---:|
| 최소 (T=3) | 480 | 480 | — | — |
| **기대 (T=4, ≈12/질의)** | **≈720** | ≈720 | ≈0.85M (session-08: 14,112 토큰/질의) | ≈17.5분 (session-08 평균 17.46s/질의) |
| **최대 (T=5, 22/질의)** | **1,320** | **3,960** | ≈1.55M (1,176 토큰/호출 × 1,320) | ≈37분 (session-08 최대 37.4s/질의 기준) — HTTP 타임아웃 120s가 겹치면 상한 없음 |
| 비용 | | | | **0원(할당분)** |

### 3.4 소표본 3건 구성안 (층 A/B/C 각 1)

| 케이스 | 층 | 고른 이유 |
|---|---|---|
| **GS-013** | A | ko 정답 **1위**인데 점수 0.8251 < 최난도 negative 0.8303. ADR-025 위험군 — baseline에서 Researcher가 고르는지가 나중에 전/후 비교의 기준점이 된다 |
| **GS-006** | B | 기대 문서 ko 순위 **12** (top-4 밖). session-08에서도 FAIL. **재시도 경로를 확실히 탄다** → 호출 수 상한 쪽 확인 |
| **GS-028** | C | 최난도 negative(0.8303). "근거 없음 유지"가 가장 어려운 케이스 |

대안: B를 **GS-003**(ko 1위, `min_citations=2` — §2 #4 판정 누락 확인용)으로 바꿀 수 있다.
소표본 상한: 3건×2회 = 6실행 → 논리 132 / 요청 396, 기대 ≈72.

**결정 대기 ②** — 호출 상한 (§6).

---

## 4. vLLM 디코딩 설정

| 파라미터 | 값 | 근거 |
|---|---|---|
| `temperature` | **0.0** — 전 노드 고정 | `nodes.py:133` (`_call` 단일 경로). Protocol 기본값도 0.0 (`llm.py:82`) |
| `top_p` | **보내지 않는다** → 서버 기본값 | `llm.py:119-131` payload에 없음. temperature 0(greedy)이면 무의미 |
| `seed` | **보내지 않는다** | 같은 곳. LiteLLM 1.101.0은 `openai/`·`hosted_vllm` 양쪽에서 `seed`를 지원 파라미터로 보고한다. **서버가 존중하는지는 호출 없이 확인 불가** |
| `max_tokens` | Outliner 512 / Researcher 384 / Verifier 256 / Writer 2048 | `nodes.py:250, 328, 487, 562` |
| `stop` | 없음 | |

**temperature는 0이다. 그런데 S0b 자연 변동 측정은 여전히 필요하다고 판단한다 — 근거가 이미 있다.**
session-08의 `no-cache`와 `cache-cold`는 둘 다 엔드포인트를 실제로 친 temperature 0 실행인데,
9건 중 **3건(GS-003·005·007)에서 completion 토큰이 다르고 2건은 초안 길이가 다르다**
(GS-005 689 → 534자). vLLM의 greedy는 배칭·수치 연산 순서 때문에 비트 단위 결정론을 보장하지 않는다.
**변동 폭을 모르면 v1.2의 어떤 전/후 차이도 효과로 읽을 수 없다.**
(주의: `cache-cold`는 회차 내 동일 프롬프트를 캐시로 돌려받아 두 실행의 호출 경로가 완전히 같지는
않다 — "비결정성이 있다"까지는 확실하고, 폭은 S0b에서 잰다.)

---

## 5. P1 · T2 사전 조사 (읽기만)

### 5.1 섹션별 조사의 순차 루프 — **두 곳**

- **Researcher** `nodes.py:308` `for topic in topics:` — 항목마다 검색(`_retrieve`) → LLM 호출이 **직렬**.
- **Verifier** `nodes.py:472` `for topic in outline:` — 항목마다 LLM 호출이 **직렬**.
- 둘 다 노드 **내부** 루프라 그래프 수준에는 병렬 지점이 없다. 순차 깊이(질의당) ≈ 호출 수 전체.

### 5.2 Verifier 재검증 루프 — **전체 단위**다 (Researcher 재시도는 섹션 단위)

- 라우팅은 전역(`route_after_verify`, uncovered가 하나라도 남으면 재시도).
- Researcher 재시도는 **uncovered 항목만** 다룬다(`nodes.py:302-303`) → 섹션 단위.
- **Verifier는 매 회차 outline 전체를 다시 판정한다**(`nodes.py:472`). 1회차에 covered였고 근거가
  그대로인 항목도 **똑같은 프롬프트로 재호출**된다.
  - 증거: session-08 Verifier 36 = 18(1회차) + 18(2회차). `cache-cold` 실행에서 **한 질의 안에서**
    캐시 적중 0–4건이 난 것이 바로 이 재판정이다(같은 입력 → 같은 키).
  - ⚠️ **정확성 함의:** §4의 비결정성과 겹치면, 1회차 covered 항목이 2회차 재판정에서 **uncovered로
    뒤집힐 수 있고**, 그대로 "근거 없음"으로 초안에 나간다. 관측된 적은 없으나 구조상 가능하다.
  - 섹션 단위로 바꾸면 호출 절감 + 뒤집힘 제거가 함께 되지만 **Verifier 동작 변경 = ADR-006 사안**이다.

### 5.3 LangGraph `Send` (fan-out) 지원

- **지원한다.** langgraph **1.1.4**에서 `from langgraph.types import Send` 성공.
  `add_conditional_edges`의 path가 `Hashable | Sequence[Hashable]`을 받는다(Send 리스트 반환 가능).
- ⚠️ **State 쪽 걸림돌:** `findings`·`trace`는 `operator.add` reducer라 병렬 분기 합류에 안전하다.
  **`revision`·`uncovered`·`outline`은 reducer 없는 덮어쓰기**다(`state.py:92-106`) — 병렬 분기가
  같은 스텝에 `revision`을 쓰면 LangGraph가 `InvalidUpdateError`를 낸다. Researcher가 지금
  `revision+1`을 반환하므로(`nodes.py:346`) **항목별 Send로 쪼개면 회차 증가를 합류 노드로 옮겨야 한다.**
- 병렬화는 오케스트레이션 구조 변경 = ADR-002 하네스 변수. 엔드포인트 동시 요청 수(할당 GPU가
  받아주는지)는 **호출 없이 확인 불가.**

### 5.4 LiteLLM → vLLM `tools` 전달 구조 — **지금은 전달되지 않는다**

| 계층 | 상태 | 근거 |
|---|---|---|
| LiteLLM 라이브러리 | ✅ 전달 가능. 1.101.0이 `openai/`(우리 경로)에서 `tools`·`tool_choice`·`parallel_tool_calls`를 지원 파라미터로 보고 | `get_supported_openai_params` |
| **우리 프로바이더** | ❌ `LLMProvider.complete` 시그니처에 `tools` 없음, payload를 **명시 필드로만** 조립 | `llm.py:78-85, 119-131` |
| 응답 파싱 | ❌ `message.content`만 읽는다 — `tool_calls`는 버려진다 | `llm.py:155` |
| 캐시 키 | ❌ `tools`가 키에 없다 → 추가 시 **도구 정의만 다른 호출이 같은 키로 충돌** | `cache.py:74-95` |
| 서버(vLLM) | **미확인.** vLLM은 `tool_choice="auto"`에 `--enable-auto-tool-choice --tool-call-parser` 기동 옵션이 필요하다. KT 엔드포인트는 우리가 소유하지 않으므로 옵션을 볼 수 없다 — **호출로만 확인 가능** | |

T2를 하려면 Protocol · `LiteLLMProvider` · `_to_response` · `CachingLLMProvider` · 테스트용 가짜 프로바이더가
함께 바뀐다(ADR-003 경계 안이지만 인터페이스 변경).

---

## 6. 🔴 사용자 결정 3건

| # | 결정 | 선택지 요약 | 참조 |
|---|---|---|---|
| **①** | **bench 변경 범위** | (a) 무변경으로 baseline — 단 출력이 층 합산이라 ADR-022 위반 형태, `min_citations` 무시, 덮어쓰기 위험 / (b) **측정 인프라만** 변경(#2 버전 기록, #3·#5 층별 집계, #6 반복, #7 덮어쓰기 방지, #8 무결성 검사, #9 워밍업) — 파이프라인·판정 기준 불변 / (c) (b) + 판정 기준 변경(#4 `min_citations`), en 파이프라인 실행 | §2 |
| **②** | **호출 상한** | 소표본 3건×2 = 논리 ≤132 / 요청 ≤396. 본측정 30건×2 = 논리 ≤1,320 / 요청 ≤3,960, 기대 ≈720. 비용 0원(할당분). 본측정 전 소표본 실측으로 기대값을 교정할지 | §3 |
| **③** | **점수 처리** | ADR-025 (가) 제거 / (나) 정규화 / (다) 경고. **그리고 순서:** v1.1(점수 노출) 상태로 baseline을 먼저 잴지 — session-13 §6.2는 "재측정보다 먼저 결정"이라 적었고, S0 방침은 "하네스 v1.1 그대로"다. 먼저 잰다면 baseline에 "점수 노출 상태"를 명기해 전/후 비교의 before로 쓴다 | ADR-025 |

---

## 7. 다음 세션 진입 조건

- [ ] **v1.2 계획 전문 저장** (§0)
- [ ] 결정 ①②③
- [ ] S0b 전: 승인 게이트 6항목 제시 — 1번(대상 엔드포인트 = `VLLM_BASE`)부터
- [ ] S0b에서 호출로만 확인 가능한 것: 자연 변동 폭(§4), `seed` 존중 여부, tool calling 서버 옵션(§5.4), 동시 요청 수용(§5.3)
- [ ] 측정 직전: `embeddings_queue` == 문서 수 재확인, 같은 인덱스 동시 실행 금지, `--cache` 미지정 확인

## 8. 승인 게이트 / 상태

- **LLM 호출 0건, 외부 API 호출 0건.** 유료 리소스·IaC 변경 0건.
- 인덱스는 **읽기 전용**으로만 열었다. 쓰기 없음.
- 코드 변경 0줄. `PROMPT_VERSION` `2026-09-16.1` 그대로. 테스트는 돌리지 않았다(코드 변경 없음).

| 항목 | 상태 |
|---|---|
| v1.2 계획 문서 | **미저장** — 본문 누락 (§0) |
| 하네스 점검 | ✅ 충돌 0 / 빠짐 4 (§1) |
| bench × v2.0 | ✅ 조사 완료, 변경 11개 지점 (§2) |
| 호출 수 · 상한표 · 소표본 | ✅ (§3) |
| 디코딩 설정 | ✅ temperature 0, 그러나 비결정성 관측 → S0b 필요 (§4) |
| P1·T2 사전 조사 | ✅ (§5) |
| ADR-025 | Proposed, 결정 미정 |
| 🔒 같은 인덱스 동시 실행 | **금지 유지** |
