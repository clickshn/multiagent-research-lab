# Session 22 핸드오프 — v1.2-T2 Researcher `tech_domain` 선택 도구 (json_schema 경로)

- **날짜:** 2026-10-01
- **범위:** v1.2-T2. Researcher가 1회차 항목마다 `tech_domain` 필터 값을 고르게 했다. 경로는 json_schema이고, 네이티브 tool calling은 쓰지 않았다.
  **골든셋 전량 평가는 하지 않았다(T3로 넘김).** governance.md · `.claude/rules/` · 훅은 고치지 않았다.
- **ADR:** ADR-028 신규(**Proposed**)
- **테스트:** `python -m pytest tests/ -q` → **306 passed** (273 → +10 response_format · +2 스키마 · +21 도구)
- **LLM 호출:** **43건**(강제 판별 3 + 소표본 40). 목적지는 전부 `VLLM_BASE`(`***.ktcloud.com`, fp `1897f0ecc081`)이고 둘 다 사용자 승인을 받았다.
  외부 벤더 호출 0건, 유료 리소스·IaC 0건, 캐시 끔.
- **시크릿 검사:** `scan_local_secrets.py` 0건(양성 대조 통과)이고, 커밋마다 diff 대조 0건이었다.

## 커밋 순서

| 커밋 | 내용 |
|---|---|
| **`ce86b04`** | **사전 등록** `docs/plans/v1.2-preregistration.md` — 이 세션의 첫 커밋이고 모든 LLM 호출보다 앞선다 |
| `cdaad22` | response_format 전달 경로(시그니처·payload·캐시 키·입력 해시) + 테스트 10 |
| `d1fafc3` | 강제 판별 3건 결과 · `probe_schema_enforcement.py` · ADR-028 · `nodes.tech_domain_schema` |
| `b7dd925` | Researcher 배선 · 도구 on/off 설정 · bench 귀속 검사 확장 · 테스트 20 |
| `30413e0` | off = p1-on 입력 해시 재생 대조(스크립트·결과·테스트 1) |
| `3871937` | 소표본 3건 결과 · README 상태와 ADR 수(28 / Amendment 16) |
| (이 커밋) | 이 핸드오프 |

push 하지 않았다.

---

## 1. 사전 등록 — 해시 **`ce86b04`**, 임계값 (사용자 결정)

| # | 결정 |
|---|---|
| D1 | **1회차 항목에서만 선택한다. 재시도는 1회차 값을 재사용한다**(질의당 +3.3) |
| D2 | 정확 일치는 **항목 풀링**으로만 센다. 케이스 과반 일치는 관찰로 병기한다 |
| D3 | 층 A 해악 케이스 **1건 = 관찰, 2건 이상 = 해악** |
| D4 | 층 B e2e(기준 5/9, 절대 차이) **−1건 = 관찰, −2건 이상 = 해악** |
| D5 | 층 C 기권율은 기록만 한다. **e2e 근거 없음 유지가 1건이라도 하락하면 해악**(기준 7/7) |
| D6 | 정확 일치율 하한은 **없다(기록만)**. 판정은 **호환 일치와 해악**으로 한다 |
| D7 | 층 A fail→pass가 2건 이상이고 해악이 아니면 **"개선 관찰"** 로 적는다(효과 입증 아님) |
| 정지 | `FilterValueError` · 스키마 위반 · **`selection_error` ≥ 3건(T3)** · off 해시 불일치 · 귀속 불일치 · 캐시 켜짐 |

- `selection_error` 항목은 선택 지표의 분자와 분모에서 모두 뺀다. 건수는 따로 보고한다(사용자 추가).
- negative에서 기권이 정답인가에 대해서는 찬반 근거를 문서 §3.6에 적었다. 결정은 D5다(정답으로 채점하지 않는다).
- 해악의 적격 분모(p1-on 1회차, 무필터 top-4에 정답이 있는 항목)는 층 A 36개(12케이스), 층 B 16개(6케이스)다.
  층 B 정답 문서에는 온톨로지 메타가 없다. 그래서 **층 B에서는 기권만 무해하다.**

## 2. response_format 경로 — **tools와 같은 문제가 있었고, 고쳤다**

