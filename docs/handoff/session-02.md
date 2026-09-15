# Session 02 핸드오프 — 서빙 확정 + 프로바이더 계층 + 오케스트레이터 골격

- **날짜:** 2026-09-15
- **범위:** 서빙 경로 확정(원격 엔드포인트 재사용), 제약 재정의, ADR-002 갱신 / ADR-003 신규,
  LiteLLM 프로바이더 추상화, LangGraph 그래프 골격
- **코드:** 이번 세션부터 있음. `src/providers/`, `src/orchestrator/`, `scripts/`, `tests/`

## 이번 세션의 핵심 변화

**서빙을 우리가 만들지 않기로 했다.** 로컬에 vLLM을 새로 띄우는 대신, KT Cloud AI Nexus에
이미 배포된 vLLM 엔드포인트(`gemma-4-31B-it`)를 OpenAI 호환 API로 호출만 한다 (ADR-003).
그 결과 session-01에서 "가장 시급한 확인 사항"이던 **GPU 전용/공유 여부 이슈가 사라졌다** —
우리가 프로세스를 기동하지 않으므로 GPU 메모리 선점 문제가 우리 쪽에서 발생하지 않는다.

**제약의 문구도 실태에 맞게 고쳤다.** 이 엔드포인트는 외부 인터넷에서 도달 가능한 HTTPS
프록시라 "인터넷 연결 없음"은 사실이 아니었다. 실제 핵심 제약은 **외부 LLM 벤더 비의존** —
자체 호스팅 오픈소스 모델만 쓰고 데이터가 타사 벤더로 나가지 않는 것 — 이며, 완전한 물리적
망분리가 아니다. 판단 기준은 네트워크 도달 가능성이 아니라 **데이터가 향하는 곳**이다.

## 완료된 것

### A. 설정 / 서빙

- `.env` — `VLLM_BASE`, `VLLM_MODEL` 주입. `git check-ignore`로 무시 처리 재확인 완료.
- `.env.example` — 키 이름 + 플레이스홀더만. 실제 URL은 커밋되지 않았다
  (커밋 대상 전체를 grep으로 검사해 확인).
- **헬스체크 실측** (2026-09-15) — CLAUDE.md 승인 규칙 갱신 후 승인 없이 수행:

  | 요청 | 결과 | 지연 |
  | --- | --- | --- |
  | `GET /v1/models` | HTTP 200, `gemma-4-31B-it`, `max_model_len` 262144 | 7.80s (콜드, TLS 포함) |
  | `POST /v1/chat/completions` (curl) | HTTP 200, `finish_reason: stop`, 18+2=20 토큰 | 0.152s (웜) |
  | 프로바이더 계층 경유 (`scripts/healthcheck.py`) | 정상, `text='OK'` | 1.566s |

  인증 헤더 없이 200 — **현재 엔드포인트는 API 키를 요구하지 않는다.**

### B. 문서 / 결정

- `docs/problem-statement.md` v0.2 — 제약 섹션 재작성 (벤더 비의존, 2026년 말 자원 만료).
- `docs/adr/ADR-002` — Amendment 추가. "로컬 기동 시 GPU 메모리 선점" Review Trigger /
  Recheck if / Risks를 **해소됨**으로 기록. 선택(LangGraph + vLLM) 자체는 유지.
- `docs/adr/ADR-003` (신규) — 엔드포인트 재사용 결정. Evidence에 실측 헬스체크,
  Risks에 2026년 말 만료, Review Trigger에 만료 전 대체 엔드포인트 검토.
- `docs/architecture.md` v1.1 — Provider Layer + 원격 엔드포인트 박스 추가,
  운영 경계 명시, 그래프 배선 stateDiagram 추가.
- `CLAUDE.md` — 제약 문구 정정 + 승인 규칙 갱신 (아래 "주의" 참조).

### C. 코드

- `src/providers/` — LiteLLM 기반 추상화. `config.py`가 `.env`를 읽는 유일한 지점이고,
  `llm.py`의 `LLMProvider` 프로토콜이 노드가 보는 유일한 인터페이스다. 응답에 토큰·지연을
  실어 노드가 별도 계측 코드 없이 감사 로그 요구를 충족한다. Langfuse용 `observer` 훅 예약.
- `src/orchestrator/` — State 스키마(TypedDict + Annotated reducer), 노드 시그니처,
  그래프 배선. **내부 로직은 비어 있다.** 배선:
  `START → outliner → researcher → verifier ⇄ researcher → writer → END`
- `scripts/healthcheck.py` — 추상화 계층을 태우는 재현 가능한 헬스체크.
- `requirements.txt` / `requirements-dev.txt` — 버전 고정 (langgraph 1.1.4,
  litellm 1.101.0, python-dotenv 1.1.1, pytest 9.0.3).
- `tests/test_orchestrator_graph.py` — 가짜 프로바이더로 구조 검증. **5 passed.**

## 다음 세션에서 할 일 (Sub-agent 구현 + 계측)

