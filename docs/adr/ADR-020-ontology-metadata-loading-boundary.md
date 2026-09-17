# ADR-020: 온톨로지 필드는 메타데이터로 싣기만 하고, 검색 경로는 Session 3까지 건드리지 않는다

- **Status:** Accepted
- **Date:** 2026-09-17
- **Decision:** 계약 v1의 온톨로지·provenance 필드를 `CorpusDoc.extra` → Chroma 메타데이터로 평탄화해 싣는다. 파생 텍스트는 `derived_` 접두어를 붙인다. **`src/tools/retrieval.py`는 이번 세션에서 한 줄도 바꾸지 않는다** — `release_type` 필터 배선은 Session 3의 단독 변경이다.
- **Scope:** multiagent-research-lab (코퍼스 로더 / 인덱싱 메타데이터 / 세션 경계)
- **Decision Source:** Human

---

## Context

계약 §3.2가 정한 온톨로지 7필드를 실을 자리가 없었다. `CorpusDoc.as_metadata()`가
스칼라 6키(`doc_id`·`source`·`title`·`locator`·`url`·`published`)만 돌려주고 `extra`를
**조용히 버렸기** 때문이다. 에러가 아니라 정상 동작으로 보이는 종류의 결함이다.

동시에 계약 §8.2와 ADR-018이 **필터 배선을 Session 3으로 미뤄 뒀다.** 이유는 취향이
아니라 측정 설계다 — 코퍼스가 16건에서 37건이 되는 변경과 검색 조건이 바뀌는 변경을
한 세션에 넣으면, 수치가 움직였을 때 어느 쪽 때문인지 **영원히 가를 수 없다.**
모델을 고정하고 하네스만 움직이는 원칙(ADR-002)이 그대로 적용된다.

## Decision

1. **`as_metadata()`가 `extra`를 얹는다.** 단, 인용에 필요한 6키는 `extra`가 **덮어쓸 수
   없다**. 덮어쓸 수 있으면 출처 표기 규칙이 문서마다 달라지고, 이 프로젝트의 1순위
   목표(출처 제시)가 데이터 쪽에서 무너진다.
2. **평탄화는 `contract_import.ontology_metadata()`가 한다.** Chroma는 스칼라만 받으므로
   리스트는 `|`로 잇고, `None`은 키째 버린다.
3. **파생 텍스트는 `derived_summary` / `derived_impact_rationale`로 싣는다.** 계약 §6이
   색인·인용을 금지하는 값이므로, 저장 계층에서 **이름만 봐도** 인용 금지가 보이게 한다.
4. **`src/tools/retrieval.py`는 건드리지 않는다.** `_to_chunks()`는 `derived_*`를
   `RetrievedChunk`로 옮기지 않는다 — 옮기는 순간 Writer가 원문 대신 모델 패러프레이즈를
   인용할 수 있게 된다.
5. **병합 문서의 길이 메타데이터는 실제로 색인되는 텍스트를 가리킨다.** arXiv 병합에서
   `text`는 스냅샷 쪽을 유지하므로(계약 §4.3), export의 `text_chars`/`text_truncated`를
   그대로 두면 **버려지는 쪽(피드 발췌)을 설명하는 값**이 남는다. 재계산해서 덮는다.

## Rationale

**"싣기만 한다"가 애매한 상태가 아니라 정의된 상태여야 한다.** 메타데이터에 값이 있는데
아무도 안 쓰는 것은 미완성처럼 보인다 — 그래서 다음 세션에서 "이왕 있으니 필터를
붙이자"가 되기 쉽다. 그 유혹을 막는 것이 이 ADR의 절반이다.

`release_type`만 스칼라로 저장되는 것도 우연이 아니다. `tech_domains`는 다중값이라
`|`로 이어 붙였고, **Chroma에는 LIKE가 없어 이 문자열에는 부분 일치 필터를 걸 수 없다.**
`tech_domains` 필터가 필요해지면 저장 형태부터 다시 정해야 한다 — 이것은 제약이 아니라
계약 §8.2가 `release_type`을 1순위로 고른 이유의 기술적 뒷면이다.

## Evidence

- 1단계 30 레코드 반입 후 코퍼스 **37건**(arxiv 24 + news 13), 계약 §12.2 체크리스트
  **12 PASS / 0 FAIL / 1 PENDING**(생산자 레포에서만 판정 가능한 `prompt_sha256` 실측).
