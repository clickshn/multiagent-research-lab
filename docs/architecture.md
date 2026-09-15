# Architecture — v1.2

> 상태: v1.2 개념도 (2026-09-15). 박스 단위의 논리 구성만 표현하며, 배포 토폴로지·스케일링은
> 이후 세션에서 별도 문서로 다룬다.
> v1.1에서 서빙 계층(원격 vLLM 엔드포인트)과 프로바이더 추상화를 반영했고 (ADR-003),
> v1.2에서 검색 계층(Chroma + 로컬 임베딩, ADR-005)과 계측 계층(Tracer 파사드, ADR-007)을
> 실제 구현에 맞게 갱신했다. Verifier가 도식에 정식으로 들어왔다.

## 개념도

```mermaid
graph LR
    User[User<br/>리서치 질의]
    Orch[Orchestrator<br/>LangGraph]
    Agents[Agents<br/>Outliner / Researcher<br/>Verifier / Writer]
    Prov[Provider Layer<br/>LiteLLM 추상화]
    Emb[Embedding Provider<br/>로컬 sentence-transformers]
    VDB[(VectorDB — Chroma<br/>공개 코퍼스 인덱스)]
    Obs[Tracer 파사드<br/>로컬 JSONL + Langfuse]

    subgraph ext [외부 인프라 — KT Cloud AI Nexus]
        VLLM[vLLM 엔드포인트<br/>gemma-4-31B-it]
    end

    User --> Orch
    Orch --> Agents
    Agents --> Prov
    Prov -- OpenAI 호환 /v1 --> VLLM
    Agents -- Researcher --> VDB
    VDB --- Emb
    Orch -.계측.-> Obs
```

**경계.** 점선 안(`vLLM 엔드포인트`)만 우리가 운영하지 않는 영역이다. Provider Layer가
그 경계를 감싸므로, 엔드포인트가 바뀌어도 왼쪽 박스들은 영향을 받지 않는다.

## 구성 요소

| 박스 | 역할 |
| --- | --- |
| **User** | 리서치 질의를 입력하고, 생성된 초안을 검증·편집한다. |
| **Orchestrator** | 질의를 받아 에이전트 실행 순서와 상태를 관리한다. LangGraph의 State 그래프로 구현하며, 검증 루프(근거 누락 시 재검색)도 여기서 제어한다. |
| **Outliner / Researcher / Verifier / Writer** | Outliner는 질의를 조사 항목으로 분해하고, Researcher는 항목별로 근거 문서를 검색·추출하며, Verifier는 근거가 실제로 그 항목을 뒷받침하는지 판정하고(ADR-006), Writer는 근거에 붙은 초안을 작성한다. 네 에이전트는 동일한 모델을 공유하고 프롬프트·툴 스코프로만 구분된다 — 모델 고정 원칙(ADR-002)이 여기서 구체화된다. |
| **Provider Layer** | 모든 모델 호출이 지나는 단일 통로 (`src/providers/`). LiteLLM으로 OpenAI 호환 엔드포인트를 호출하며, 엔드포인트 주소·모델 이름은 `.env`로 주입한다. 이 계층이 있어 엔드포인트 교체가 Orchestrator/Sub-agent 코드에 번지지 않는다 (ADR-003). |
| **vLLM 엔드포인트** | KT Cloud AI Nexus에 이미 배포된 vLLM 서버(`gemma-4-31B-it`). 우리가 기동·운영하지 않고 OpenAI 호환 API로 호출만 한다. 정부지원 GPU 자원 할당으로 무료이며 **2026년 말 만료** — 이후 대체 서빙 경로가 필요하다 (ADR-003). |
| **VectorDB (Chroma)** | 공개 코퍼스의 임베딩 인덱스 (`src/tools/retrieval.py`). 임베디드 모드라 별도 서버가 없다. Researcher만 접근하며, 검색 결과에는 출처 문서 ID(`doc_id`)와 위치(`locator`)가 항상 함께 반환된다. **인덱스에는 공개 자료만 들어간다** — 허용 출처가 코드에 화이트리스트로 박혀 있다 (ADR-004). |
| **Embedding Provider** | 서빙 엔드포인트가 `/v1/embeddings`를 제공하지 않아(실측 404) 임베딩만 로컬 sentence-transformers로 계산한다 (`src/providers/embeddings.py`). 벡터 DB 선택과 임베딩 모델 선택을 따로 되돌릴 수 있도록, Chroma의 기본 임베딩 함수를 쓰지 않고 우리가 계산한 벡터를 넣는다 (ADR-005). |
| **Tracer 파사드** | 각 노드의 입출력·지연·토큰 사용량을 기록한다 (`src/obs/`). 실행 1건 = trace 1개, 모델 호출 1건 = span 1개. 로컬 JSONL 기록은 항상 켜져 있고, Langfuse는 `.env`에 self-host 주소가 있을 때만 추가로 전송한다 — 감사 로그가 인프라 구성 여부에 좌우되지 않게 하기 위함이다 (ADR-007). |

