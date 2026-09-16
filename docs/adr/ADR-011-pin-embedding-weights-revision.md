# ADR-011: 임베딩 가중치를 리비전 해시로 고정하고 sha256으로 검증한다

- **Status:** Proposed
- **Date:** 2026-09-16
- **Decision:** `intfloat/multilingual-e5-small`을 커밋 해시 `614241f622f53c4eeff9890bdc4f31cfecc418b3`에 고정하고, LFS 파일은 sha256까지 대조한다. 매니페스트는 `infra/model-pin.json` 한 곳에 둔다.
- **Scope:** multiagent-research-lab (임베딩 공급망 / 재현성)
- **Decision Source:** Human

---

## Context

### Problem

`SentenceTransformer("intfloat/multilingual-e5-small")`은 **런타임에 HuggingFace `main`의
현재 내용**을 받는다. 같은 이름으로 다른 가중치가 재배포되면 두 가지가 동시에 깨진다.

1. **재현성.** 이 프로젝트가 재려는 것은 하네스(오케스트레이션·검증 루프·컨텍스트 관리)의
   효과다 (ADR-002). 그래서 LLM은 고정했다. 그런데 **임베딩이 조용히 바뀌면 검색 결과가
   바뀌고, 검색이 바뀌면 파이프라인 출력이 바뀐다.** 모델을 고정한 의미가 사라진다.
2. **공급망 무결성.** session-05의 OWASP 정리에서 LLM03을 위험 상위 3개로 꼽았고, 그 근거가
   정확히 "임베딩 가중치·의존성 해시 미고정"이었다 (`docs/security/owasp-notes.md` §3.1).
   지금은 **바뀌었다는 사실을 탐지할 수단조차 없다.**

session-05 핸드오프는 이것을 "공급망이 고정돼 있지 않다"로 남기며 셋(의존성 해시, 임베딩
가중치, 엔드포인트 모델 지문) 중 **가장 싼 개선**을 지목했고, 세션 6 사전 확인 항목 §6이
컨테이너화와 함께 처리하라고 걸어 뒀다.

### Constraints

- 고정이 **기존 측정치를 바꾸면 안 된다.** session-03~05의 모든 수치(검색 재현율 71.4%,
  점수 간격 0.074 등)가 현재 인덱스를 기준으로 하므로, 고정 대상은 "지금 쓰고 있는 그것"이어야 한다.
- 인덱스 메타데이터 대조는 **모델 이름**으로 한다 (`src/tools/retrieval.py`). 고정 방식이
  이 이름을 바꾸면 기존 인덱스를 못 읽는다.
- `EMBEDDING_MODEL`로 모델을 바꿀 수 있어야 한다 (ADR-005). 고정이 그 교체를 막으면 안 된다.
- 컨테이너는 런타임에 네트워크를 타지 않아야 한다 (ADR-010).

## Decision

### Selected

- **Technology:** HuggingFace git 커밋 해시(`revision`) + 파일 sha256. 매니페스트는
  `infra/model-pin.json`.
- **Architecture:** **리비전과 sha256은 대체 관계가 아니라 역할이 다르다.** 리비전은
  "무엇을 받을지"를, sha256은 "받은 것이 맞는지"를 고정한다. 리비전만으로는 레지스트리가
  같은 해시에 다른 바이트를 주는 경우를 잡지 못하고, sha256만으로는 무엇을 받을지 지정할 수 없다.