- `release_type` 메타데이터가 실린 문서는 **23건뿐**이다 — 계약 이전에 수집된 arXiv
  스냅샷 14건에는 온톨로지가 아예 없다 (아래 Consequences).
- 신규 테스트 42건 (`tests/test_contract_import.py`), 전체 **156 passed**.

## Alternatives

**(가) 0.5에서 `release_type` 필터까지 배선한다.** 한 세션에 끝나 보인다. 그러나 Session 1
baseline이 "코퍼스가 10배가 된 효과 + 필터 효과"의 합이 되어 분해할 수 없다. 기각 —
ADR-018과 계약 §8.2에서 이미 결정된 사항이고, 이 ADR은 그 경계를 코드 쪽에 고정한다.

**(나) `summary`를 색인 텍스트에 포함한다.** 한국어 문서의 검색 품질이 올라갈 수 있다.
그러나 인용이 원문이 아니라 Opus 5의 패러프레이즈를 가리키게 된다 — 인용 정확도가 1순위
지표인 시스템에서 지표 자체가 무의미해진다 (계약 §6, ADR-004). 기각. 허용하려면
별도 ADR이 필요하다.

**(다) 파생 텍스트를 메타데이터에서도 뺀다.** 가장 안전하지만, 리포팅·분포 분석에서
쓸 값을 버리게 된다. 접두어로 표시하는 선에서 절충했다.

## Consequences

- ⚠️ **스냅샷 arXiv 14건에는 `release_type`이 없다.** Session 3이
  `where={"release_type": "Paper"}`를 걸면 이 14건이 **조용히 전부 빠진다** — 결과가
  0건이 되는 것이 아니라 **덜 나오고, 그게 필터 효과처럼 보인다.** Session 3은 필터를
  배선하기 전에 이 14건을 어떻게 다룰지(재추출 / `$or` 허용 / 대조군에서 제외) 먼저 정한다.
- 메타데이터 키가 문서마다 다르다. Chroma는 허용하지만, 키 존재 여부가 곧 "계약 경로로
  들어왔는가"를 뜻하게 된다 (`merged_with_export`, `export_id`로 구분 가능).
- `CorpusDoc.extra`의 타입이 `dict[str, str]`에서 스칼라 4종으로 넓어졌다.

## Implementation

- [x] `src/tools/corpus.py` — `as_metadata()`가 `extra`를 얹고, 인용 6키를 보호
- [x] `src/tools/contract_import.py` — 평탄화·`derived_` 접두어·병합 시 길이 재계산
- [x] `scripts/verify_import.py` — 계약 §12.2 판정 (MARA 쪽 정본)
- [x] `tests/test_contract_import.py` — 위반 1건씩 심은 42개 테스트
- [x] **`src/tools/retrieval.py` 변경 없음** (`git diff`로 확인 가능)
- [ ] **Session 3:** `release_type` 필터 배선 + 스냅샷 14건 처리 결정. **그것 하나만** 바꿔 대조

## Reversibility

- **Reversible:** Yes
- **Rollback:** 메타데이터를 빼려면 `ontology_metadata()`가 돌려주는 키를 줄이고
  `build_index.py --reset`. 인덱스 재생성 비용뿐이고 외부 호출이 없다.
- **Migration Cost:** Low

## Review Trigger

- **Session 3 진입 시** — 스냅샷 14건의 `release_type` 공백 처리를 정하지 않고 필터를
  배선하면 대조가 성립하지 않는다.
- `tech_domains`·`companies`로 필터를 걸어야 할 수요가 생기는 경우 — `|` 연결 문자열로는
  안 되므로 저장 형태부터 다시 본다.
- 파생 텍스트를 색인하기로 결정하는 경우 — 계약 §6 변경 + 별도 ADR 사안이다.

## References

- **Related ADR:** ADR-018(출력 계약 — §8.2 배선 경계), ADR-019(스냅샷 커밋), ADR-005(VectorDB·메타데이터 필터 지점), ADR-002(모델 고정 — 한 번에 하나만 바꾼다), ADR-004(파생 텍스트 기각 논리)
- **Documentation:** `docs/contracts/ai-news-ontology-export-v1.md` §3.2·§6·§8.2, `docs/handoff/session-10.md`