1. Outliner / Researcher / Writer 노드 내부 로직 구현 (`# TODO(session-03)` 표시된 곳)
2. VectorDB 선택 후 Researcher의 검색 툴 연결 — 툴 스코프 화이트리스트 포함
3. Langfuse self-host 연동 — `LiteLLMProvider(observer=...)` 훅에 붙인다
4. 프로바이더 계층 단위 테스트 (설정 로딩 / 엔드포인트 교체 시 호출부 불변 검증)
5. 체크포인터 백엔드 선택 및 중단·재개 테스트

## 열린 이슈 / 결정 대기

### 막힌 것 — 결정이 있어야 진행되는 항목

- **VectorDB 선택.** Researcher 노드 구현이 여기 묶여 있다. 검색 결과에 문서 ID와 위치가
  항상 함께 와야 한다는 요구(출처 제시)가 후보 선정 기준의 핵심이다. **session-03 착수 전 필요.**
- **골든셋.** 규모(N), 작성 주체, 정답·근거 판정 기준 모두 미정. 성공 기준 전체와
  ADR-003의 Evaluation 표가 여기 묶여 있다. 계측을 붙여도 비교 대상이 없다.

### 새로 드러난 것

- **의존성 반입 전제를 다시 봐야 한다.** ADR-002는 "모든 의존성이 폐쇄망 반입 절차 대상"을
  전제하지만, 이번 세션에서 PyPI로 `litellm`을 그냥 설치했다. 엔드포인트가 인터넷 경유라면
  패키지 레지스트리도 막혀 있지 않을 가능성이 높다. **반입 절차가 실제로 적용되는 범위가
  어디까지인지 확인이 필요하다** — 불필요한 제약을 스스로 지고 있을 수 있다.
- **엔드포인트에 인증이 없다.** → **사실 확인됨 (세션 2 후속):** VPN·사내망 제한 없이
  어떤 네트워크에서든 접근 가능하다. **URL이 곧 접근 권한이다.** 우리는 엔드포인트
  소유자가 아니라 IP 화이트리스트·API 키를 추가할 수 없으므로, 통제 수단은 URL을
  시크릿으로 취급하는 것뿐이다. 취급 원칙은 `README.md` "보안 / 운영"과
  `docs/governance.md`에 명시했다.
  **남은 확인 사항:** 사내 보안 정책상 이 상태(무인증 공개 엔드포인트로 사내 문서 질의)가
  허용되는지는 여전히 미확인이다. 허용되지 않으면 서빙 경로 자체를 다시 봐야 한다.
- **Python 3.14 + langchain-core 경고.** 테스트 실행 시
  `Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater` 경고가 뜬다.
  현재는 동작에 문제없지만, 노드 구현에서 pydantic 모델을 쓰기 시작하면 터질 수 있다.
  Python 버전을 3.12/3.13으로 내릴지 판단 필요.
- **2026년 말 대체 서빙 경로.** 프로바이더 추상화로 *전환 비용*은 낮췄지만,
  **대체 경로 확보 자체는 해결되지 않았다.** 자체 GPU / 다른 할당 / 유료 전환 중
  무엇인지 결정하고 리드타임을 역산해야 한다. 만료가 다가올수록 선택지가 줄어든다.

### 계속 열려 있는 것

- ~~**CLAUDE.md가 여전히 `.gitignore`에 있다.**~~ → **해소됨 (세션 2 후속 작업).**
  거버넌스 규칙을 `docs/governance.md`(커밋 대상)로 분리하고, `CLAUDE.md`는 맨 위
  `@docs/governance.md` import + 개인 메모만 남겼다. 규칙 변경이 이제 커밋 이력에 남는다.
- 대상 문서 저장소의 규모·형식·업데이트 주기
- 감사 로그 보존 기간·접근 권한 요구사항

## 세션 2 후속 작업 (같은 날)

1. **엔드포인트 무인증 대응** — `README.md` 신규 작성, "보안 / 운영" 섹션에 URL =
   접근 권한이라는 사실과 시크릿 취급 원칙(.env 외 평문 금지, 마스킹, 커밋 전 검사)을
   명시. 별도 접근 통제 추가가 프로젝트 범위 밖임도 기록.
2. **거버넌스 분리** — `docs/governance.md` 신규(커밋 대상), `CLAUDE.md`는
   `@docs/governance.md` import + 개인 메모. 시크릿 취급·모델 고정·엔드포인트
   하드코딩 금지도 함께 옮겼다 — 팀이 따라야 할 규칙이 gitignore된 파일에만 있으면
   분리의 취지에 어긋나기 때문이다.
3. **import 동작 검증** — 파일 도구를 전부 끈 세션에서 `governance.md`에만 있는 내용을
   답하는지로 확인했다. 승인 규칙·시크릿 검사 명령·출처 파일 경로 모두 정확히 재현됐다.

## 주의

- `CLAUDE.md` 승인 규칙이 바뀌었다: **이미 할당된 무료 자원(현 KT Cloud vLLM 엔드포인트)
  호출은 승인 없이 진행**, **새 유료 리소스 생성(세션 7 클라우드 배포 등)은 기존대로 사전 승인**.
  IaC 경로의 `.claude/deploy-approved` 확인 규칙은 그대로다.
- 코드 실행은 레포 루트를 cwd로 한다 (`python scripts/healthcheck.py`, `python -m pytest`).
  `src`를 패키지 루트로 임포트하므로(`from src.providers import ...`) 다른 위치에서 실행하면 깨진다.
