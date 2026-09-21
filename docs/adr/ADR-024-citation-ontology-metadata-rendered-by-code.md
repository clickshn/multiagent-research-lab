# ADR-024: 인용의 온톨로지 메타데이터는 코드가 렌더링한다 — LLM 입력에 넣지 않는다

- **Status:** Proposed
- **Date:** 2026-09-21
- **Decision:** 검색 결과의 온톨로지 메타(`release_type` / `tech_domains` / 발행일)를
  `RetrievedChunk` → `Citation`으로 실어 Writer 단계까지 보내되, **Writer의 LLM 입력
  (근거 블록)에는 넣지 않는다.** 보고서 끝의 출처 표를 **코드가** 검색 결과에서 직접
  렌더링한다. 메타 결측 문서는 빈칸이 아니라 `미확인`으로 표시해 "값이 없음"과 구별한다.
- **Scope:** multiagent-research-lab (`src/tools/retrieval.py` / `src/orchestrator/` 인용 경로)
- **Decision Source:** Human

---

## Context

### Problem

Session 4의 목표는 "인용에 온톨로지 메타데이터를 붙이는 것"이다. 붙이는 방법은 둘로 갈린다:
**모델에게 보여주고 쓰게 하거나, 코드가 붙이거나.** 둘은 같은 결과처럼 보이지만 감사
가능성의 근거가 다르다 — 전자에서는 출처 표의 정확도가 모델 정확도에 걸리고, 후자에서는
걸리지 않는다.

동시에 **코퍼스의 37.8%(14/37)에 온톨로지 메타가 아예 없다** (ADR-020 Consequences →
ADR-022 층 B). 이 문서들의 인용을 빈칸으로 두면 **"등급이 없는 문서"와 "등급을 아직 확인하지
않은 문서"가 같아 보인다.** 그 구별이 정확히 ADR-022가 골든셋을 층화한 이유다 — 두 문장은
다음 판단을 반대 방향으로 가른다.

원래 Session 4 계획에는 "어떤 필터로 좁혔는가"를 인용에 남기는 것도 있었다. **이번 범위에서
제외한다** — 지금 파이프라인에는 필터가 배선돼 있지 않고(Session 3의 필터는
`probe_retrieval.py`의 후처리다, ADR-023), 질의에서 필터 값을 고르는 플래너도 없다
(Session 3이 쓴 것은 앵커에서 유도한 오라클이다). 선행 조건 둘이 없으면 "필터 경로"라고
적을 내용 자체가 없다.

### Constraints

- **`PROMPT_VERSION`을 올리면 전/후 비교가 깨진다.** 인용 형식의 효과를 보려면 다른 것이
  바뀌지 않아야 한다 (ADR-002 모델 고정과 같은 원칙 — 변화의 원인을 귀속시킬 수 있어야 한다).
- **검색 결과가 바뀌면 안 된다.** 질의·`where` 절·`k`·정렬·점수 계산은 불변이어야 한다.
- `derived_summary` / `derived_impact_rationale`는 모델 패러프레이즈라 계약 §6이 색인·인용을
  금지한다. 인용 경로로 옮기면 Writer가 원문 대신 패러프레이즈를 인용할 수 있게 된다.
- 이번 세션의 LLM 호출 상한은 **0건**이다.

## Decision

### Selected

- **Technology:** 기존 스택 그대로. 새 의존성 없음.
- **Architecture:** 메타는 **검색 → State → 렌더러**로 흐르고, **모델을 통과하지 않는다.**
  `RetrievedChunk`에 `published` / `release_type` / `tech_domains` / `has_ontology`를
  additive로 추가하고, `_select_citations()`가 그대로 `Citation`에 옮긴다.
  Writer 노드는 모델 본문 뒤에 `citations.render_source_table()`의 출력을 덧붙인다.
- **Implementation:**
  - `src/orchestrator/citations.py` (신규) — 출처 표 렌더링과 3상태 표기.
  - `_format_evidence()`(= LLM 입력)는 **변경 없음.** `PROMPT_VERSION`도 그대로다.
  - 결측 표기 3상태: 값 있음 → 값 / export 거쳤는데 빈 값 → `없음` /
    **export를 안 거침 → `미확인`**. 판정 근거는 `has_ontology`(메타의 `export_id` 유무)다.
  - **발행일은 두 층 모두 표시된다** — 스냅샷에도 `published`가 있다. 결측은 "이 문서를
    모른다"가 아니라 "온톨로지 라벨링을 거치지 않았다"는 뜻이고, 표기도 그 범위다.

