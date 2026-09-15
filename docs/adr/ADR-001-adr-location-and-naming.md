# ADR-001: 아키텍처 결정 기록을 docs/adr/ 개별 파일로 관리

- **Status:** Proposed
- **Date:** 2026-09-15
- **Decision:** 결정 기록은 `docs/adr/` 아래 `ADR-NNN-slug.md` 형식의 개별 파일로 남긴다.
- **Scope:** multiagent-research-lab (레포 전체 / 문서 규약)
- **Decision Source:** Human

---

## Context

### Problem

폐쇄망 멀티에이전트 리서치 시스템을 처음부터 설계하는 단계라, 앞으로 오케스트레이션
프레임워크·로컬 서빙 스택·벡터 DB 등 되돌리기 비용이 큰 결정이 연달아 나온다.
결정의 근거와 기각된 대안을 남길 위치와 형식을 먼저 고정해두지 않으면, 이후 세션에서
"왜 이렇게 했는지"를 추적할 수 없다.

### Constraints

- 폐쇄망 환경이라 외부 SaaS(이슈 트래커, 위키 등)를 결정 기록 저장소로 쓸 수 없다.
- 세션이 끊겨도 이어서 작업해야 하므로, 기록은 레포 안에 코드와 함께 버전 관리되어야 한다.
- `adr-recorder` 스킬이 `docs/adr/` 경로와 `ADR-NNN` 번호 규칙을 전제로 동작한다.

## Decision

### Selected

- **Technology:** 플레인 마크다운 파일 (별도 도구 없음)
- **Architecture:** 결정 1건 = 파일 1개. `docs/adr/ADR-NNN-slug.md`, NNN은 001부터 증가하는 3자리 일련번호.
- **Implementation:** `docs/adr/` 디렉터리를 두고, `adr-recorder` 스킬이 이 경로에만 파일을 생성·수정한다. CLAUDE.md에 ADR 자동 기록 규칙을 명시한다.

## Rationale

1. 결정 1건당 파일 1개면 PR diff에서 어떤 결정이 추가·변경됐는지 한눈에 보이고, 다른 문서에서 `ADR-002`처럼 개별 결정을 안정적으로 참조할 수 있다.
2. 레포 안의 마크다운이라 폐쇄망에서도 추가 인프라 없이 동작하고, 코드와 같은 커밋 이력에 남아 결정 시점과 코드 변경 시점을 대조할 수 있다.
3. `adr-recorder` 스킬이 이 경로·번호 규칙을 전제로 하므로, 규칙을 맞추면 결정 기록이 수작업 없이 자동화된다.

## Alternatives

### docs/decisions.md 단일 파일

- **Pros:** 파일이 하나뿐이라 처음에는 관리가 단순하고, 전체 결정을 한 번에 훑기 쉽다.
- **Cons:** 결정이 쌓일수록 파일이 비대해지고, 모든 결정 변경이 같은 파일의 diff로 섞인다.
- **Rejected because:** 결정이 누적되면 diff 리뷰와 개별 결정 참조가 어려워진다.

### GitHub Issues / Wiki로 관리

- **Pros:** 검색·라벨·코멘트 등 협업 기능을 그대로 쓸 수 있고, 별도 문서 규약이 필요 없다.
- **Cons:** 기록이 코드 레포 바깥에 있어 코드와 함께 버전 관리되지 않는다.
- **Rejected because:** 폐쇄망 환경이라 외부 SaaS를 사용할 수 없다.

## Consequences

### Positive

- 이후 모든 아키텍처 결정이 동일한 템플릿으로 쌓여, 세션이 바뀌어도 근거 추적이 가능하다.
- 결정 기록이 코드와 같은 커밋 이력에 남는다.

### Negative

- 결정 건수가 늘어나면 디렉터리 안 파일 수가 많아져, 전체를 훑으려면 인덱스가 따로 필요해질 수 있다.

### Risks

- 번호를 수동으로 매기면 브랜치가 갈릴 때 번호가 충돌할 수 있다. 현재는 단일 브랜치 작업이라 문제가 되지 않지만, 병렬 작업이 생기면 재검토가 필요하다.

## Implementation

- [x] `docs/adr/` 디렉터리 생성
- [x] CLAUDE.md에 ADR 자동 기록 규칙 명시
- [x] `adr-recorder` 스킬이 이 레포에서 경로·번호 규칙대로 동작하는지 확인 (본 문서가 그 산출물)
- [ ] 결정이 10건을 넘으면 `docs/adr/README.md` 인덱스 도입 검토

## Reversibility

- **Reversible:** Yes
- **Rollback:** 마크다운 파일이므로 다른 형식으로 옮기거나 단일 파일로 합치면 된다. 참조 링크(`ADR-NNN`)만 함께 갱신하면 코드 변경은 필요 없다.
- **Migration Cost:** Low

## References

- **Documentation:** `CLAUDE.md` (ADR 자동 기록 규칙), `.claude/rules/` (경로별 컨벤션)
