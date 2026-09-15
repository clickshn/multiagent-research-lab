# multiagent-research-lab

외부 LLM 벤더에 의존하지 않는 멀티에이전트 리서치 시스템. 오케스트레이터가
Outliner / Researcher / Verifier / Writer 에이전트를 조율해 리서치 결과를
**출처와 함께** 생성하고, 근거를 찾지 못한 항목은 문장을 만들지 않고 "근거 없음"으로 남긴다.

> 상태: end-to-end 동작 (session-03). 네 노드의 내부 로직·검색 계층·계측이 붙었고
> 실제 엔드포인트로 e2e 실행이 성공한다 (`docs/eval/run-log-session-03.md`).
> 품질 튜닝과 평가 파이프라인은 아직이다.

> **데이터 범위 주의.** 이 레포는 "사내 리서치 업무"를 가정한 **아키텍처 실습**이며,
> 실제로 투입하는 데이터는 **공개 자료로 한정한다** (arXiv 논문 초록 등).
> 실제 사내 기밀 문서는 넣지 않는다 — 이유는 ADR-004.

## 구조

```
src/providers/      모델 호출 단일 통로 (LiteLLM) + 로컬 임베딩 + 설정 로딩
src/orchestrator/   LangGraph State 그래프 — 네 노드 + 검증 루프 + 프롬프트
src/tools/          에이전트 툴 — 코퍼스 반입 정책, Chroma 검색
src/obs/            계측 파사드 — 로컬 JSONL(기본) + Langfuse(옵션)
scripts/            헬스체크 · 임베딩 지원 확인 · 코퍼스 수집 · 인덱싱 · 실행
data/corpus/        공개 코퍼스 스냅샷 (커밋 대상)
var/                인덱스 · 계측 로그 (커밋 안 함, 재생성 가능)
docs/               문제 정의 · 아키텍처 · 거버넌스 · ADR · 평가 · 세션 핸드오프
```

## 시작하기

```bash
cp .env.example .env      # VLLM_BASE / VLLM_MODEL 값을 채운다
pip install -r requirements-dev.txt
python scripts/healthcheck.py     # 엔드포인트 + 프로바이더 계층 동작 확인
python -m pytest tests/ -q
```

리서치를 실제로 한 번 돌리려면 코퍼스와 인덱스가 필요하다:

```bash
python scripts/ingest_corpus.py   # 공개 arXiv 초록 수집 → data/corpus/ (재개 가능)
python scripts/build_index.py     # → var/chroma/ (임베딩 모델 첫 로딩에 수십 초)
python scripts/run_research.py "리서치 질의"
```

실행이 끝나면 `var/traces/<run_id>.jsonl`에 단계별 입력·출력·토큰·지연이 남는다.

모든 명령은 **레포 루트를 cwd로** 실행한다. `src`를 패키지 루트로 임포트하므로
(`from src.providers import ...`) 다른 위치에서 실행하면 깨진다.

## 보안 / 운영

### 엔드포인트 URL은 시크릿이다

현재 추론 경로는 KT Cloud AI Nexus에 배포된 vLLM 엔드포인트다 (ADR-003).
**이 엔드포인트는 인증을 요구하지 않으며, VPN이나 사내망 제한 없이 어떤
네트워크에서든 접근 가능하다.** 즉 **URL을 아는 것이 곧 접근 권한**이다 —
API 키가 없을 뿐, 사실상 URL이 자격증명 역할을 한다.

따라서 URL은 API 키와 동일한 등급으로 취급한다.

| 원칙 | 구체적으로 |
| --- | --- |
| **`.env`에만 둔다** | 코드·문서·커밋 메시지·이슈·테스트 픽스처 어디에도 평문으로 쓰지 않는다. `.env`는 `.gitignore` 대상이며, 공유는 `.env.example`의 플레이스홀더로만 한다. |
| **출력할 때 마스킹한다** | 로그·에러 메시지·스크린샷·터미널 출력을 공유하기 전에 가린다. 진단 출력에는 `ProviderSettings.redacted()`를 쓴다 — 호스트까지만 남기고 경로를 버린다. |
| **유출 시** | URL 자체가 접근 권한이므로 회수할 방법이 없다. 엔드포인트 소유 조직에 재발급을 요청하는 것 외에 우리 쪽 대응 수단은 없다. 그래서 예방이 유일한 통제다. |