## 설계 원칙

- **모델 고정, 하네스 분리.** 모델은 고정하고 오케스트레이션 구조·검증 루프·컨텍스트
  관리는 하네스 계층으로 분리한다. 성능 변화의 원인을 모델과 하네스로 구분해
  측정하기 위함이다 (ADR-002).
- **서빙은 교체 가능한 부품으로 둔다.** 현 엔드포인트는 2026년 말 만료가 예정돼 있다.
  모델 호출을 Provider Layer 뒤에 두어, 그 전환이 코드 변경이 아니라 설정 변경이 되게 한다.
- **근거 우선.** Writer는 Researcher가 반환한 근거 범위 밖의 주장을 생성하지 않는다.
  근거가 없으면 문장을 만들지 않고 누락으로 표시한다.
- **툴 스코프 최소화.** 각 에이전트는 자신에게 필요한 툴만 화이트리스트로 받는다
  (`.claude/rules/security.md`).

## 오케스트레이터 그래프

`src/orchestrator/graph.py`의 실제 배선. 노드 내부 로직은 session-03에서 채웠다.

```mermaid
stateDiagram-v2
    [*] --> outliner
    outliner --> researcher
    researcher --> verifier
    verifier --> researcher: uncovered 남음<br/>& revision < max
    verifier --> writer: 근거 충족<br/>또는 상한 도달
    writer --> [*]
```

`verifier → researcher`가 검증 루프다. 상한(`max_revisions`)에 도달하면 남은 항목을
"근거 없음"으로 표시한 채 `writer`로 넘어간다 — 무한 루프 대신 누락을 드러내는 쪽을 택한다.

## 인제스천 경로

질의 경로와 분리된 오프라인 경로다. 재현성의 기준은 인덱스가 아니라 **스냅샷 + 임베딩
모델 이름**이므로, 커밋 대상은 `data/corpus/`이고 `var/chroma/`는 언제든 재생성한다.

```mermaid
graph LR
    Pub[공개 자료<br/>arXiv API] --> Ing[ingest_corpus.py]
    Ing --> Snap[(data/corpus/<br/>스냅샷 · 커밋 대상)]
    Snap --> Build[build_index.py]
    Build --> Idx[(var/chroma/<br/>인덱스 · 커밋 안 함)]
```

출처 화이트리스트(`ALLOWED_SOURCES`)가 `ingest_corpus.py`와 `build_index.py` 양쪽 경로에서
강제된다. 허용 목록 밖의 출처는 빈 결과가 아니라 예외다 (ADR-004).

## 다음 버전에서 다룰 것

- 2026년 말 이후 대체 서빙 경로의 배포 토폴로지
- 체크포인터 백엔드와 중단·재개 경로 (ADR-002 Implementation에 남아 있음)
- Langfuse self-host 기동 시의 배포 위치 (ADR-007)
