# ADR-010: 로컬 스택을 컨테이너화하고 시크릿·평문 산출물을 이미지 밖에 둔다

- **Status:** Proposed
- **Date:** 2026-09-16
- **Decision:** 오케스트레이터를 멀티스테이지 Dockerfile로 이미지화하고, `VLLM_BASE`는 런타임 주입으로만, `var/`(traces·llm_cache·chroma)는 named volume으로만 다룬다. Langfuse self-host는 `--profile langfuse` 옵션으로 붙인다.
- **Scope:** multiagent-research-lab (런타임 패키징 / 로컬 스택)
- **Decision Source:** Human

---

## Context

### Problem

세션 7의 클라우드 배포 전에 실행 환경을 이미지로 고정해야 한다. 그런데 이 프로젝트는
컨테이너화가 **보안 결정**이 되는 조건을 두 개 갖고 있다 (session-05 핸드오프 §1·§2).

1. **`var/`에 평문이 쌓인다.** `var/traces/`에는 프롬프트를 포함한 감사 로그가(ADR-007),
   `var/llm_cache/`에는 LLM 응답 본문이(ADR-008), `var/chroma/`에는 인덱싱된 문서 본문이
   남는다. 이미지 레이어에 들어가면 **이미지를 받는 모두가 읽는다.**
2. **`VLLM_BASE` URL 자체가 자격증명이다.** 엔드포인트가 인증을 요구하지 않아 URL을 아는
   것이 곧 접근 권한이다 (`docs/governance.md` "시크릿 취급"). 이미지 레이어는 나중에
   지워도 **히스토리에 남는다** — 빌드 인자·`ENV`·복사된 `.env` 어디에도 들어가면 안 된다.

여기에 더해 ADR-007이 Langfuse를 옵션으로 남겨 뒀는데, v1.0 시점에는 자격증명이 없어
**local-jsonl fallback 경로만 검증됐다.** 실제 서버로의 전송은 한 번도 확인된 적이 없다.

### Constraints

- 서빙은 컨테이너화 대상이 아니다. 추론 엔드포인트는 KT Cloud AI Nexus의 관리형 vLLM이고
  우리가 운영하지 않는다 (ADR-003). **"전체 스택"에서 우리가 올릴 수 있는 것은
  오케스트레이터와 관측 백엔드뿐이다.**
- 임베딩은 로컬 CPU에서 돈다 (ADR-005). 가중치가 이미지 안에 있어야 런타임에 네트워크를
  타지 않는다.
- 컨테이너와 로컬의 런타임이 다르면 측정치를 비교할 수 없다. 이 프로젝트가 재려는 것이
  하네스의 효과이므로(ADR-002) 런타임도 고정 대상이다.
- 관측 데이터를 외부 SaaS로 내보내지 않는다 (`docs/problem-statement.md` §2) — Langfuse는
  Cloud가 아니라 self-host여야 한다.

## Decision

### Selected

- **Technology:** Docker 멀티스테이지 빌드 (`python:3.14-slim`) + Docker Compose v2 프로파일.
  Langfuse self-host v3 스택(web/worker + PostgreSQL + ClickHouse + Redis + MinIO)을
  별도 프로파일로 둔다.
- **Architecture:** `orchestrator`는 **데몬이 아니라 작업 컨테이너**다. `docker compose run --rm`으로
  한 번 돌고 끝난다. 상태는 전부 named volume(`traces` / `llm_cache` / `chroma`)에 있고
  이미지는 무상태다. Langfuse는 같은 브리지 네트워크에 붙어 컨테이너 이름으로 해석된다.