- **Implementation:**
  - `EmbeddingSettings`에 `revision` / `local_path` 추가. `pinned`·`load_target` 프로퍼티로
    고정 여부와 실제 로딩 대상을 **호출부가 볼 수 있게** 드러낸다.
  - `load_embedding_settings()`의 리비전 결정 순서: `EMBEDDING_REVISION` 환경변수 >
    매니페스트(**단, `EMBEDDING_MODEL`이 매니페스트의 `repo_id`와 같을 때만**) > 빈 값.
  - `SentenceTransformerEmbeddings._load()`가 `revision=`을 넘긴다.
  - `scripts/fetch_model.py` — 고정 리비전으로 받고 sha256을 대조한다. **불일치는 경고가
    아니라 실패다.** 받은 디렉터리에 `PINNED_REVISION` 파일을 남겨 파일시스템만 보고도
    "이 경로가 어느 리비전인가"를 답할 수 있게 한다.
  - 컨테이너는 **빌드 시점**에 이 스크립트를 돌려 `/opt/models`에 굽고, 런타임은
    `EMBEDDING_LOCAL_PATH` + `HF_HUB_OFFLINE=1`로 읽는다 (ADR-010).
  - **`model_name`은 바꾸지 않는다.** 인덱스 메타데이터 대조에 쓰이는 이름은 경로가 아니라
    여전히 repo id다 — 그래야 컨테이너에서 만든 인덱스를 호스트에서 읽을 수 있다.

## Rationale

1. **매니페스트를 코드 상수가 아니라 파일로 둔 이유.** 해시는 사람이 읽고 대조하는 값이고,
   Dockerfile(빌드 시점)과 파이썬(런타임)이 **같은 출처**를 봐야 한다. 두 곳에 적으면 따로
   놀 수 있어서 테스트로 일치를 강제한다 (`test_pin_manifest_and_config_agree`).
2. **매니페스트의 리비전을 다른 모델에 적용하지 않는 것이 핵심이다.** `EMBEDDING_MODEL`을
   바꿨는데 e5-small의 커밋 해시를 그대로 들이밀면 "그런 리비전 없음"으로 실패하거나,
   더 나쁘게는 **우연히 존재하는 엉뚱한 해시의 가중치를 받는다.** 그래서 repo id가 일치할
   때만 물려준다.
3. **`pinned`를 프로퍼티로 노출한 이유.** 고정이 풀린 상태(예: 모델만 바꾸고 리비전을
   안 넣음)가 조용히 지나가면 안 된다. 진단 출력(`redacted()`)과 테스트가 이 값을 본다.
4. **sha256 대상이 LFS 파일 3개뿐인 것은 한계이자 의도다.** HuggingFace API는 LFS 파일에만
   sha256을 준다. 작은 JSON 설정 파일들은 리비전 고정으로만 보증된다 — 이 한계를
   매니페스트 주석과 아래 Risks에 명시했다.
5. **가장 싼 개선이라는 판단이 맞았다.** 코드 변경은 설정 dataclass 2개 필드와 로딩 인자
   하나이고, 기존 인덱스·측정치를 전혀 건드리지 않는다 (아래 Evidence).

## Evidence

- **Experiment:** 고정 대상이 "지금 쓰고 있는 그것"인지 확인 (2026-09-16)

  | 확인 | 값 |
  | --- | --- |
  | HuggingFace API `sha` (HEAD) | `614241f622f53c4eeff9890bdc4f31cfecc418b3` |
  | 로컬 HF 캐시 `refs/main` | `614241f622f53c4eeff9890bdc4f31cfecc418b3` — **동일** |
  | 로컬 스냅샷 디렉터리 | `snapshots/614241f622f5…` 1개뿐 |

  → session-03에서 `var/chroma/` 인덱스를 만든 리비전과 **같다.** 따라서 이 고정은
  기존 측정치를 바꾸지 않는다. 인덱스 재생성도 필요 없다.

- **Experiment:** sha256 대조 (로컬 캐시 파일 ↔ HuggingFace API, 2026-09-16)

  | 파일 | 크기 | sha256 (앞 16자리) | 일치 |
  | --- | ---: | --- | --- |
  | `model.safetensors` | 470,641,600 | `1a55775f53449dac` | ✅ |
  | `tokenizer.json` | 17,082,730 | `0b44a9d7b51c3c62` | ✅ |
  | `sentencepiece.bpe.model` | 5,069,051 | `cfc8146abe2a0488` | ✅ |