| 위치 (session-17 §5.4 대응) | 이전 | 지금 |
|---|---|---|
| 프로바이더 시그니처·payload | 인자 없음 | `complete(..., response_format=None)`. None이면 **키 자체를 넣지 않는다** |
| 응답 파싱 | — | **변경 불필요.** 구조화 출력은 `message.content`에 JSON 문자열로 온다(테스트로 고정) |
| 캐시 키 | 스키마 빠짐 → 스키마만 다른 호출끼리 충돌 | 스키마를 키에 넣는다. None이면 **기존 키와 바이트 동일**(옛 직렬화와 대조하는 테스트) |
| (추가) span 입력 해시 | 빠짐 | 스키마를 해시에 넣는다. None이면 p1-on 해시와 같다 |

- LiteLLM 1.101.0은 `openai/` 경로에서 `response_format`을 **변형 없이** 요청 본문에 싣는다. tools로 바꾸지 않는다.
  로컬 127.0.0.1 가짜 서버로 HTTP 본문을 직접 확인했다(`tests/test_response_format_path.py`).

## 3. 강제 판별 — **강제 확인** (3건, `docs/eval/t2-schema-enforcement.json`)

| 호출 | 스키마 | 출력 |
|---|---|---|
| control | 없음 | `Agent. 여러 에이전트 간의 역할 분담과…` — 자연어. 프롬프트("JSON을 쓰지 마라")가 효과가 있다 = 판별력 있음 |
| enforce1 | json_schema | `{"tech_domain": "Agent"}` — 지시를 어기고 스키마대로 나왔다 |
| enforce2 | json_schema | `{"tech_domain": "없음"}` — 어휘 밖 주제에서 기권 값이 enum 안에서 나왔다 |

- **유효 파라미터: `response_format={"type":"json_schema","json_schema":{name,strict,schema}}`.**
  ② `extra_body`(`guided_json` / `structured_outputs`)는 필요 없었다.
- ADR-028에 기록했다. 생산자 레포 ADR-019(vLLM 이전 시 모르는 파라미터를 조용히 무시)를 근거로 인용했다.
- n=3이므로 "항상 걸린다"는 증명이 아니다. 그래서 위반 시 정지 규칙을 유지한다. T3의 모든 선택 호출이 이 판별을 이어서 검증하는 셈이다.

## 4. 배선 — 무엇이 어떻게 바뀌었나

- `make_researcher_node(..., tech_domain_vocab=None)`. 값을 주면 도구가 켜지고, `compile_graph`와 진입점에서 이 값을 넘긴다.
- 흐름: **선택 호출(`researcher_select`, max_tokens 64) → `_retrieve(tech_domain=…)` → 기존 Researcher 호출.**
  선택 호출은 `research_one` 안에 있어서 동시 실행 경로(상한 4)를 그대로 탄다. 검색 락은 그대로다(테스트에서 검색 동시 진입 최대 = 1).
- 재시도: 1회차 Finding(`tech_domain`, `tech_domain_outcome` 필드 추가)에서 값을 가져온다. State 키는 새로 만들지 않았다.
- 정지:
  - 스키마 위반(JSON 아님 · 코드펜스 · 키 추가 · enum 밖) → `TechDomainSelectionError`
  - 어휘 밖 값 → `FilterValueError`
  - 둘 다 삼키지 않는다. bench는 `EXIT_SELECTION=7`로 멈추고 부분 결과를 저장하지 않는다.
  - **파싱은 엄격하다.** `_extract_json`의 코드펜스 흡수를 쓰지 않는다. 흡수하면 강제가 꺼진 출력도 통과하기 때문이다.
- `LLMError` → `selection_error`로 기록하고 무필터로 진행한다. bench는 실행 전체에서 누적 3건이면 멈춘다(Finding에서 센다 — 트레이스가 꺼져 있어도 동작한다).
- span:
  - `researcher_select`: 원문 응답(output), `selected`·`selection_outcome`·`input_hash`·`(revision, item_index)`
  - `researcher_retrieve`: 입력에 `tech_domain`·`tech_domain_source`(selected/reused)·`tech_domain_outcome`
  - `retrieval_filter`: `_KeyedTrace`로 같은 항목 키를 붙여 필터 후 결과 수를 남긴다
  - **도구 off이면 셋 다 v1.2-P1과 같은 모양이다.**
