# Session 21 핸드오프 — v1.2-T1 `tech_domains` 필터 배선 (오라클 검증)

- **날짜:** 2026-10-01
- **범위:** v1.2-T1. 바뀐 것은 **검색 계층에 선택 필터 인자 하나**다. 에이전트·프롬프트·노드 배선은 그대로이고, LLM 호출은 0건이다.
  **인덱스는 다시 만들지 않았다.** governance.md · `.claude/rules/` · 훅 수정 없음.
- **ADR:** ADR-027 신규(**Proposed**, 방식 B — 사용자 결정) · ADR-023 Amendment
- **테스트:** `python -m pytest tests/ -q` → **273 passed** (256 + 신규 17, `tests/test_retrieval_filter.py`)
- **LLM 호출:** **0건.** 외부 벤더 0건, 유료 리소스·IaC 0건. 임베딩은 로컬, 인덱스는 읽기만 했다.
- **시크릿 검사:** `scan_local_secrets.py` 0건(양성 대조 통과), 커밋 diff 대조 0건 (커밋마다)

## 커밋 순서

| 커밋 | 내용 |
|---|---|
| `eb4dd9e` | 코드: `retrieval.py` `tech_domain`·`trace` 인자 · `FilterValueError` · 테스트 17건 · `_LIST_JOIN` docstring 갱신 |
| `75e4430` | 도구: `probe_retrieval.py --filter-impl retrieval` · `scripts/replay_retrieval_trace.py` |
| `fb75778` | 결과: 프로브 2건 + 재생 대조 1건 (`docs/eval/`) |
| `bc29c9f` | ADR-027 · ADR-023 Amendment · README 상태 "필터 배선 완료(오라클 검증)" · ADR 수/표 |
| (이 커밋) | 이 핸드오프 |

push 하지 않았다.

---

## 0. P1 후속 확인 (코드 읽기만 함, 둘 다 문제 없음)

| 확인 | 결과 | 근거 |
|---|---|---|
| `input_hash`가 4,000자 잘림 **전** 전체 입력으로 계산되는가 | **예.** `_call()`이 메시지 원문 전체(`messages`·`temperature`·`max_tokens`)로 해시한 뒤 span 메타에 붙인다. 잘림은 `_safe()`가 JSONL을 쓸 때만 일어난다 | `src/orchestrator/nodes.py:111-127, 181`, `src/obs/tracer.py:116` |
| → P1 "입력 동일 297/297"의 잘린 7건 | **전체 입력이 같다는 증거다.** 앞부분만 같다는 뜻이 아니다 | 위와 같음 |
| 배포 진입점이 `RESEARCH_PARALLEL`을 읽는가 | **예.** ECS·compose 모두 `containerOverrides`/`run`으로 `scripts/run_research.py`를 실행하고, 이 스크립트가 `load_concurrency_settings()`를 호출해 `max_concurrency=concurrency.effective`를 넘긴다. `ecs.tf` environment와 `.env`에 이 키가 없으므로 값은 빈 문자열이고, 즉 **켜짐(4)** 이다 | `scripts/run_research.py:81,118`, `src/providers/config.py:378-394`, `infra/terraform/ecs.tf:99-112` |
| ⚠️ 단서 | README의 "기본 켜짐"은 **코드 기준으로 맞다.** 실제 배포에 반영되는지는 ECR에 푸시된 이미지가 `668b207` 이후 빌드인지에 달렸다. 이것은 코드만으로 확인할 수 없다 | — |

## 1. 배선 방식 — **B (전수 조회 + 후처리), 재인덱싱 없음** (사용자 결정)