- **Benchmark:** 고정 후 동작 확인

  | 항목 | 결과 |
  | --- | --- |
  | 호스트 로딩 (고정 리비전) | 384차원, load+encode 25.7s (기존 19~30s 범위 내) |
  | 컨테이너 빌드 중 검증 | 9개 파일 다운로드 21s → **3개 파일 sha256 일치, 빌드 통과** |
  | 컨테이너 인덱싱 | 16건 / **10.5s** (호스트 19.9s — 로컬 경로라 HF 조회가 없다) |
  | 테스트 | 고정 관련 6건 추가, 전체 **114건 통과** |

## Alternatives

### 가중치를 레포에 커밋

- **Pros:** 리비전·해시를 따질 필요 없이 내용 자체가 버전 관리된다.
- **Cons:** `model.safetensors`가 470MB다. `.gitignore`가 이미 `*.safetensors`를 제외하고
  있고(대용량 산출물 정책), 레포 크기가 클론·CI 비용이 된다.
- **Rejected because:** 재현의 기준은 스냅샷 + 모델 식별자라는 기존 원칙(ADR-005)과 충돌하고,
  470MB를 git 이력에 넣는 비용이 고정이 주는 이득보다 크다.

### `requirements.txt`처럼 버전 태그만 고정

- **Pros:** 표기가 짧고 사람이 읽기 쉽다.
- **Cons:** HuggingFace 모델 저장소에는 의미 있는 버전 태그가 없다. `main` 브랜치가 사실상
  유일한 참조점이라 "태그 고정"이 곧 "브랜치 고정" = 고정 안 됨이다.
- **Rejected because:** 고정하려는 대상에 고정할 수 있는 태그가 존재하지 않는다.

### 아무것도 하지 않고 모델 이름만 유지 (현행 유지)

- **Pros:** 작업이 없다.
- **Cons:** 재현성과 공급망 무결성이 둘 다 열린 채로 남는다. 가중치가 바뀌어도 **탐지 수단이
  없다** — 검색 품질이 조용히 달라지고 그 원인을 하네스 변경으로 오인하게 된다.
- **Rejected because:** session-05가 이것을 위험 상위 3개(LLM03)로 지목했고, 셋 중 가장 싼
  개선으로 이미 식별해 뒀다.

## Consequences

### Positive

- 임베딩이 바뀌면 **에러로 드러난다** (sha256 불일치 → 빌드 실패). 조용한 품질 변화가 아니다.
- 기존 인덱스·측정치가 그대로 유효하다 — 고정 대상이 현재 사용 중인 리비전과 같음을 확인했다.
- 컨테이너가 런타임에 HuggingFace를 호출하지 않는다. 네트워크 없는 환경에서도 돈다.
- `pinned`가 설정 객체에 노출돼 "고정이 풀렸는지"를 코드가 판단할 수 있다.

### Negative

- 모델을 바꿀 때 할 일이 하나 늘었다 — 새 모델의 리비전을 `EMBEDDING_REVISION`에 넣어야
  한다. 안 넣으면 고정이 풀린 채로 돈다 (`.env.example`에 명시).
- 상류가 새 리비전을 내면 우리가 수동으로 올려야 한다. 자동 추적 수단이 없다.
- 매니페스트와 코드가 따로 놀 여지가 구조적으로 존재한다 (테스트로 막았지만 구조 자체는 남는다).

### Risks

- **sha256 보증이 LFS 파일 3개에 한정된다.** `config.json`·`modules.json`·`tokenizer_config.json`
  같은 작은 설정 파일은 HuggingFace API가 sha256을 주지 않아 **리비전 고정으로만 보증된다.**
  `1_Pooling/config.json`이 바뀌면 풀링 방식이 달라져 임베딩이 바뀌는데, 이 경로는 리비전이
  같은 한 안전하지만 해시로 이중 확인되지는 않는다.
- **이 결정은 임베딩 공급망만 닫는다.** owasp-notes §3.1이 꼽은 셋 중 나머지 둘 —
  **의존성 해시 미고정**과 **엔드포인트 모델 지문 확인 수단 없음** — 은 그대로 열려 있다.
  특히 후자는 엔드포인트를 우리가 운영하지 않아 구조적으로 어렵다.
