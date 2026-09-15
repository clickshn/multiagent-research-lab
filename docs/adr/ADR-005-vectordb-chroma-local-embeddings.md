# ADR-005: VectorDB는 Chroma로 시작하고 임베딩은 로컬 모델로 내린다

- **Status:** Proposed
- **Date:** 2026-09-15
- **Decision:** 벡터 저장소는 Chroma 임베디드 모드로 시작하고, 임베딩은 서빙 엔드포인트가 제공하지 않으므로 로컬 sentence-transformers(`intfloat/multilingual-e5-small`)로 계산한다.
- **Scope:** multiagent-research-lab (검색 계층 — 벡터 저장소 + 임베딩)
- **Decision Source:** Human

---

## Context

### Problem

session-02 핸드오프에서 **VectorDB 선택이 Researcher 노드 구현을 막고 있는 항목**으로
기록됐다. 검색 결과에 문서 ID와 문서 내 위치가 항상 함께 와야 한다는 요구(출처 제시,
`docs/problem-statement.md` §3 목표 2)가 후보 선정의 1순위 기준이다.

여기에 더해, 임베딩을 어디서 계산할지가 정해지지 않았다. 추론은 KT Cloud AI Nexus의
vLLM 엔드포인트로 하고 있는데, 같은 엔드포인트가 임베딩도 서빙하는지 확인되지 않은
상태였다. 서빙한다면 임베딩 계산도 그쪽에 맡기는 것이 단순하다.

### Constraints

- 검색 결과에 `doc_id`와 `locator`가 항상 실려야 한다. 이게 안 되는 후보는 탈락이다.
- 이번 세션의 목표는 "동작 확인"이다. 별도 서버를 세우고 운영해야 하는 선택지는
  이번 세션 안에 끝나지 않는다.
- 외부 LLM 벤더 비의존 — 문서 내용이 타사 모델로 나가면 안 된다. 관리형 임베딩 API는
  이 제약에 걸린다.
- 세션 6에서 컨테이너화가 예정돼 있어, 그 시점에 저장소 형태를 다시 볼 기회가 있다.
- 대상 문서가 한국어·영어 혼재 환경이다 (`docs/problem-statement.md` §3 비목표).

## Decision

### Selected

- **Technology:** Chroma 1.5.5 (임베디드 PersistentClient) + sentence-transformers 5.1.1 (`intfloat/multilingual-e5-small`)
- **Architecture:** 노드는 `src/tools/retrieval.py`의 `Retriever` 프로토콜만 본다. 임베딩은 `src/providers/embeddings.py`의 `EmbeddingProvider` 프로토콜 뒤에 둔다. **두 결정은 서로 묶지 않는다** — Chroma의 기본 임베딩 함수를 쓰지 않고 우리가 계산한 벡터를 직접 넣는다. 그래야 벡터 DB 교체와 임베딩 모델 교체를 따로 되돌릴 수 있다.
- **Implementation:** 인덱스는 `var/chroma/`에 두고 커밋하지 않는다. 커밋 대상은 `data/corpus/`의 원본 스냅샷이며 `scripts/build_index.py`로 재생성한다. 컬렉션 메타데이터에 임베딩 모델 이름을 기록하고, 인덱스를 만든 모델과 현재 설정이 다르면 검색 시 예외를 던진다 — 모델이 바뀌면 벡터 공간이 달라져 에러 없이 결과만 이상해지는 조용한 실패가 나기 때문이다. 유사도는 코사인이며 임베딩을 정규화해서 넣는다.

## Rationale

1. **임베딩을 로컬로 내리는 것은 선택이 아니라 확인 결과다.** 엔드포인트의
   `/v1/embeddings`가 404를 반환한다(아래 Evidence). 현 서빙 경로는 생성 전용이므로
   임베딩은 다른 곳에서 계산해야 한다.
2. **로컬 임베딩은 벤더 비의존 제약을 깨지 않는다.** 모델 가중치가 로컬에 있고 문서
   내용이 어디로도 나가지 않는다. 판단 기준은 네트워크 도달 가능성이 아니라 데이터가
   향하는 곳이다 (`docs/problem-statement.md` §2).