## Rationale

1. **옮겨 적기는 틀릴 수 있고, 틀리면 감사 가능성이 무너진다.** 모델이 `Community`를
   `Paper`로 쓰면 출처 표는 그럴듯하게 틀린다. 등급은 검색 결과에 **이미 정확히 있는 값**이므로
   모델에게 물을 이유가 없다 — ADR-006의 "셀 수 있는 것은 모델에게 묻지 않는다"와 같은
   원칙이고, 거기서 인용 수 판정을 기계에 맡긴 것과 같은 이유다.
2. **`release_type`을 Writer가 보면 근거 신뢰도 신호로 읽는다.** `Paper`가 붙은 근거를 더
   믿고 쓰는 식이다. 이것은 Researcher 프롬프트에 유사도 점수를 노출한 것과 **같은 함정**이며
   (session-13 §6.2, 아직 열려 있는 결정 항목), 같은 실수를 인용 경로에서 반복하지 않는다.
   무엇이 근거인지는 검증 단계가 판정하고, 문서의 종류는 독자가 읽는다.
3. **LLM 입력이 그대로면 `PROMPT_VERSION`이 바뀌지 않는다.** 그래야 "인용 형식 하나만
   바뀌었다"가 **정확히** 참이 되고, 전/후 비교가 성립한다. 프롬프트를 건드리면 이번 변경의
   효과와 프롬프트 변경의 효과를 나눌 수 없다.
4. **결측을 빈칸으로 두면 두 사실이 하나로 뭉개진다.** ADR-022는 바로 그 구별을 위해
   골든셋을 층화했다. 인용에서 다시 합치면 층화의 이유가 인용 쪽에서 무효가 된다.

## Evidence

- **Experiment:** `RetrievedChunk` 확장 전/후로 `scripts/probe_retrieval.py`를 같은 인덱스에서
  각각 1회 실행하고 `scripts/compare_probe_runs.py`로 대조했다. **30케이스 전 필드가
  시간 값을 제외하고 바이트 단위로 동일**하다 (`sha256:00f6353bcd68…`, 양쪽 동일).
  차이가 난 유일한 키는 `retrieval_latency_s`(벽시계)였다.
  산출물: `docs/eval/probe-retrieval-session-16-{before,after}-chunk-metadata.json`.
- **Experiment:** `scripts/probe_citation_format.py` — 같은 검색 결과로 전/후 형식을 렌더링하고,
  모델이 받는 근거 블록이 **메타 포함본과 제거본에서 바이트 단위로 같은지**를 해시로 확인한다.
  산출물: `docs/eval/citation-format-session-16.md`. **LLM 호출 0건.**
- **Production Data:** 코퍼스 37건 중 온톨로지 메타 보유 23건 / 결측 14건(전부 스냅샷 arXiv).

## Alternatives

### (가) Writer 프롬프트에 메타를 넣고 모델이 인용에 적게 한다

- **Pros:** 산문 안에서 "2026년 5월 발표된 Paper에 따르면"처럼 자연스럽게 활용할 수 있다.
  구현이 렌더러 없이 프롬프트 수정만으로 끝난다.
- **Cons:** 출처 표의 정확도가 모델 정확도에 걸린다. `release_type`이 근거 신뢰도 신호로
  읽힌다. `PROMPT_VERSION`이 올라가 전/후 비교의 전제가 깨진다.
- **Rejected because:** 위 Rationale 1~3. 특히 **감사 가능성을 모델 정확도에 거는 것**이
  이 프로젝트의 1순위 목표(출처 제시)와 직접 충돌한다.
- **Recheck if:** Writer 산문에서 메타를 활용하기로 결정하는 경우 — **별도 `PROMPT_VERSION`
  변경으로 v1.2 항목에서 다룬다.** 그때는 출처 표(코드 렌더링)와 산문(모델)이 공존하며,
  표가 정본이라는 것을 명시해야 한다.

### (나) `retrieval.py`를 불변으로 두고 별도 조회 계층에서 메타를 붙인다

- **Pros:** 검색 계층 변경이 0줄이다. session-14·15가 지켜온 형태와 같다.
- **Cons:** 메타의 출처가 인덱스가 아니라 스냅샷(또는 Chroma 두 번째 개방)이 된다. 전자는
  **인덱스와 갈릴 수 있고**(인덱스가 낡으면 인용의 메타만 최신이 된다), 후자는 §7.2의
  동시 실행 금지에 걸린다.