- 나는 A(도메인별 불리언 키 + `where`, 재인덱싱)를 권했고 반대 근거도 함께 제시했다. **사용자가 B로 결정했고**, 조건은 아래와 같다.
  - k는 설정값이 아니라 **항상 컬렉션 문서 수**다. k<N 경로를 만들지 않는다 → 구현됨.
  - **필터 후 결과 수를 트레이스에 기록**한다 → `retrieval_filter` span.
  - ADR에 A의 기각 이유(재인덱싱 없이 p1-on 기준 유지 · where 경로 완전성 미측정 · 이번 주 범위)와 Review Trigger(문서 수가 수천 단위로 늘 때 / A3 유실을 해결할 때)를 적는다 → ADR-027.
  - **2·3번(키 펼치기, 백업·재인덱싱)은 건너뛴다.** 인덱스 디렉터리를 백업하지 않았다. 지울 것도 바꿀 것도 없었기 때문이다.

## 2. 인덱스 형태 — **변경 없음**

- 저장 형태는 그대로다. `tech_domains`는 `|`로 이은 문자열이고, `build_index.py`는 건드리지 않았다.
- 현재 인덱스: 문서 37건, `embeddings_queue` 37행 clean(p1-on `index_check`와 같다). 이번 세션에서 쓰기 0건.
- 변경 전 확인: 코드를 바꾸기 **전에** 현재 인덱스·현재 코드로 무필터와 td-strict 프로브를 돌렸다. 둘 다 세션 14 결과와 케이스·요약 단위로 같았다. 즉 현재 인덱스는 v1.1 값을 그대로 재현한다(scratchpad, 커밋 안 함).

## 3. 허용오차 — **0 (완전 일치)**

- 내가 제안한 값은 "개별 점수 |Δ| ≤ 0.0001, 간격은 4자리 정확 일치"였다. **사용자가 0을 택했다.** 문서 ID·순위·점수(저장 정밀도 4자리) 모두 정확히 같아야 한다.

## 4. 필터 인자 인터페이스

```python
ChromaRetriever.search(query, *, k=4, sources=None, tech_domain: str | None = None,
                       trace: RunTrace | None = None) -> list[RetrievedChunk]
```

| 항목 | 동작 |
|---|---|
| `tech_domain=None` | **기존 경로와 같다.** `n_results=k`, `where`는 `sources`가 있을 때만 붙고, `count()`·`get()`을 부르지 않으며 `trace`를 무시한다(테스트로 고정) |
| 받아오는 건수 | **항상 전수.** `collection.count()`이고, `sources`가 있으면 `get(where=…)` 건수. 호출자의 `k`는 필터 **뒤** 자르는 데만 쓴다 |
| 판정 | **strict.** `tech_domain in chunk.tech_domains`. 메타가 없는 문서는 탈락하고, 생존자는 무필터 순위 순서를 유지한다(v1.1 `_survives` 정의) |
| 값 검증 | 통제어휘(export manifest 합집합) 밖이면 **`FilterValueError(ValueError)`.** `RetrievalError`가 아니다 — Researcher가 삼키면 오타가 "생존자 0"으로 보이기 때문이다. 대소문자도 구분한다 |
| 전수 미수신 | `RetrievalError`("전수를 받지 못했다", ADR-005 A3). span에 `expected`·`fetched`가 남는다 |
| 트레이스 | `trace`를 주면 `retrieval_filter` span을 남긴다. input `{tech_domain, null_policy:"strict", k}`, metadata `{expected, fetched, survivors, returned}`, output은 반환 후보 `[{doc_id, score}]` |
| `Retriever` 프로토콜 | 같은 선택 인자를 추가했다(additive). **노드는 아직 이 인자를 넘기지 않는다** |

- 단일 값만 받는다. null-통과 정책은 구현하지 않았다(v1.1 오라클 arm이 strict였으므로).
- **내가 정한 것(지시에 없음):** 어휘 밖 값을 별도 예외로 처리한 것, 전수 미수신을 예외로 처리한 것, `trace`를 검색 계층 인자로 받은 것. `trace`를 인자로 받은 이유는 노드가 범위 밖이라 생존자 수를 기록할 다른 자리가 없었기 때문이다. 노드 배선 때 `_retrieve()`가 이 `trace`를 넘기면 된다.