- **`--require-trace` 귀속 검사를 확장했다**(`selection_attribution_problems`). 확인하는 것:
  - 선택·필터 span 키 중복
  - 1회차 항목당 선택 span 1개, 재시도 항목에는 0개
  - 선택값 = Finding = 검색 span 입력
  - 재사용 값 = 1회차 값
  - 필터 `returned` = 후보 수
  - off 행에 선택·필터 span이 없음
  - 위조 테스트 4종을 전부 검출했다.
- 선택 프롬프트(`TECH_DOMAIN_SELECT_SYSTEM/USER`)를 추가하고 **PROMPT_VERSION을 `2026-10-01.1`로 올렸다.**
  - 기존 4노드 프롬프트는 바꾸지 않았다.
  - few-shot 3개는 전부 합성 문장이고, 골든셋 질의가 들어 있지 않음을 테스트로 확인했다.
  - 출력 형식 지시는 넣지 않았다(스키마가 강제한다).
- **내가 정한 것(지시에 없음):**
  - 프롬프트가 "고른 영역으로 검색이 제한되고, '없음'은 전체 검색"이라는 결과를 모델에게 알린다.
  - 재시도 항목에 1회차 선택이 없으면 예외로 처리한다(버그일 때만 생기는 경우).
  - 엔드포인트 오류 시 무필터로 진행한다(사전 등록 §3.3에 등록했다).

## 5. 도구 on/off — off = p1-on (**30/30 케이스, 297/297 호출 입력 해시 동일**)

- 설정: `RESEARCH_TECH_DOMAIN_TOOL`(기본 off, 오타 값은 거부) / bench `--tech-domain-tool on|off`. 결과 JSON에 `tech_domain_tool`·`tech_domain_selection`을 기록한다.
- `scripts/check_tool_off_parity.py`: 현재 코드의 off 경로를 **p1-on 트레이스의 응답으로 재생**한다.
  - 프로바이더는 입력 해시로 응답을 찾아 돌려주고, 해시가 없으면 즉시 불일치로 처리한다. 검색은 실제 로컬 인덱스를 쓴다. LLM 호출은 0건이다.
  - → `docs/eval/t2-tool-off-parity-p1-on.json`
- **양성 대조:** `VERIFIER_USER`에 1글자를 더하면 불일치가 검출되고 exit 1이 난다.
- pytest(`test_tool_off_replays_p1_on_with_identical_input_hashes`)는 로컬 트레이스가 있을 때만 돈다(skipif). var/traces는 커밋되지 않는다.
- ⚠️ PROMPT_VERSION 값은 span 메타데이터로만 바뀐다. LLM 입력에는 없다. off 회차의 `prompt_version` 표기가 p1-on과 다른 것은 이 때문이다.

## 6. 소표본 3건 e2e — `docs/eval/bench-v1.2-t2-pathcheck.json` (**경로 확인용, 판정에 쓰지 않는다**)

조건: tool on, parallel on(4), `--require-trace`, 캐시 끔, 인덱스 queue 37 == 문서 37.
결과: **귀속 불일치 0 · selection_error 0 · 엔드포인트 오류 0.** 1회차 Outliner 항목은 3건 모두 p1-on과 같았다.

| 케이스 | 층 | 선택(1회차) | 필터 후 생존 / 반환 | 재시도 | 호출 (p1-on) | pass (p1-on) |
|---|---|---|---|---|---|---|
| GS-013 | A | Multimodal · **Eval/Governance** · Multimodal | 3/3 · 13/4 · 3/3 | 1항목 재사용 | 15 (12) | pass (pass) |
| GS-006 | B | Eval/Governance × 4 | 13/4 × 4 | **4항목 전부 재사용** | 14 (10) | fail (fail) |
| GS-028 | C | Inference/Serving × 3 | **1/1** × 3 | 3항목 재사용 | 11 (8) | pass (pass) |

### 🔑 GS-006 재시도 재사용 — **확인됨**

- 1회차 4항목(item 0~3)이 전부 `Eval/Governance`를 골랐다(`source=selected`, 선택 span 4개).
- 재시도 회차(revision 1)의 4항목은 **전부 `source=reused`, 값 `Eval/Governance`, outcome `chosen`** 이다. 1회차의 같은 topic과 값이 같다.
- **재시도 회차의 선택 호출은 0건**이다(revision 1 선택 span 없음, `selected=None`).
- 재시도 검색에도 필터가 걸렸다. revision 1 키를 단 `retrieval_filter` span 4개, 각 survivors 13 / returned 4.
- 이 사실을 귀속 검사(`재사용 값이 1회차와 다르다` / `1회차 항목이 아닌 곳에 선택 span`)가 검증했고, 불일치는 0이었다.

