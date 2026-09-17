# ADR-019: 온톨로지 export 스냅샷(JSONL + manifest)을 MARA 레포에 커밋한다

- **Status:** Accepted
- **Date:** 2026-09-17
- **Decision:** 생산자(`ai-news-ontology`)가 만든 계약 v1 export 산출물(`data/corpus/<source>/<export_id>.jsonl` + `<export_id>.manifest.json`)을 MARA 레포에 **커밋한다.** 생산자 레포는 `data/`를 `.gitignore` 하므로, 커밋하지 않으면 이 코퍼스는 **어느 레포에서도 버전 관리되지 않는다.**
- **Scope:** multiagent-research-lab (코퍼스 스냅샷 보관 / 측정 재현성)
- **Decision Source:** Human

---

## Context

계약 1단계 export 30건이 생산자 로컬에 만들어졌다(생산자 session-01 §2). 두 레포의
`.gitignore` 상태가 서로 다르다.

| | `data/` | 결과 |
|---|---|---|
| `ai-news-ontology` (생산자) | **gitignore 대상** | export 산출물이 push되지 않는다. 그 머신의 로컬 디스크에만 있다 |
| `multiagent-research-lab` (MARA) | 추적 대상 (`data/corpus/arxiv/*.json` 16건이 이미 커밋돼 있다) | 넣으면 버전 관리된다 |

즉 **"MARA가 커밋하지 않는다"는 "아무도 보관하지 않는다"와 같은 말**이다. 파일이 사라지면
다시 만드는 비용은 무료가 아니다 — 재생성에는 외부 벤더 API 호출 약 $2.13이 다시 들고
(생산자 session-01 §5), 그보다 중요하게 **같은 파일이 다시 나온다는 보장이 없다**:
피드는 시점 기준이라 같은 날 다시 돌려도 수집 대상이 다르고, 추출은 LLM 출력이다.

Session 1(baseline 재측정), Session 2(크로스링구얼), Session 3(`release_type` 필터 대조)이
전부 이 코퍼스 위에서 잰다.

## Decision

1. `data/corpus/**/*.jsonl`과 `data/corpus/*.manifest.json`을 **커밋한다.**
2. **manifest는 JSONL보다 오래 남긴다.** v1은 전량 스냅샷이라(계약 §10) 새 export가 오면
   이전 JSONL을 **교체**해야 한다 — 두 개를 같이 두면 `doc_id` 중복으로 import가 실패한다
   (계약 §7-2). 그때 **manifest는 지우지 않는다.** 어휘 삭제·개명·쪼개짐 검사(계약 §5)의
   유일한 근거가 이전 어휘 스냅샷이고, MARA는 어휘를 복제하지 않기로 했기 때문이다.
   `contract_import.discover_manifests()`가 JSONL 없는 manifest도 읽는다.
3. **인덱스(`var/chroma/`)는 여전히 커밋하지 않는다.** 재현 기준은 인덱스가 아니라
   스냅샷 + 임베딩 모델이다 (ADR-004, ADR-011과 같은 원칙).

## Rationale

**ADR-018이 런타임 API 결합을 기각한 것과 같은 논리다.** 그때의 이유는 "재현의 기준은
살아 있는 서비스가 아니라 파일"이었다. 파일로 받아 놓고 그 파일을 버전 관리하지 않으면,
결합만 끊고 재현성은 얻지 못한다 — **파일이 있다는 사실 자체가 재현 근거가 되지 않는다.
어느 파일이었는지 가리킬 수 있어야 근거가 된다.**

커밋하면 Session 1~3의 측정치가 **특정 스냅샷에 고정**된다. 수치가 움직였을 때
`git log data/corpus/`로 코퍼스가 그 사이에 바뀌었는지 1초 만에 답할 수 있다. 커밋하지
않으면 그 질문에 **영원히 답할 수 없다** — 하네스 효과를 재려는 프로젝트에서 코퍼스는
고정돼야 하는 쪽인데, 고정됐는지 확인할 방법이 없어진다.

`provenance`를 필수화한 것(계약 §3.3)과 같은 성질의 조치다. 저쪽은 "어느 모델이 만들었나",
이쪽은 "어느 파일 위에서 쟀나"를 기록한다. 둘 중 하나만 있으면 반쪽이다.

## Evidence

- 현재 `data/corpus/` 전체 **104 KB** — JSONL 73.6 KB(30 레코드) + manifest 1.8 KB +
  기존 arXiv 스냅샷 28.5 KB. 레코드당 약 2.5 KB.
- 2단계 250 레코드면 JSONL이 약 **600 KB**, `data/corpus/` 누적 약 **630 KB**.
  레포에 부담이 되는 규모가 아니다.