- **Implementation:**
  - **시크릿:** `.dockerignore`가 `.env`를 빌드 컨텍스트에서 제거한다. 주입은 compose의
    `env_file`(`required: false`)로 **런타임에만** 한다. Dockerfile의 `ENV`에는 비시크릿
    (경로·디바이스·오프라인 플래그)만 둔다.
  - **평문 산출물:** `.dockerignore`에 `var/`. 컨테이너에서는 `/app/var/{traces,llm_cache,chroma}`가
    볼륨 마운트 지점이며, non-root(`uid 10001`)가 쓸 수 있도록 빌드 시점에 소유권을 준다.
  - **가중치:** builder 스테이지에서 `scripts/fetch_model.py`로 **고정 리비전 + sha256 검증**
    후 `/opt/models`에 굽는다 (ADR-011). 런타임은 `EMBEDDING_LOCAL_PATH`로 그 경로를 읽고
    `HF_HUB_OFFLINE=1`로 온라인 폴백을 막는다.
  - **torch:** PyPI 기본 인덱스 대신 `download.pytorch.org/whl/cpu`에서 `torch==2.9.0+cpu`를
    먼저 설치한다. 기본 인덱스는 CUDA 런타임을 통째로 끌고 오는데 임베딩은 CPU로 돈다.
  - **COPY는 경로를 명시한다** (`src/ scripts/ tests/ data/ infra/model-pin.json`).
    `COPY . .`을 쓰지 않는 이유는 나중에 레포 루트에 생긴 것이 자동으로 이미지에 딸려
    들어가지 않게 하기 위함이다 — `.dockerignore` 하나에만 의존하지 않는다.

## Rationale

1. **`var/`를 볼륨으로 빼는 이유는 용량이 아니라 내용이다.** 이미지에 구우면 이미지 배포가
   곧 프롬프트·응답 배포가 된다. 볼륨은 이미지와 수명이 분리돼 있어 이미지를 지워도 남고,
   반대로 `docker compose down -v`로 이미지를 건드리지 않고 지울 수 있다. 보존·접근 기준은
   `docs/governance.md` "로컬 산출물 취급"이 정한다.
2. **`ENV`가 아니라 런타임 주입인 이유는 레이어의 영속성 때문이다.** `ENV`에 넣은 값은
   `docker image inspect`로 누구나 읽고, 빌드 인자는 `docker history`에 남는다. 레이어를
   덮어써도 히스토리는 남는다. 컨테이너 환경변수는 레이어가 아니므로 이미지에 흔적이 없다.
3. **작업 컨테이너로 둔 것은 이 파이프라인이 요청-응답 서비스가 아니기 때문이다.** 리서치
   1회 = 프로세스 1회 = trace 1개(ADR-007)라는 구조를 컨테이너 경계와 일치시켰다. 이 성질은
   배포 대상 선정에도 그대로 영향을 준다 (ADR-013).
4. **Python 3.14를 유지한 것은 비교 가능성 때문이다.** 컨테이너에서 3.12로 내리면 로컬 측정과
   컨테이너 측정을 직접 비교할 수 없다. `torch 2.9.0`의 cp314 리눅스 휠이 존재하는 것을
   확인했으므로 내릴 이유가 없었다.
5. **Langfuse를 기본 프로파일에서 뺀 이유는 무게다.** v3는 컨테이너 6개를 요구한다. 매번
   리서치 한 번 돌리자고 ClickHouse를 띄울 이유가 없다. 다만 **프로파일로 넣어 두면
   "구성이 존재하는가"와 "실제로 전송되는가"가 분리되지 않는다** — ADR-007의 미검증 경로가
   이번에 실측됐다 (아래 Evidence).

## Evidence

- **Experiment:** 시크릿·평문 누출 검사 (2026-09-16, 빌드된 `multiagent-research-lab/orchestrator:dev`)

  | 검사 | 방법 | 결과 |
  | --- | --- | --- |
  | 최종 이미지 `ENV`에 시크릿 | `docker image inspect --format '{{.Config.Env}}'` | 비시크릿 8개만 (`VLLM_*` 없음) |
  | 빌드 히스토리에 URL | `docker history --no-trunc` \| `grep -F`(.env에서 읽은 값) | **미검출** |
  | 이미지 파일시스템에 URL | 컨테이너 내부 `grep -rlF /app /opt` | **미검출** |
  | `.env` 파일 존재 | 컨테이너 내부 `find / -name .env` | **없음** |
  | `/app/var/*` 내용 | 컨테이너 내부 `ls -laR` | 비어 있음 (마운트 지점만) |

  검사 명령에 URL 조각을 직접 적지 않고 `.env`의 실제 값을 읽어 대조했다
  (`docs/governance.md`의 커밋 전 확인 절차와 같은 방식).

