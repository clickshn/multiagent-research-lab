# multiagent-research-lab

외부 LLM 벤더에 의존하지 않는 멀티에이전트 리서치 시스템. 오케스트레이터가
Outliner / Researcher / Writer 에이전트를 조율해 사내 문서 기반 리서치 결과를
**출처와 함께** 생성한다.

> 상태: 골격 단계 (session-02). 프로바이더 계층과 LangGraph 그래프 배선은 동작하며,
> 노드 내부 로직은 아직 비어 있다.

## 구조

```
src/providers/      모델 호출 단일 통로 (LiteLLM → OpenAI 호환 엔드포인트)
src/orchestrator/   LangGraph State 그래프 — 노드 골격 + 검증 루프
src/tools/          에이전트 툴 (미구현)
scripts/            운영 스크립트 (헬스체크)
docs/               문제 정의 · 아키텍처 · 거버넌스 · ADR · 세션 핸드오프
```

## 시작하기

```bash
cp .env.example .env      # VLLM_BASE / VLLM_MODEL 값을 채운다
pip install -r requirements-dev.txt
python scripts/healthcheck.py     # 엔드포인트 + 프로바이더 계층 동작 확인
python -m pytest tests/ -q
```

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

사내 문서 내용과 질의는 **자체 호스팅된 오픈소스 모델로만** 흘러가며, OpenAI·Anthropic
등 타사 LLM 벤더로 나가지 않는다. 이 시스템의 핵심 제약은 물리적 망분리가 아니라
**외부 LLM 벤더 비의존**이다 — 판단 기준은 네트워크 도달 가능성이 아니라 데이터가
향하는 곳이다. 상세는 `docs/problem-statement.md` §2.

### 감사 로그

모든 에이전트·툴 호출은 사후 재현 가능해야 한다. `ResearchState.trace`에
노드별 모델·토큰·지연이 누적되며, Langfuse 연동 시
`LiteLLMProvider(observer=...)` 훅에 붙인다. 관측 도구도 외부 SaaS가 아니라
우리가 통제하는 인프라에 self-host 한다.

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
| `docs/handoff/` | 세션별 핸드오프 |