- 재생성 비용: 30건 실측 **약 $2.13**(생산자 session-01 §5). 250건이면 약 $16.
- 투입 데이터는 공개 RSS/Atom 피드의 메타데이터와 발췌뿐이다 (manifest `license_note`,
  ADR-004).

## Alternatives

**(가) 커밋하지 않고 로컬에만 둔다.** 레포가 가볍다. 그러나 수치의 출처를 재구성할 수
없고, 파일이 사라지면 **돈을 다시 내고도 같은 것을 못 받는다**(피드는 시점 기준,
추출은 LLM 출력). 기각.

**(나) 생산자 레포가 `.gitignore`를 풀고 커밋한다.** 생산자에 그 파일을 둘 이유가 없다 —
쓰는 쪽은 MARA이고, 측정도 MARA에서 한다. 소비자가 자기 입력을 고정하는 것이 맞다. 기각.

**(다) Git LFS / 외부 스토리지.** 630 KB에 인프라를 하나 늘리는 것은 과하다. 규모가
문제가 되면 그때 다시 본다 (아래 Review Trigger).

## Consequences

- `git log data/corpus/`가 **코퍼스 변경 이력**이 된다. 측정 결과와 코퍼스 버전을
  커밋 시점으로 맞춰 볼 수 있다.
- 지난 export의 manifest가 레포에 누적된다 (건당 약 2 KB). 이것은 낭비가 아니라
  **어휘 이력**이다 (계약 §5).
- ⚠️ **export 교체 절차가 생겼다.** 2단계 export가 오면 `ontology-20260917-120733.jsonl`
  두 개를 **지우고** 새 것을 넣는다. manifest는 남긴다. 지우지 않으면 `doc_id` 중복으로
  import가 실패한다 — 조용히 틀리는 것이 아니라 멈추므로 실패 모드로서는 안전한 쪽이다.
- ⚠️ **ADR-004 전제에 묶여 있다.** 공개 자료가 아닌 것이 코퍼스에 들어오는 순간 이 결정은
  **즉시 무효**다. 커밋은 되돌려도 git 이력에 남으므로, `var/traces/` 같은 로컬 산출물보다
  **되돌리기가 더 어렵다** (`docs/governance.md` 로컬 산출물 기준과 다른 점이 이것이다).

## Implementation

- [x] `data/corpus/arxiv/ontology-20260917-120733.jsonl` (10줄)
- [x] `data/corpus/news/ontology-20260917-120733.jsonl` (20줄)
- [x] `data/corpus/ontology-20260917-120733.manifest.json`
- [x] `discover_manifests()` — JSONL 없는 manifest도 읽어 어휘 이력을 보존
- [ ] 2단계 export 도착 시: 위 JSONL 2개 교체, manifest는 추가(삭제 금지)

## Reversibility

- **Reversible:** Partial
- **Rollback:** 파일 삭제 + `build_index.py --reset`이면 코퍼스에서는 빠진다. 다만
  **git 이력에서는 지워지지 않는다** — 이력 재작성이 필요하고, 그건 공개 레포에서
  되돌리기 비싼 작업이다.
- **Migration Cost:** Low (코퍼스에서 제거) / High (git 이력에서 제거)

## Review Trigger

**임계값을 데이터가 커지기 전에 적어 둔다** — 커진 뒤에 정하면 어떤 크기든 정당화할 수 있다.

- `data/corpus/` 누적이 **5 MB를 넘으면** 보관 방식을 다시 본다 (LFS / 외부 스토리지 /
  오래된 export 제거). 2단계 250건(약 630 KB)은 여기 한참 못 미친다.
- export가 **5회를 넘어 쌓이면** — v1은 전량 스냅샷이라 이전 export는 대부분 중복이다.
  JSONL 보관 정책(최신 N개만 유지 등)을 정한다. **manifest는 이 정책의 대상이 아니다.**
- **공개 자료가 아닌 데이터가 코퍼스에 들어오는 순간** — 이 결정은 즉시 무효이고,
  커밋 여부가 아니라 반입 여부부터 다시 본다 (ADR-004, `docs/governance.md`).

## References

- **Related ADR:** ADR-004(코퍼스 반입 범위 — 공개 자료 전제), ADR-018(출력 계약 — 런타임 API 결합 기각의 논리), ADR-011(임베딩 리비전 고정 — 재현 기준), ADR-020(온톨로지 메타데이터 적재와 검색 경로 경계)
- **Documentation:** `docs/contracts/ai-news-ontology-export-v1.md` §2·§5·§7·§10, 생산자 레포 `docs/handoff/session-01.md` §2·§5