### 관찰 (판정 아님, n=3)

- **기권 0/10.** 층 C(GS-028)의 어휘 안 주제는 예상대로 도메인을 골랐다(§3.6의 "반" 근거와 맞는다).
- **GS-028은 필터 후 생존 문서가 1건이었다.** Researcher는 후보 1건만 받았고, 근거 없음을 유지해 pass했다.
  후보 풀이 1건까지 줄어드는 항목이 생긴다는 것을 T3 기록 항목으로 둔다.
- **GS-006(B)은 모든 항목이 필터를 걸었다.** 그래서 정답 문서(메타 없음)가 구조적으로 걸러졌다.
  p1-on에서도 정답이 1회차 top-4에 없었으므로(0/4 적격) 사전 등록 정의상 해악은 아니다. 다만 층 B 정답이 top-4에 있는 케이스에서는 이것이 그대로 해악이 된다.
- GS-013 item 1의 `Eval/Governance`는 오라클(Multimodal)과 **정확 일치는 아니지만 호환된다**(정답 문서 도메인에 포함). 정답이 여전히 후보에 있었고 선택됐다.
- 원문 응답은 `{ "tech_domain": "Eval\/Governance" }`처럼 `/`를 이스케이프하고 공백·줄바꿈이 섞인다. 유효한 JSON이고 strict 파싱을 통과했다.
- 호출 증가는 **정확히 +T**(14−10=4, 15−12=3, 11−8=3)다. Researcher와 Verifier 호출 수는 p1-on과 같았다.

## 7. 🔴 T3 호출 상한안

| | 값 |
|---|---|
| 기대 | **≈396** 논리 호출 (p1-on 297 + 1회차 항목 99). 소표본 실측 +T와 일치한다 |
| 상한 (질의당 5T+2, T≤5) | **810** 논리 / **2,430** 엔드포인트 요청 (`num_retries`=2) |
| 정지 | `selection_error` 누적 3 · 스키마 위반 1 · `FilterValueError` 1 · 귀속 불일치 1 |
| 비용 | 0원(할당분) |
| 명령(안) | `python scripts/bench_golden.py --label v1.2-t3 --tech-domain-tool on --require-trace` (parallel 기본 on, `--cache` 미지정) |

## 8. 🔴 결정 대기 / 열린 것

| # | 항목 | 참조 |
|---|---|---|
| ① | **ADR-028 Status** — Proposed. Accepted로 올릴지 | ADR-028 |
| ② | **T3 승인**(위 상한, 승인 게이트 6항목) | §7 |
| ③ | T3 분석 스크립트(호환 일치·해악률·이득·층별 e2e)가 아직 없다. 해악은 T3 트레이스의 `search_text`를 무필터로 재생해 잰다(사전 등록 §3.4). **T3 실행 전에 만들고, p1-on 데이터로 먼저 검증하는 것을 권한다** | 사전 등록 §3 |
| — | bench 행 출력이 노드명 앞 3글자를 써서 `researcher`와 `researcher_select`가 둘 다 `res`로 보인다(표시만의 문제, JSON은 정확하다) | `bench_golden._print_row` |
| — | 이어지는 것: ECR 이미지 빌드 시점 · `where` 경로 HNSW 완전성 · session-20의 열린 항목들 | session-21 §8 |

## 9. 다음 세션(T3) 진입 조건

- [ ] 결정 ①②
- [ ] 사전 등록 `ce86b04`의 임계값을 그대로 쓴다. 바꾸려면 Amendment를 남기고 T3 **전에** 커밋한다
- [ ] 비교 기준 `docs/eval/bench-v1.2-p1-on.json` · 도구 on · parallel on(4) · 캐시 끔 · `--require-trace`
- [ ] T3 분석 스크립트를 먼저 만든다(§8 ③)
- [ ] 측정 직전: queue == 문서 수, 같은 인덱스 동시 실행 금지
- [ ] 승인 게이트 6항목 — 1번 `VLLM_BASE`부터