- **Rejected because:** 진실 공급원이 둘이 된다. 인용은 **검색된 그 문서**를 설명해야 하므로
  메타도 검색 결과와 같은 곳에서 와야 한다. 대신 변경을 additive로 제한하고, 검색 결과
  불변을 30케이스 전건 대조로 확인했다 (Evidence).

## Consequences

### Positive

- 출처 표의 값이 **모델 정확도와 무관**하다. 재현 가능하고 diff 가능하다.
- `PROMPT_VERSION`이 그대로라 session-12~15 수치와의 비교선이 끊기지 않는다.
- 층 B(메타 결측 14건)가 보고서 표면에서 **눈에 보인다.** 지금까지는 코퍼스 통계에만 있었다.

### Negative

- Writer가 메타를 모르므로 산문에서 활용하지 못한다. 표와 본문이 분리돼 있다.
- 출처 표가 마크다운 표라 보고서 형식이 마크다운이라는 가정이 하나 늘었다.

### Risks

- ⚠️ **`미확인`이 많은 것이 정상처럼 굳을 수 있다.** 14/37은 적은 비율이 아니다. 표기는
  결측을 **보이게** 할 뿐 해소하지 않는다. backfill은 여전히 ADR-022 Alternatives (가)에서
  기각된 상태이고, 생산자 레포의 게이트 이식(session-11 §5)이 선행 조건이다.
- ⚠️ **`has_ontology`는 `export_id` 키의 존재로 판정한다.** 생산자가 그 키를 빼면 전 문서가
  `미확인`으로 보인다. 계약 필수 필드가 아니라 `ontology_metadata()`가 싣는 값이므로
  MARA 쪽 규약이다 — 바뀌면 `tests/test_citations.py`가 깨진다.

## Implementation

- [x] `RetrievedChunk` additive 확장 + `_to_chunks` 매핑 (`derived_*` 제외 유지)
- [x] `Citation` 확장 + `_select_citations` 전달
- [x] `src/orchestrator/citations.py` 렌더러 + Writer 노드 부착
- [x] 테스트 — `tests/test_citations.py` (12건): 3상태 표기 · LLM 입력 무유출 ·
      메타 유무에 따른 입력 바이트 동일성 · `derived_*` 미유출
- [x] 검색 결과 불변 확인 — `scripts/compare_probe_runs.py`로 30케이스 전건 대조
- [x] 전/후 대조 산출물 — `scripts/probe_citation_format.py`
- [ ] 실제 보고서(Writer 호출 포함) 위에서의 확인 — **LLM 호출 상한 0건이라 이번 세션 범위 밖**

## Reversibility

- **Reversible:** Yes
- **Rollback:** 렌더러 호출 한 줄(`attach_source_table`)을 빼면 보고서가 이전 형태로 돌아간다.
  `RetrievedChunk`·`Citation`의 새 필드는 기본값이 있어 남아 있어도 무해하다.
  인덱스 재생성 불필요 — 메타는 이미 인덱스에 있었고 읽지 않았을 뿐이다.
- **Migration Cost:** Low

## Review Trigger

- **Writer 산문에서 메타를 활용하기로 하는 경우** — 별도 `PROMPT_VERSION` 변경으로
  v1.2 항목에서 다룬다 (Alternatives (가) Recheck if).

## References

- **Related ADR:** ADR-022 (층 A/B — 결측을 구별해야 하는 이유), ADR-020 (온톨로지 메타 적재
  경계), ADR-018 (출력 계약 — `derived_*` 인용 금지), ADR-006 (셀 수 있는 것은 모델에게 묻지
  않는다), ADR-023 (필터는 아직 프로브 후처리다 — 필터 경로 인용이 범위 밖인 이유)
- **Documentation:** `docs/contracts/ai-news-ontology-export-v1.md` §5·§6,
  `docs/eval/citation-format-session-16.md`,
  `docs/eval/probe-retrieval-session-16-{before,after}-chunk-metadata.json`

## AI/ML Details

- **Model:** 변경 없음 (`gemma-4-31B-it`, ADR-002 고정). **이 결정은 모델을 호출하지 않는다.**
- **Evaluation:** 검색 결과 불변을 30케이스 전건 바이트 대조로 확인. 인용 형식의 효과(사람이
  출처를 판단하는 데 도움이 되는가)는 골든셋에 측정 항목이 없어 아직 재지 않는다.
- **Inference:** 해당 없음 — 출처 표는 추론 없이 렌더링된다.
