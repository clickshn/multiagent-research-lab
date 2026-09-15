# Session 01 핸드오프 — 레포 하네스 세팅 + 설계 초안

- **날짜:** 2026-09-15
- **범위:** 레포 초기화, Claude Code 하네스 구성, 문제 정의·아키텍처 v1, 기반 스택 결정(ADR-002)
- **코드:** 아직 없음. 이번 세션은 문서·하네스만.

## 완료된 것

### A. 레포 기본 & 하네스

- `.gitignore` — Python 표준 + venv/.env/캐시, 로컬 모델 가중치(`models/`, `*.gguf`, `*.safetensors`), 그리고 `CLAUDE.md`, `.claude/deploy-approved`, `.claude/settings.local.json`
- `CLAUDE.md` — 레포 정의, 폐쇄망 제약, 승인 게이트 규칙, ADR 자동 기록 규칙, 모델 고정 원칙
- `docs/adr/` — adr-recorder 스킬 동작 확인 완료 (ADR-001이 그 산출물)
- `.claude/rules/` — `orchestrator.md`(src/orchestrator), `security.md`(tests/security, src/tools), `iac.md`(infra, Dockerfile, docker-compose.yml). paths-scoped 뼈대만, 내용은 이후 세션에서 채움
- `.claude/hooks/load-handoff.sh` — 실행권한 부여, 최신 핸드오프 자동 출력. 파일 있을 때/없을 때 모두 스모크 테스트 통과
- `.claude/settings.json` — SessionStart 훅 등록
- 빈 디렉터리 골격: `src/orchestrator/`, `src/tools/`, `tests/security/`, `infra/`, `docs/handoff/`

### B. 설계

- `docs/problem-statement.md` — 문제/제약/목표/성공 기준. 성공 기준 지표는 표로 정리했으나 목표값은 대부분 TBD (골든셋 구축 후 확정)
- `docs/architecture.md` — mermaid v1 개념도 (User → Orchestrator → Agents, Researcher → VectorDB, Orchestrator ⇢ Langfuse) + 구성요소 표 + 설계 원칙
- `docs/adr/ADR-001` — ADR 기록 위치·명명 규칙
- `docs/adr/ADR-002` — LangGraph(오케스트레이션) + vLLM(로컬 서빙). Status: Proposed

### C. 마무리

- 본 핸드오프 문서 + 논리 단위 커밋 (push 안 함)

## 다음 세션에서 할 일 (서빙 + 오케스트레이터 골격)

1. vLLM OpenAI 호환 서버 기동 스크립트 + 엔드포인트 설정 주입 경로
2. LangGraph State 스키마 (TypedDict + Annotated reducer) 정의
3. Outliner / Researcher / Writer 노드 골격 + 검증 루프 조건부 엣지
4. 폐쇄망 반입 대상 의존성 목록 작성 (버전 고정) — `requirements.txt` 추가 시 ADR 트리거 가능성
5. 개발용 Ollama 백엔드로 동일 코드가 도는지 확인 (헤지 경로 검증)

## 열린 이슈 / 결정 대기

- **GPU 전용 여부** — vLLM은 기동 시 GPU 메모리를 선점한다. GPU가 다른 워크로드와 공유로
  확정되면 ADR-002의 Review Trigger가 발동해 서빙 선택을 재검토해야 한다. **가장 시급한 확인 사항.**
- **구체 모델 선택** — ADR-002는 서빙 *계층*만 정했다. 어떤 모델을 고정할지는 별도 ADR.
- **VectorDB 선택** — 아키텍처 도식에는 박스로만 존재. 폐쇄망 self-host 가능한 후보 비교 필요.
- **골든셋** — 규모(N), 작성 주체, 정답·근거 판정 기준 미정. 성공 기준 전체가 여기에 묶여 있다.
- **CLAUDE.md가 .gitignore에 있음** — 요청대로 반영했으나, 이 때문에 팀 공유 컨벤션이
  버전 관리되지 않는다. 의도된 것인지 확인 필요.