- **Benchmark:** 컨테이너 동작 확인 (2026-09-16)

  | 항목 | 결과 |
  | --- | --- |
  | 이미지 크기 | 3.46GB (torch CPU + 가중치 470MB 포함) |
  | 테스트 | `python -m pytest tests/ -q` → **113 passed** (네트워크 없이) |
  | 헬스체크 | `scripts/healthcheck.py` → OK, latency 0.917s, 18+2 tokens |
  | 인덱싱 | 16건 / **10.5s** (호스트 19.9s — 가중치가 로컬이라 HF 조회가 없다) |
  | e2e (GS-001 질의) | 13콜 / 34.86s / 출처 표기 유지 (`[arXiv:2602.03128v1 / abstract]`) |

- **Experiment:** Langfuse self-host 실제 전송 확인 — **ADR-007의 미검증 경로 해소** (2026-09-16)

  | 항목 | 결과 |
  | --- | --- |
  | 서버 | `langfuse/langfuse:3` → `/api/public/health` → `{"status":"OK","version":"3.225.7"}` |
  | 수신된 trace | `research_run` 1건, observation **19개** |
  | observation 내역 | GENERATION 13 (outliner 1 / researcher 5 / verifier 6 / writer 1) + SPAN 6 |
  | 토큰·지연 보존 | `writer_call` → `model=gemma-4-31B-it`, `usage={input:1131, output:247, total:1378}`, `latency=4.462` |
  | 터미널 요약과 대조 | 13콜 / 12875+881 토큰 — **일치** |

## Alternatives

### `var/`를 이미지에 COPY하고 컨테이너 내부에 두기

- **Pros:** 볼륨 설정이 필요 없어 compose가 단순해진다.
- **Cons:** 프롬프트·LLM 응답 본문이 이미지 레이어에 들어간다. 이미지를 받는 모두가 읽고,
  레이어를 지워도 히스토리에 남는다. 컨테이너를 지우면 감사 로그도 함께 사라진다.
- **Rejected because:** 감사 로그 요구가 규제성이고(`docs/problem-statement.md` §2) 평문
  노출이 곧 사고가 되는 데이터다. 편의와 바꿀 수 있는 항목이 아니다.

### `VLLM_BASE`를 빌드 인자(`ARG`)나 `ENV`로 이미지에 굽기

- **Pros:** 컨테이너를 돌릴 때 설정을 신경 쓰지 않아도 된다.
- **Cons:** URL이 곧 접근 권한인데 `docker history`·`docker image inspect`로 누구나 읽는다.
  레이어를 덮어써도 히스토리에는 남는다.
- **Rejected because:** `docs/governance.md` "시크릿 취급"이 `.env` 외 평문 보관을 금지한다.
  이미지 레이어는 되돌릴 수 없는 평문 보관이다.

### 컨테이너 베이스를 Python 3.12/3.13으로 내리기

- **Pros:** langchain-core의 Pydantic V1 경고(Python 3.14 비호환)를 우회할 수 있다.
- **Cons:** 컨테이너와 로컬의 런타임이 갈라져 측정치를 직접 비교할 수 없게 된다.
- **Rejected because:** `torch 2.9.0`의 cp314 리눅스 휠이 존재해 3.14로 빌드가 통과했다
  (실측). 경고는 현재 무해하므로 비교 가능성을 깨면서까지 내릴 이유가 없다.
- **Recheck if:** 노드에서 pydantic 모델을 쓰기 시작해 경고가 실패로 바뀌는 경우
  (`CLAUDE.md`에 열려 있는 항목).

### Langfuse를 기본 프로파일에 포함

- **Pros:** 별도 플래그 없이 항상 계측 서버가 함께 뜬다.
- **Cons:** v3는 PostgreSQL·ClickHouse·Redis·MinIO를 요구해 컨테이너 6개가 상시로 뜬다.
  리서치 1회를 돌리는 비용이 과도해진다.
- **Rejected because:** 로컬 JSONL이 항상 켜져 있어 감사 로그 요구는 이미 충족된다
  (ADR-007). Langfuse는 그 위에 얹는 옵션이다.

## Consequences

### Positive

- 시크릿과 평문 산출물이 이미지 밖에 있다는 것이 **주장이 아니라 검사 결과로** 남았다
  (위 Evidence). 같은 검사를 재실행할 수 있다.
- ADR-007이 v1.0에 남긴 "Langfuse 경로 미검증"이 해소됐다. fallback이 아니라 실제 서버로
  13개 generation이 토큰·지연과 함께 도착하는 것을 확인했다.