## 5. 동일성 결과 — **전부 일치. 되돌림 없음**

| 대조 | 기준 | 결과 |
|---|---|---|
| **None** — 새 코드 무필터 프로브 (골든 30 × ko·en, k=37 전 순위) | `probe-retrieval-session-14-baseline-nofilter.json` · `…session-16-after-chunk-metadata.json` | **바이트 동일**(`compare_probe_runs.py`, 시간 키만 제외). sha256 `00f6353b…`가 기준과 같다 |
| **오라클** — `--arm tech_domain --null-policy strict --filter-impl retrieval` | `probe-retrieval-session-14-tech-domain-strict.json` | **바이트 동일.** sha256 `6463fa18…`. 층 A ko **14/14**(en 14/14) · 간격 ko **0.0286 / 0.0502**(en 0.0705 / 0.0900) · 층 B strict ko·en **0/9** |
| **실제 검색어 재생** — p1-on·off·pathcheck 트레이스의 `researcher_retrieve` **340건**, `k=4`, 무필터 | 트레이스에 기록된 후보 | **340/340 일치**(ID·순위·점수, 허용오차 0). span 수가 행의 `retrieval_calls` 합과 같다(161 · 161 · 18) |
| 재생 검사기 양성 대조 | 트레이스 사본에 순위 맞바꿈 1 · 점수 +0.0001 1 | **2/2 검출**(종료 1). 사본은 즉시 삭제했다 |

- ⚠️ **바이트 비교에서 유일하게 다른 것은 새로 추가한 `summary.filter_impl` 키 1개다**(옛 파일에는 없음). 이 키를 뺀 사본으로 다시 비교하면 OK이고 sha256도 같다. 커밋한 결과 파일에는 이 키가 들어 있다.
- ⚠️ **층 B strict 0/9는 검색 경로가 낸 값이 아니다.** 층 B는 필터 값이 선언되지 않았다. 검색 인자로는 이것을 표현할 수 없으므로(None = 무필터) 프로브가 strict의 정의대로 생존자 0을 넣는다. v1.1과 같은 정의이며, 검색 계층이 검증한 것은 층 A 14건과 negative의 값별 최고점이다.
- 원자료: `docs/eval/probe-retrieval-v1.2-t1-nofilter.json` · `probe-retrieval-v1.2-t1-tech-domain-strict-retrieval.json` · `t1-retrieval-replay-p1.json`

```
python scripts/probe_retrieval.py --label v1.2-t1-nofilter
python scripts/probe_retrieval.py --arm tech_domain --null-policy strict --filter-impl retrieval --label v1.2-t1-tech-domain-strict-retrieval
python scripts/compare_probe_runs.py docs/eval/probe-retrieval-session-14-tech-domain-strict.json docs/eval/probe-retrieval-v1.2-t1-tech-domain-strict-retrieval.json
python scripts/replay_retrieval_trace.py docs/eval/bench-v1.2-p1-on.json docs/eval/bench-v1.2-p1-off.json docs/eval/bench-v1.2-p1-pathcheck.json --out docs/eval/t1-retrieval-replay-p1.json
```

## 6. 🔑 p1-on 비교 기준은 유효한가 — **유효하다**

**논리:**

1. S0c 이후 Researcher 프롬프트에는 후보의 **ID·순서·본문**만 들어가고 **점수는 들어가지 않는다**(ADR-025, p1-on `candidate_score_exposed: false`).
   그러므로 무필터 검색의 **문서 ID·순위가 같으면 파이프라인 입력이 같다.**
2. 이번 변경에서 무필터 경로는 바뀌지 않았다. 재인덱싱도 없었다.
3. 이를 두 겹으로 확인했다.
   - 골든 질의 30 × 2언어의 k=37 전 순위가 v1.1과 바이트 단위로 같다.
   - **파이프라인이 p1-on에서 실제로 던진 검색어 161건**(+ off 161, pathcheck 18)을 다시 실행한 결과가 기록과 ID·순위·**점수까지** 같다.
     두 번째 확인은 골든 질의라는 표본이 아니라 **기준 회차의 실제 입력 그 자체**를 대조한 것이다.