3. **Chroma는 임베디드라 이번 세션 안에 붙는다.** 별도 서버 프로세스·컨테이너·접속
   설정이 없고 디스크 경로 하나면 된다. "동작 확인"이 목표인 단계에서 인프라 작업이
   선행되지 않는 것이 결정적이다.
4. **Chroma는 메타데이터를 문서와 함께 저장·반환한다.** `doc_id`·`locator`를 메타데이터로
   실어 검색 결과에 항상 함께 오게 할 수 있다 — 1순위 기준을 만족한다.
5. **다국어 모델이어야 한다.** 질의는 한국어, 문서(arXiv 초록)는 영어인 교차 언어 검색이
   기본 상황이다. `multilingual-e5-small`은 118M 파라미터로 CPU에서 돌아갈 만큼 가볍고
   한국어·영어를 같은 벡터 공간에 둔다.
6. **세션 6에 재검토 기회가 이미 있다.** 지금 서버형 DB를 고르면 이번 세션의 비용이
   커지는데, 컨테이너화 시점에 어차피 저장소 배치를 다시 봐야 한다. 그때 판단하는 것이
   정보가 더 많은 상태에서의 결정이다.

## Evidence

- **Experiment:** 엔드포인트 임베딩 지원 확인 (2026-09-15, `scripts/probe_embeddings.py`로 재현 가능)

  | 요청 | 결과 | 지연 |
  | --- | --- | --- |
  | `GET /v1/models` | HTTP 200 — `gemma-4-31B-it` 단일 모델, `max_model_len` 262144 | 0.12s |
  | `POST /v1/embeddings` (model=`gemma-4-31B-it`) | **HTTP 404** `{"detail":"Not Found"}` | 0.04s |

  → 현 엔드포인트는 생성 전용이며 임베딩을 서빙하지 않는다.

- **Benchmark:** 로컬 임베딩·인덱싱 실측 (CPU, Windows / Python 3.14)

  | 항목 | 값 |
  | --- | --- |
  | 임베딩 차원 | 384 |
  | 인덱싱 | 16건 / 19.9s (모델 첫 로딩 포함) |
  | e2e 검색 호출 | 6회, 실패 0 (run_id `20260915-152557-c34630`) |

## Alternatives

### Qdrant

- **Pros:** 필터링·스케일 특성이 좋고 운영 환경에 적합하다. 컨테이너 배포가 표준적이다.
- **Cons:** 별도 서버 프로세스가 필요해 이번 세션에 인프라 작업이 선행된다.
- **Rejected because:** 이번 세션의 목표가 "동작 확인"이라, 서버를 세우는 비용이 목표 달성을 지연시킨다.
- **Recheck if:** 세션 6 컨테이너화 시점 — 그때 Qdrant로 옮길지 별도 ADR로 다시 판단한다.

### FAISS

- **Pros:** 검색 속도가 빠르고 의존성이 가볍다.
- **Cons:** 메타데이터 저장을 직접 관리해야 한다 — 벡터 인덱스와 문서 메타데이터를 따로 들고 다니며 동기화해야 한다.
- **Rejected because:** `doc_id`·`locator`가 항상 함께 와야 한다는 1순위 기준을 우리가 직접 구현해서 유지해야 하는데, 그 동기화가 깨지면 출처가 틀린 인용이 나온다.

### 엔드포인트의 임베딩 API 사용

- **Pros:** 임베딩 계산을 로컬에 두지 않아도 되고, 모델 관리 지점이 하나로 줄어든다.
- **Cons:** —
- **Rejected because:** 엔드포인트가 `/v1/embeddings`를 제공하지 않는다 (실측 404). 선택지가 아니다.
- **Recheck if:** 엔드포인트가 임베딩 서빙을 추가하는 경우 (`scripts/probe_embeddings.py`가 200을 반환).

### 관리형 임베딩 API (OpenAI 등)

- **Pros:** 품질이 좋고 운영 부담이 없다.
- **Cons:** 문서 내용이 타사 LLM 벤더로 전송된다.
- **Rejected because:** 외부 LLM 벤더 비의존이라는 핵심 제약에 정면으로 걸린다 (`docs/problem-statement.md` §2).

## Consequences

### Positive

- 별도 서버 없이 이번 세션 안에 Researcher 노드가 동작한다.
- 벡터 DB와 임베딩 모델을 따로 교체할 수 있다 — 한쪽을 고정하고 다른 쪽만 바꿔가며 측정하는 이 프로젝트의 측정 원칙과 맞는다.
- 임베딩 모델 불일치가 조용한 품질 저하가 아니라 예외로 드러난다.