- 실행 환경이 고정돼 세션 7의 배포가 "이미지를 어디서 돌릴 것인가" 문제로 좁혀진다.
- `python -m pytest tests/`가 네트워크 없이 컨테이너 안에서 통과해 이미지 스모크 테스트로
  쓸 수 있다 (113건).

### Negative

- **이미지가 3.46GB다.** 대부분 torch(CPU)와 가중치 470MB다. ECR 저장 비용과 태스크 콜드
  스타트에 영향을 준다 (ADR-013에서 다룸).
- Langfuse 프로파일은 컨테이너 6개를 띄운다 — 개발 머신에서 가볍지 않다.
- 작업 컨테이너 모델이라 `docker compose up`만으로는 아무 일도 일어나지 않는다.
  `run --rm`을 알아야 한다. README에 적었지만 학습 비용이 0은 아니다.

### Risks

- **compose의 Langfuse 자격증명이 전부 로컬 개발용 기본값이다** (`pk-lf-local-dev`,
  `postgres`, `ENCRYPTION_KEY`가 0으로 채워진 값 등). 파일에 평문으로 적혀 있고 **의도된
  것이다** — 로컬 전용이기 때문이다. **이 값이 배포로 넘어가면 사고다.** ADR-013의 배포
  범위에 Langfuse를 넣을 경우 자격증명 발급이 선행 조건이다.
- Langfuse SDK가 실행 중 `Context error: No active span in current context`를 출력한다.
  데이터는 정상 도착했지만(19 observation) 스팬 중첩이 SDK v4의 기대와 완전히 맞지는 않는다.
  현재는 표시 문제로 보이나 확인되지 않았다.
- `.dockerignore`는 **빠뜨리면 조용히 실패한다.** 새 산출물 경로가 생겼을 때 여기 추가하는
  것을 잊으면 아무 에러 없이 이미지에 들어간다. COPY 경로 명시가 2차 방어지만 완전하지 않다.

## Implementation

- [x] `.dockerignore` — `.env` / `var/` / `vendor/` 제외, 왜 빼는지 주석
- [x] 멀티스테이지 `Dockerfile` — CPU torch, 가중치 굽기, non-root, HEALTHCHECK
- [x] `docker-compose.yml` — 볼륨 3개, 런타임 env 주입, `langfuse` 프로파일
- [x] 시크릿 누출 검사 (ENV / history / 파일시스템 / `.env` 존재)
- [x] 컨테이너 안 테스트 113건 통과
- [x] 컨테이너 e2e 리서치 성공 (출처 표기 유지)
- [x] Langfuse self-host 실제 전송 확인
- [ ] 방어 적용 후 비용·지연 재측정 (`scripts/bench_golden.py`) — session-05에서 이월된 항목
- [ ] 이미지 크기 축소 검토 (torch 슬림화 / 가중치 분리)

## Reversibility

- **Reversible:** Yes
- **Rollback:** `Dockerfile` / `docker-compose.yml` / `.dockerignore` 세 파일을 지우면
  session-05 상태로 돌아간다. 애플리케이션 코드는 컨테이너를 전제하지 않는다 — 설정은 전부
  환경변수로 들어가고(ADR-003), 로컬 실행 경로가 그대로 남아 있다.
- **Migration Cost:** Low

## Review Trigger

- 노드에서 pydantic 모델을 쓰기 시작해 Python 3.14의 Pydantic V1 경고가 실패로 바뀌는 경우 —
  컨테이너 베이스를 3.12/3.13으로 내릴지 재검토한다.

## References

- **Related ADR:** ADR-003 (서빙은 외부 관리형 — 컨테이너화 대상이 아닌 이유),
  ADR-005 (chroma 볼륨), ADR-007 (traces 볼륨 · Langfuse 미검증 경로),
  ADR-008 (llm_cache 볼륨), ADR-009 (Review Trigger가 이 시점의 `var/` 배치·URL 노출 확인을
  지목), ADR-011 (가중치 고정), ADR-013 (이 이미지를 어디서 돌릴 것인가)
- **Documentation:** `docs/governance.md` "시크릿 취급" / "로컬 산출물 취급",
  `docs/security/owasp-notes.md` §3.1, `docs/handoff/session-05.md` "다음 세션 전에 알아야 할 것"