4. 따라서 **T2·T3는 `bench-v1.2-p1-on.json`을 비교 기준으로 계속 쓸 수 있다.** 단, T2·T3에서 노드가 필터를 넘기기 시작하면 그것이 그 측정의 "바뀐 변수 하나"다.

⚠️ 범위: 이 결론은 **현재 인덱스·현재 코퍼스 37건·현재 임베딩 핀**에 한정된다. 재인덱싱하면 같은 재생 대조를 다시 돌린다(스크립트가 그 용도다).

## 7. 지시와 다르게 / 추가로 한 것

| 항목 | 내용 |
|---|---|
| 2·3번 | **건너뜀**(사용자 결정 B). 백업·`--reset`·펼친 키 테스트 없음 |
| 재생 대조 | 사용자 승인을 받고 추가했다. 스크립트를 **커밋**했다(재인덱싱 때 다시 쓸 수 있게) |
| 변경 전 프로브 | 코드를 바꾸기 전에 현재 인덱스로 v1.1 값이 재현되는지 먼저 확인했다(scratchpad) |
| `contract_import._LIST_JOIN` docstring | "필터를 걸려면 저장 형태부터 다시 정한다" → ADR-027 내용으로 갱신(동작 변경 없음) |
| ADR-023 Amendment | "`retrieval.py` 변경 0줄" 전제가 깨졌으므로 기록했다(governance: 전제가 바뀌면 Amendment) |
| README | 요청한 상태 줄 외에 수치 두 가지를 정정했다. 요약표 "ADR 25건 (+ Amendment 14건)"은 **세션 20 시점에 이미 어긋나 있었다**(실제 26 / 15). 이번 변경을 반영해 27 / 16으로 고쳤다. §8 범위는 "~ ADR-027", §8.1에 027 행을 추가했다 |

## 8. 🔴 결정 대기 / 열린 것

| # | 항목 | 참조 |
|---|---|---|
| ① | **ADR-027 Status** — Proposed. Accepted로 올릴지 | ADR-027 |
| ② | 노드 배선: Researcher `_retrieve()`가 `tech_domain`·`trace`를 넘기는 것, 그리고 값을 고르는 **플래너**. 지금 값은 오라클이라 개선폭은 상한이다 | ADR-023 Negative · ADR-027 |
| ③ | `FilterValueError`는 Researcher에서 잡히지 않는다(의도: 오타면 실행이 멈춘다). 플래너가 값을 만들게 되면 이 정책을 다시 본다 | §4 |
| — | ECR 이미지가 `668b207` 이후 빌드인지 미확인(§0) | — |
| — | `where` 필터 경로의 HNSW 완전성은 미측정이다(A의 Recheck 조건과 연결) | ADR-027 |
| — | session-20에서 넘어온 것: 엔드포인트 동시 수용량 · 내부 재시도로 흡수된 429 · 텍스트 층 비결정성 원인 · Langfuse 스레드 안전성 · session-19 4건 | session-20 §10 |

## 9. 다음 세션 진입 조건

- [ ] 결정 ①(ADR-027 Status)
- [ ] T2·T3 비교 기준 = `docs/eval/bench-v1.2-p1-on.json` (유효, §6). 기본값(on, 상한 4)으로 잰다
- [ ] 노드에서 필터를 켜면 그것이 바뀐 변수 하나다(ADR-002). 판정 기준은 측정 전에 등록한다
- [ ] 재인덱싱이 생기면 `replay_retrieval_trace.py`로 p1-on 재생 대조를 먼저 돌린다
- [ ] 승인 게이트 6항목 — 1번 `VLLM_BASE`부터