### Negative

- `sentence-transformers`가 torch를 전이 의존성으로 끌고 온다. 의존성 트리가 눈에 띄게 넓어졌다 (검증 환경 torch 2.9.0).
- CPU 임베딩이라 코퍼스가 커지면 인덱싱 시간이 선형으로 늘어난다. 지금 규모에서는 문제가 아니지만 수천 건 단위에서는 다시 봐야 한다.
- Chroma 임베디드는 단일 프로세스 전제다. 여러 실행이 동시에 인덱스를 쓰는 상황은 지원 범위 밖이다.

### Risks

- 교차 언어 검색(한국어 질의 → 영어 문서) 품질을 아직 측정하지 않았다. 골든셋이 생기기 전까지는 검색 재현율에 대한 근거가 없다.
- `multilingual-e5-small`은 경량 모델이라 도메인 용어에서 약할 수 있다. 검색 재현율이 목표에 못 미치면 모델 교체(인덱스 재생성 필요)가 먼저 검토 대상이다.

## Implementation

- [x] `EmbeddingProvider` 프로토콜 + sentence-transformers 구현 (`src/providers/embeddings.py`)
- [x] `Retriever` 프로토콜 + Chroma 구현 (`src/tools/retrieval.py`)
- [x] 인덱스 생성 스크립트 (`scripts/build_index.py`, `--reset` 지원)
- [x] 엔드포인트 임베딩 지원 확인 스크립트 (`scripts/probe_embeddings.py`)
- [x] 임베딩 모델 불일치 감지 (컬렉션 메타데이터 대조)
- [ ] 검색 재현율 측정 — 골든셋 확보 후
- [ ] 세션 6에서 Qdrant 이전 여부 판단 (별도 ADR)

## Reversibility

- **Reversible:** Yes
- **Rollback:** 벡터 DB 교체는 `Retriever` 프로토콜을 구현한 클래스를 하나 더 쓰고 `scripts/build_index.py`를 다시 돌리면 된다 — 노드 코드는 바뀌지 않는다. 임베딩 모델 교체는 `.env`의 `EMBEDDING_MODEL`을 바꾸고 `--reset`으로 인덱스를 재생성한다. 원본 스냅샷이 `data/corpus/`에 커밋돼 있어 재생성에 외부 의존이 없다.
- **Migration Cost:** Low

## Review Trigger

- 세션 6 컨테이너화 시점 — Qdrant 등 서버형 벡터 DB로 옮길지 별도 ADR로 판단한다.
- 엔드포인트가 임베딩 서빙을 추가하는 경우 (`scripts/probe_embeddings.py`가 200 반환) — 임베딩을 엔드포인트로 옮길지 재검토.

## References

- **Related ADR:** ADR-003 (서빙 토폴로지 — 임베딩 미지원의 출처), ADR-004 (이 인덱스에 넣는 코퍼스의 범위), ADR-006 (이 검색 결과를 판정하는 조건)
- **Documentation:** `docs/problem-statement.md` §2·§3, `src/tools/retrieval.py`, `src/providers/embeddings.py`, `scripts/probe_embeddings.py`

## AI/ML Details

- **Model:** `intfloat/multilingual-e5-small` (118M, 384차원, 다국어). e5 계열은 질의에 `query: `, 문서에 `passage: ` 접두사를 요구하며, 이를 빠뜨리면 에러 없이 검색 품질만 나빠진다 — 접두사를 설정(`EmbeddingSettings`)으로 들고 다니고 모델 이름에 `e5`가 없으면 자동으로 끈다.
- **Evaluation:** 골든셋 확보 후 검색 재현율 측정 예정. 현재는 기준선 없음.
- **Inference:** CPU, 정규화된 임베딩 + 코사인 유사도. 조사 항목당 기본 top-k=4.

### Evaluation

| Metric | Before | After | Target |
| ------ | -----: | ----: | -----: |
| 검색 재현율 (정답 근거가 상위 k에 포함) | — | 미측정 | TBD |
| 인덱싱 처리량 (건/s, CPU) | — | 약 0.8 (16건/19.9s, 모델 로딩 포함) | — |