커밋 전 확인 — 출력이 비어 있어야 한다:

```bash
git diff --cached | grep -F "$(sed -n 's/^VLLM_BASE=//p' .env)"
```

URL 조각을 검사 명령에 직접 적지 않는다. 그 자체가 평문 노출이기 때문에,
`.env`의 실제 값을 읽어서 대조한다.

**별도 접근 통제(IP 화이트리스트, API 키 발급 등)를 추가할 수 있는지는 이 프로젝트
범위 밖이다 — 우리는 엔드포인트 소유자가 아니라 이용자다.** 통제를 걸 수 있는 지점은
"URL을 어떻게 다루는가"뿐이며, 위 원칙이 그 전부다. 사내 보안 정책상 이 상태가
허용되는지는 별도 확인이 필요하다 (session-02 핸드오프의 열린 이슈).

### 데이터 경계

질의와 문서 내용은 **자체 호스팅된 오픈소스 모델로만** 흘러가며, OpenAI·Anthropic
등 타사 LLM 벤더로 나가지 않는다. 이 시스템의 핵심 제약은 물리적 망분리가 아니라
**외부 LLM 벤더 비의존**이다 — 판단 기준은 네트워크 도달 가능성이 아니라 데이터가
향하는 곳이다. 상세는 `docs/problem-statement.md` §2.

임베딩도 같은 원칙을 따른다. 서빙 엔드포인트가 `/v1/embeddings`를 제공하지 않아
(실측 404) 임베딩은 로컬 sentence-transformers로 계산한다 — 모델 가중치가 로컬에 있고
문서 내용이 어디로도 나가지 않는다 (ADR-005).

### 코퍼스 반입 범위

**인덱스에는 공개 자료만 들어간다** (ADR-004). 허용 출처는 `src/tools/corpus.py`의
`ALLOWED_SOURCES`에 코드로 박혀 있고, 목록 밖의 출처는 빈 결과가 아니라 예외다 —
정책을 문서에만 두면 다음 세션에서 무심코 깨지기 때문이다.

실제 사내 문서를 투입하는 것은 현 엔드포인트가 무인증 공개 접근인 상태에서 회사 보안
정책 검토가 별도로 필요한 문제이고, 이 포트폴리오 프로젝트의 범위를 벗어난다.

### 감사 로그

모든 에이전트·툴 호출은 사후 재현 가능해야 한다. 실행 1건 = trace 1개,
모델 호출 1건 = span 1개로 `var/traces/<run_id>.jsonl`에 남는다 (`src/obs/`, ADR-007).
`ResearchState.trace`에도 노드별 모델·토큰·지연이 누적돼, 계측 백엔드 없이 그래프
실행 결과만으로도 추적할 수 있다.

**로컬 JSONL 기록은 항상 켜져 있다** — 감사 로그가 인프라 구성 여부에 좌우되면 안 되기
때문이다. Langfuse는 `.env`에 self-host 주소와 키가 모두 있을 때만 추가로 전송한다.
관측 도구도 외부 SaaS가 아니라 우리가 통제하는 인프라에 self-host 한다.

### 서빙 자원 만료

현 엔드포인트는 정부지원 GPU 자원 할당으로 무료 사용 중이며 **2026년 말 만료**된다.
이후 대체 서빙 경로로 전환해야 한다. 모델 호출이 프로바이더 계층 뒤에 있어 전환은
`.env` 변경으로 끝나지만, **대체 경로 확보 자체는 아직 미해결 과제다** (ADR-003 Risks).

## 문서

| 문서 | 내용 |
| --- | --- |
| `docs/problem-statement.md` | 문제·제약·목표·성공 기준 |
| `docs/architecture.md` | 개념도, 구성 요소, 그래프 배선 |
| `docs/governance.md` | 승인 게이트·ADR·시크릿 취급 규칙 (에이전트 세션에 자동 로드) |
| `docs/adr/` | 아키텍처 결정 기록 |
| `docs/eval/` | 골든셋 초안 · e2e 실행 로그 |
| `docs/handoff/` | 세션별 핸드오프 |