- **고정은 무결성을 보장하지 이 가중치가 좋다는 뜻이 아니다.** ADR-005 Amendment가 측정한
  변별력 부족(간격 0.074)은 이 결정으로 전혀 변하지 않는다. 오히려 **모델 교체를 검토할 때
  교체 전/후를 같은 기준으로 비교할 수 있게 해 준다.**

## Implementation

- [x] `infra/model-pin.json` — repo_id / revision / allow_patterns / 파일 sha256
- [x] `EmbeddingSettings`에 `revision`·`local_path`·`pinned`·`load_target`·`redacted()`
- [x] `SentenceTransformerEmbeddings._load()`가 `revision=` 전달
- [x] `scripts/fetch_model.py` — 고정 다운로드 + sha256 검증 (`--verify-only` 지원)
- [x] Dockerfile builder 스테이지에서 빌드 시 검증
- [x] 테스트 6건 (고정 존재 / 매니페스트-코드 일치 / 다른 모델에 미적용 / env 우선 / 로컬 경로 / 진단 출력)
- [x] `.env.example`에 `EMBEDDING_REVISION`·`EMBEDDING_LOCAL_PATH`
- [ ] 의존성 해시 고정 (`requirements.txt` — `--require-hashes`) — 이 ADR 범위 밖, 여전히 열림
- [ ] 엔드포인트 모델 지문 확인 수단 — 구조적으로 어려움, 열림

## Reversibility

- **Reversible:** Yes
- **Rollback:** `infra/model-pin.json`의 `revision`을 비우면 고정이 풀린다 (코드 변경 불필요).
  파일을 통째로 지워도 `_load_pinned_revision()`이 빈 값을 돌려주고 `pinned`가 False가 된다 —
  임포트가 깨지지 않는다.
- **Migration Cost:** Low — 고정 리비전이 현재 사용 중인 것과 같아 인덱스 재생성이 없다.

## Review Trigger

- ADR-005 Amendment §6의 임베딩 모델 교체(`multilingual-e5-base` / `bge-m3`)를 실제로
  진행하는 경우 — 새 모델의 리비전과 sha256으로 매니페스트를 갱신한다.

## References

- **Related ADR:** ADR-002 (모델 고정 원칙 — 임베딩에도 적용되는 이유),
  ADR-005 (임베딩 모델 선정 · 변별력 부족 측정), ADR-010 (이미지에 굽는 방식)
- **Documentation:** `docs/security/owasp-notes.md` §3.1 (LLM03),
  `docs/handoff/session-05.md` "공급망이 고정돼 있지 않다",
  `infra/model-pin.json`, `scripts/fetch_model.py`

## AI/ML Details

- **Model:** `intfloat/multilingual-e5-small` @ `614241f622f53c4eeff9890bdc4f31cfecc418b3`
  (118M, 384차원). 받는 파일은 9개로 제한한다(`allow_patterns`) — `pytorch_model.bin`(470MB,
  safetensors와 중복)·onnx·openvino·`.eval_results`는 쓰지 않으므로 이미지에 넣지 않는다.
- **Evaluation:** 이 결정은 검색 품질을 바꾸지 않는다 — **바꾸지 않는 것이 목표다.**
  고정 리비전이 기존 인덱스를 만든 리비전과 동일함을 확인했으므로 ADR-005 Amendment의
  측정치(top-4 재현율 ko 71.4% / en 85.7%, 간격 0.074)가 그대로 유효하다.
- **Inference:** CPU. 컨테이너는 `/opt/models/multilingual-e5-small`에서 오프라인 로딩.

### Evaluation

| Metric | Before | After | Target |
| ------ | -----: | ----: | -----: |
| 가중치 리비전 고정 여부 | 없음 (`main` 추적) | **커밋 해시 고정** | 고정 |
| 가중치 무결성 검증 | 없음 | **sha256 3파일 (LFS 전체)** | 전체 파일 |
| 런타임 HuggingFace 호출 (컨테이너) | 매 실행 | **0회** (오프라인) | 0회 |
| 검색 재현율 top-4 (ko, n=7) | 71.4% | **71.4%** (불변) | 불변이어야 함 |
