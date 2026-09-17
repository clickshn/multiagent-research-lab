# 출력 계약 v1 — `ai-news-ontology` → `multiagent-research-lab`

- **Status:** Draft (session-09 작성, 미구현)
- **Contract Version:** `1.0`
- **생산자(Producer):** `ai-news-ontology` (별도 레포)
- **소비자(Consumer):** `multiagent-research-lab` (MARA)
- **관련 ADR:** ADR-018(이 계약), ADR-004(코퍼스 반입 범위), ADR-002(모델 고정), ADR-005(VectorDB)

---

## 0. 이 문서의 지위

**두 레포는 코드를 공유하지 않는다.** 공유하는 것은 이 문서가 정의하는 **파일 형식
하나뿐**이다. 생산자는 이 형식으로 파일을 쓰고, 소비자는 이 형식만 읽는다. 어느 쪽도
상대의 모듈을 import 하지 않고, 상대가 살아 있어야 동작하지도 않는다.

계약을 바꿀 때는 이 문서를 먼저 고치고 `contract_version`을 올린다. 코드가 먼저
바뀌면 계약이 아니라 우연이 된다.

**통제어휘(`tech_domain` / `release_type`)의 정본은 `ai-news-ontology` 쪽
`extraction/schema.py`의 Enum이다.** MARA는 어휘를 복제하지 않는다 — 복제하는 순간
두 곳이 갈리고, 갈린 것을 알아챌 방법이 없다. MARA는 매 export의 manifest에 실린
어휘 스냅샷을 **검증용으로만** 쓴다 (§5).

## 1. 왜 Vault를 직접 읽지 않고 별도 export인가

Obsidian Vault의 노트는 사람이 읽는 종착지다. MARA가 그것을 파싱할 수 없는 이유는
취향이 아니라 4가지 사실이다.

| # | 사실 | 근거 |
|---|---|---|
| 1 | **노트에 원문 본문이 없다.** 본문 섹션에 들어가는 텍스트는 `ontology.summary`(한국어 2~3문장)뿐이고 `RawItem.body`는 프롬프트에만 쓰이고 버려진다 | `obsidian_writer/mapper.py: render_body()`; 골든셋 파일의 `"_input"` — *"수집 파이프라인은 본문을 보존하지 않는다. 이 파일이 원문의 유일한 사본이다."* |
| 2 | **안정적 문서 ID가 없다.** 파일명은 `{처리일}-{소스}-{제목슬러그}.md`이고 슬러그 충돌 시 `-2`, `-3`이 붙는다. 처리 시각·제목·처리 순서에 의존한다. 동일성 판정은 frontmatter `source_url` 비교로만 한다 | `obsidian_writer/writer.py: build_filename()`, `resolve_path()`, `_existing_source_url()` |
| 3 | **발행일·수집일이 유실된다.** `RawItem.published_at` / `collected_at`은 수집 단계에 있지만 frontmatter에는 `date`(=처리일)와 `processed_at`만 실린다 | `obsidian_writer/mapper.py: FRONTMATTER_ORDER` |
| 4 | **Vault는 사람이 손으로 고치는 디렉터리다.** 생산자 코드 자신이 *"사용자가 Obsidian에서 손으로 고친 노트가 파싱되지 않을 수 있다"*를 전제로 YAML 전체 파싱을 피한다 | `obsidian_writer/writer.py: _existing_source_url()` docstring |

→ **export의 소스는 Vault가 아니라 파이프라인 중간 산출물**이다:
`RawItem`(수집) + `NewsOntology`(추출) + 실행 메타데이터. Vault 쓰기와 export는
같은 입력에서 갈라지는 **형제 출력**이지, 한쪽이 다른 쪽의 후처리가 아니다.

```
RawItem ──▶ 관련성 게이트 ──▶ NewsOntology ──┬──▶ obsidian_writer/  (사람용)
                                            └──▶ export/           (기계용, 이 계약)
```

## 2. 전송 형식

```
data/corpus/news/<export_id>.jsonl          # 레코드 1건 = 1줄 (JSON object)
data/corpus/arxiv/<export_id>.jsonl         # source=="arxiv"인 레코드는 여기로 (§4.3)
data/corpus/<export_id>.manifest.json       # 실행 단위 메타 + 어휘 스냅샷 (export당 1개)
```

> **manifest 위치 정정 (session-10).** 초안은 manifest를 `news/` 아래에 적었으나,
> `counts`가 두 출처의 **합계**라 출처 디렉터리 하나에 두면 나머지 하나를 설명할 수 없다.
> 생산자의 실제 출력도 `data/corpus/<export_id>.manifest.json` 하나다. MARA 로더는
> 두 위치를 모두 찾아본다(`contract_import.load_manifest()`) — 이미 나간 산출물을
> 깨뜨리지 않기 위해서다. 형식·필드는 그대로이므로 `contract_version`은 유지한다 (§9).
>
> **manifest는 JSONL보다 오래 남긴다** (ADR-019). v1은 전량 스냅샷이라 새 export가 오면
> 이전 JSONL을 교체해야 하는데(§10, 두 개를 같이 두면 §7-2 `doc_id` 중복으로 실패),
> 그때 manifest까지 지우면 **어휘 삭제·개명 검사(§5)의 근거가 사라진다.**

- `<export_id>` = `ontology-YYYYMMDD-HHMMSS` (생산자의 export 실행 시각, KST).
- **JSONL인 이유:** 한 줄이 한 레코드라 부분 실패가 파일 전체를 버리게 하지 않고,
  append로 이어 쓸 수 있다. 생산자 쪽 수집이 rate limit으로 중단되는 것이 상수 조건이라
  (MARA session-03~08의 arXiv 429) 재개 가능한 형식이어야 한다.
- 인코딩 UTF-8, 개행 `\n`, `ensure_ascii=false`.
- **파일은 커밋한다** — MARA의 재현 기준은 인덱스가 아니라 스냅샷이다 (ADR-004).

## 3. 레코드 스키마

필드 순서는 의미가 없다. `필수`는 "없으면 import가 실패한다"는 뜻이다.

### 3.1 최상위

| 필드 | 타입 | 필수 | 채우는 쪽 | 규칙 |
|---|---|---|---|---|
| `contract_version` | string | O | 생산자 | `"1.0"`. major가 다르면 MARA가 거부한다 |
| `doc_id` | string | O | 생산자 | §4의 규칙으로 **결정적으로** 생성. 재실행해도 같은 값이어야 한다 |
| `source` | enum | O | 생산자 | `"arxiv"` \| `"news"`. MARA의 `ALLOWED_SOURCES`와 같은 값 (ADR-004) |
| `source_name` | string | O | 생산자 | `config.yaml`의 소스 이름. 예: `"GeekNews"`, `"arXiv cs.CL (Atom API)"` |
| `url` | string | O | 생산자 | **정규화된** 원문 URL (§4.1). 사람이 클릭하는 값이자 `doc_id`의 입력 |
| `title` | string | O | 생산자 | 원문 제목 그대로. 번역·요약하지 않는다 |
| `lang` | enum | O | 생산자 | `"ko"` \| `"en"`. **소스 선언값**이지 판정값이 아니다 (§3.4) |
| `text` | string | O | 생산자 | **색인·인용 단위.** `RawItem.body` 그대로, **최대 4,000자** (§6.1). 빈 문자열 허용 |
| `text_origin` | enum | O | 생산자 | v1에서는 `"source_text"`만 유효 (§6) |
| `text_chars` | int | O | 생산자 | `len(text)`. 임계값을 **측정으로** 정하기 위한 필드 (§6) |
| `text_truncated` | bool | O | 생산자 | 피드가 말줄임으로 잘라 보냈거나 **계약 상한 4,000자**에 걸린 경우 true (§6.1) |
| `locator` | string | O | 생산자 | `"abstract"`(arXiv) \| `"feed_excerpt"`(그 외). MARA의 인용 표기 `[doc_id / locator]`에 그대로 실린다 |
| `published_at` | date\|null | O | 생산자 | ISO `YYYY-MM-DD`. 피드에 없으면 `null` — **누락은 빈 문자열이 아니라 null이다** |
| `collected_at` | date | O | 생산자 | ISO `YYYY-MM-DD` |
| `ontology` | object | O | 생산자 | §3.2 |
| `provenance` | object | O | 생산자 | §3.3 |
| `indexable` | bool | O | 생산자 | §6의 규칙으로 계산. MARA는 이 값을 **재계산해서 대조**한다 |

### 3.2 `ontology` — 5필드 + 파생 텍스트

`extraction/schema.py: NewsOntology`의 영문 필드명을 쓴다. 한국어 alias는 Obsidian
frontmatter용이므로 계약에는 싣지 않는다 — 기계가 읽는 쪽에서 키가 한글일 이유가 없다.

| 필드 | 타입 | 필수 | 어휘 | MARA 소비 등급 (§8) |
|---|---|---|---|---|
| `tech_domains` | string[] (1~3) | O | 닫힘 14 | 메타데이터 (필터 미배선) |
| `release_type` | string | O | 닫힘 8 | **필터 1순위** |
| `companies` | object[] | O (빈 배열 허용) | 열림 | 메타데이터 (배선 보류) |
| `prior_art` | string[] (0~8) | O (빈 배열 허용) | **열림, 사전 없음** | 키워드 확장 전용, 필터 금지 |
| `impact_score` | int 1~5 | O | 순서형 | 정렬·리포팅 전용, **검색 필터 금지** |
| `impact_rationale` | string | O | 자유 텍스트 | **파생 텍스트 — 인용 금지** |
| `summary` | string | O | 자유 텍스트 | **파생 텍스트 — 색인·인용 금지** |

`companies[]` 원소:

| 필드 | 타입 | 규칙 |
|---|---|---|
| `canonical` | string\|null | `config.yaml: company_aliases`로 정규화된 대표명 |
| `raw` | string | 본문에 나타난 그대로의 표기. **반드시 보존한다** |
| `resolved` | bool | alias 매칭 성공 여부. `false`면 `canonical == raw` |
| `role` | string\|null | 해당 소식에서의 역할 |

> `canonical` / `resolved`는 LLM이 아니라 `normalize.py`가 채운다. 퍼지 매칭을 쓰지
> 않으므로 `resolved=false`인 롱테일이 항상 존재한다 (생산자 D-025/D-026).
> **MARA는 이 값을 보정하지 않는다** — 보정하면 사전의 단일 출처가 둘로 갈린다.

### 3.3 `provenance` — **선택 필드가 아니다**

온톨로지 5필드는 외부 벤더 모델(`claude-opus-5`)과 특정 프롬프트 버전의 **출력**이다.
이 값들이 없으면 코퍼스가 "언제 어느 모델로 만들어졌는지 모르는 것"이 되고,
**하네스 효과를 재려는 이 프로젝트의 전제(ADR-002 모델 고정)가 코퍼스 쪽에서
조용히 깨진다.** 그래서 누락은 경고가 아니라 실패다.

| 필드 | 타입 | 예시 |
|---|---|---|
| `extraction_model` | string | `"claude-opus-5"` |
| `prompt_version` | string | `"extract_ontology.v4.md"` |
| `prompt_sha256` | string | `"9ff5a9d39156b3d35a7b3ffa805261c78dfa262f390d051419cfaaa1711c5976"` |
| `extracted_at` | datetime | `"2026-09-17T14:03:11+09:00"` |
| `gate_model` | string\|null | `"claude-haiku-4-5-20251001"`. 게이트를 거치지 않았으면 null |
| `gate_prompt_version` | string\|null | `"relevance_gate.v1.md"` |
| `vocab_version` | string | `config.yaml: version` + `schema.py`의 Enum 해시 (§5) |

### 3.4 `lang`은 판정값이 아니다

`lang`은 `config.yaml`의 소스별 태그에서 온 **선언값**이다 (GeekNews는 `ko`, 나머지는
`en`). 항목 단위 언어 판정을 하지 않는 이유는 "하면 좋은데 안 한 것"이 아니라, 판정기를
넣는 순간 **그 판정기가 크로스링구얼 실험의 숨은 변수**가 되기 때문이다.

한국어 소스에 섞인 항목(한글 제목 + 영어 본문 등)이 `ko`로 선언되는 오차가 남는다.
**이 오차는 계약이 해결하지 않는다** — 크로스링구얼 대조군을 실제로 설계할 때
(MARA session-08 §7 (다)) 그 실험의 일부로 다룬다.

## 4. `doc_id` 생성 규칙

**이 절이 계약에서 가장 되돌리기 비싼 부분이다.** `doc_id`는 골든셋의
`expected_doc_ids`와 인용 표기에 그대로 박히므로, 규칙이 바뀌면 과거 측정값이 전부
무효가 된다. 규칙 변경은 `contract_version` major를 올린다.

### 4.1 URL 정규화 (`canonical_url`)

`doc_id`의 안정성은 곧 이 정규화의 안정성이다.

1. 스킴을 `https`로 고정
2. 호스트를 소문자화, 기본 포트(`:80`/`:443`) 제거
3. 쿼리에서 추적 파라미터 제거: `utm_*`, `ref`, `ref_src`, `fbclid`, `gclid`
4. 남은 쿼리 파라미터를 키 기준 사전순 정렬
5. 프래그먼트(`#...`) 제거
6. 경로 끝의 `/` 제거 (경로가 `/` 하나뿐인 경우는 유지)

### 4.2 ID 형식

| 조건 | 형식 | 예 |
|---|---|---|
| 호스트가 `arxiv.org` | `arXiv:<paper_id><version>` | `arXiv:2608.31100v1` |
| 그 외 | `news:<sha256(canonical_url)[:16]>` | `news:89dccc528c2f0457` |

- 해시는 **정규화된 URL 문자열의 UTF-8 바이트** 기준.
- 16자(64비트)를 쓰는 이유: 코퍼스가 10^4 규모여도 충돌 확률이 무시 가능하고,
  인용 표기 `[news:89dccc528c2f0457 / feed_excerpt]`가 한 줄에 들어간다.
  **충돌이 실제로 발생하면 그때 늘리는 것이 아니라 import가 에러로 멈춘다** (§7-2).

### 4.3 arXiv 특례 — 중복은 "나중에 거르는 것"이 아니라 "겹치게 만드는 것"

**두 레포가 둘 다 arXiv cs.CL을 수집한다.** MARA의 `scripts/ingest_corpus.py`는
`arXiv:2605.21404v1` 형식으로 `doc_id`를 만들고, 생산자도 같은 논문을 Atom 피드로 받는다.

> ⚠️ **위 동기 문장은 ADR-018 Amendment(2026-09-17)로 정정됐다 — 아래 규칙은 그대로
> 유효하다.** 실측하면 생산자는 arXiv **최근 피드**를, MARA는 **주제 질의 결과**를
> 수집해 **현재 자연 중복은 발생하지 않는다.** 이 규칙은 "이미 일어나는 중복을 흡수"가
> 아니라 **"겹쳤을 때 골든셋 `expected_doc_ids`·`min_citations` 판정이 조용히 깨지는 것을
> 예방"** 이다. 따라서 §12.1의 `--urls` 주입은 선택 기능이 아니라 **이 규칙을 검증할 수
> 있는 유일한 방법**이다 — 없으면 미검증 상태로 통과한 것처럼 보인다.

같은 논문이 `arxiv`와 `news` 두 출처로 각각 들어가면 —

- 검색 결과에 같은 내용이 두 번 올라와 `min_citations` 판정이 부풀고,
- 골든셋의 `expected_doc_ids`가 어느 쪽을 가리키는지 알 수 없어진다.

그래서 **arXiv 항목은 `source="arxiv"`로 내보내고 `doc_id`를 MARA의 기존 표기와
바이트 단위로 같게 만든다.** 그러면 중복이 "탐지해야 할 문제"가 아니라 **같은 키의
같은 문서**가 되어, import 시 자연스럽게 하나로 합쳐진다.

- 같은 `doc_id`가 스냅샷과 export 양쪽에 있으면 **온톨로지 메타데이터만 병합**하고
  `text`는 **기존 스냅샷 쪽을 유지**한다. arXiv API 초록(중앙값 1,339자)이 피드 발췌보다
  항상 길거나 같기 때문이다.
- **`url`은 정규화된 값(§4.1)으로 갱신한다.** 기존 스냅샷의 arXiv URL은 `http://`로
  저장돼 있어(`data/corpus/arxiv/*.json` 16건 전부) 그대로 두면 같은 문서의 링크 표기가
  두 가지로 갈린다. `doc_id`는 스킴과 무관하게 논문 ID에서 나오므로 **병합 자체는
  영향받지 않는다** — 갱신하는 것은 표기뿐이다.
- 병합했다는 사실을 import 로그에 남긴다. 조용히 합치면 코퍼스 규모 수치가 거짓이 된다.

## 5. 통제어휘 — 생산자가 정본, MARA는 검증만

manifest에 어휘 스냅샷을 싣는다:

```json
"vocab": {
  "vocab_version": "config=1;schema_sha256=<...>",
  "tech_domain": ["LLM", "Multimodal", "RAG", "..."],
  "release_type": ["Paper", "ProductLaunch", "OpenSource", "..."]
}
```

MARA의 판정 규칙 — **어휘 변화의 방향에 따라 다르게 다룬다** (생산자 D-021/D-022):

| 변화 | MARA 동작 | 왜 |
|---|---|---|
| 값이 **추가**됨 | 통과 (로그만) | 과거 라벨이 여전히 유효하다 |
| 값이 **사라짐/이름 변경** | **import 실패** | 이미 인덱싱된 문서의 라벨이 소급해서 틀린 것이 된다 |
| 값이 **쪼개짐** (`Agent` → `Agent/Tool`, `Agent/Multi`) | **import 실패** | 위와 같다. 재인덱싱 없이는 복구되지 않는다 |
| 레코드의 값이 manifest 어휘 밖 | **import 실패** | 생산자 쪽에서 스키마와 export가 갈렸다는 신호다 |

MARA는 어휘 목록을 코드에 복사하지 않는다. `ALLOWED_SOURCES`와는 성격이 다르다 —
저쪽은 **우리가 지켜야 할 정책**이라 코드에 박고(ADR-004), 이쪽은 **남의 레포가 정하는
사실**이라 받아서 대조한다.

## 6. 색인 정책 — **원문 텍스트만 색인한다**

```
indexable  ==  (text_origin == "source_text")  and  (text_chars >= MIN_TEXT_CHARS)
```

- **`summary`와 `impact_rationale`은 색인되지 않고 인용되지 않는다.** 둘 다 Opus 5가
  쓴 파생 텍스트다. ADR-004는 합성 코퍼스를 *"출처 자체가 지어낸 것이면 측정에 의미가
  없다"*는 이유로 기각했다. 파생 텍스트는 지어낸 것은 아니지만, 그것을 근거로 인용하면
  **인용이 원문이 아니라 모델의 패러프레이즈를 가리킨다.** 인용 정확도가 1순위 지표인
  시스템에서 이 구분을 흐리면 지표 자체가 무의미해진다.
- 그래서 `text_origin`은 v1에서 `"source_text"` 한 값만 유효하다. 필드를 **지금 두는**
  이유는, 나중에 요약 색인을 허용하기로 하더라도 그때 스키마를 바꾸는 것이 아니라
  **값 하나를 추가**하면 되게 하기 위해서다. 그리고 그 결정은 계약 변경이 아니라
  **ADR이 필요한 결정**이다.
- **`MIN_TEXT_CHARS`는 v1에서 `1`(비어 있지 않음)이다.** 더 높은 값이 옳을 수 있지만
  (뉴스 소스 본문 중앙값이 97~160자다) **근거 없이 정한 임계값을 또 만들지 않는다** —
  MARA에 `min_citations` / `top_k`가 이미 그 상태로 남아 있다 (session-08 §7).
  `text_chars`를 전 레코드에 싣는 이유가 이것이다: 분포를 먼저 보고 측정으로 정한다.

**실측 기준 소스별 예상 (생산자 D-013/D-014, 소스당 50건 표본의 본문 길이 중앙값):**

| 소스 | 중앙값 | 0자 항목 | v1 색인 |
|---|---|---|---|
| arXiv cs.CL (Atom) | 1,339자 | 없음 | 전량 |
| GeekNews (ko) | 160자 | 없음 (단 40/50 절단) | 전량 (`text_truncated=true` 다수) |
| OpenAI News | 149자 | — | 대부분 |
| Google DeepMind Blog | 97자 | 15/50 | 부분 |
| Hugging Face Blog | **0자** | 50/50 | **전량 제외** |

### 6.1 본문 길이 상한 — **4,000자 (계약 상수)**

```
len(text) <= 4000        # 초과분은 생산자가 자르고 text_truncated = true
text_chars == len(text)  # 자른 뒤의 값
```

- 이 값은 생산자의 `collectors/rss.py: BODY_MAX_CHARS = 4000`에서 왔지만, **계약은 그
  상수를 참조하는 것이 아니라 복사해서 고정한다.** 생산자가 자기 상수를 바꿔도 계약값은
  4,000이다. 참조였다면 남의 레포의 상수 변경이 우리 코퍼스의 문서 길이를 조용히 바꾼다.
- **상한을 계약에 박는 목적은 D-013을 미리 흡수하는 것이다.** 생산자의 열린 항목
  D-013(원문 페이지를 직접 fetch)이 나중에 실행되면 입력 텍스트가 피드 발췌에서 기사
  전문으로 바뀐다. 그때 **계약 형식은 바뀌지 않는다** — 상한과 `text_truncated`가 그
  변화를 흡수하므로 `contract_version`을 올릴 일도, export/import 코드를 고칠 일도 없다.
- ⚠️ **다만 상한이 흡수하는 것은 형식이지 판단이 아니다.** "기사 전문을 반입해도 되는가"는
  저작권·반입 범위 문제이고 **ADR-004 재검토 사안으로 그대로 남는다**(§10). 즉 D-013이
  실행되면 **계약은 재협상하지 않아도 되지만 ADR은 다시 봐야 한다.** 이 둘을 섞지 않는다.
- 상한값 자체를 바꾸는 것은 이미 인덱싱된 문서의 `text`가 달라지는 일이라 **재인덱싱이
  필요하고, `contract_version` major 인상 사안이다** (§9).

## 7. MARA가 import에서 강제하는 검증

조용히 통과시키면 코퍼스가 오염되고, 오염된 코퍼스 위의 측정치는 전부 재실행해야 한다.
그래서 전부 **예외**이지 경고가 아니다.

1. `contract_version` major 불일치 → 거부
2. **`doc_id` 중복** → 실패 (마지막 값으로 덮어쓰지 않는다. §4.3의 arXiv 병합은 이 규칙의 유일한 예외이고, 로그를 남긴다)
3. `release_type` / `tech_domains` 값이 manifest 어휘 밖 → 실패 (§5)
4. `indexable=true`인데 `text_origin != "source_text"` → 실패
5. `source`가 `ALLOWED_SOURCES` 밖 → `CorpusScopeError` (ADR-004 기존 경로 재사용)
6. `provenance` 필수 5키(`extraction_model`, `prompt_version`, `prompt_sha256`, `extracted_at`, `vocab_version`) 중 하나라도 누락 → 실패 (§3.3)
7. **`len(text) > 4000` 또는 `text_chars != len(text)`** → 실패 (§6.1). 상한 초과는 생산자가 자르지 않았다는 뜻이고, `text_chars` 불일치는 임계값을 정할 근거 자체가 틀렸다는 뜻이다 — 둘 다 조용히 통과시키면 나중에 잰 분포가 거짓이 된다

추가로 **`indexable`을 신뢰하지 않고 재계산해 대조한다.** 생산자가 계산한 값을 그대로
믿으면 두 레포의 규칙이 갈렸을 때 알아챌 수 없다. 불일치는 실패다.

## 8. 온톨로지 필드의 소비 등급

| 필드 | 카디널리티 | v1 소비 방식 | 근거 |
|---|---|---|---|
| `release_type` | 닫힘 8, 정확히 1 | 메타데이터 + **필터 후보 1순위** (배선은 Session 3, §8.2) | 등치 필터로 깨끗하고, `Paper`(근거급) 대 `Community/Discussion`(의견)을 가르는 축이라 인용 품질에 직결된다 |
| `tech_domains` | 닫힘 14, 1~3 | 메타데이터만 | MARA 골든셋 9건이 전부 멀티에이전트/LLM 에이전트 주제라 `Agent`/`Eval`로 수렴한다. 100건 규모에서 선택도가 없다 |
| `companies` | 열림 + 사전 8 | 메타데이터만 | 정확 엔티티 질의에 최고 정밀도지만 **현 골든셋에 기업명이 하나도 없다.** 수요가 생기면 배선한다 |
| `prior_art` | **열림, 사전 없음** | 키워드/질의 확장 전용 | 열거할 수 없는 어휘로는 필터를 만들 수 없다. 프롬프트 버전이 바뀌면 표기가 움직인다 |
| `impact_score` | 순서형 1~5 | 정렬·리포팅 전용 | `impact>=4` 필터는 **모델의 주관 점수로 근거를 버리는 것**이다. 인용 정확도가 1순위인 시스템에서 위험 방향이다 |

### 8.1 위 표는 **예측이다 — 측정으로 확정해야 할 항목**

> ⚠️ **5개 필드의 값 분포는 아직 측정된 적이 없다.** 생산자 레포에 대량 실행 산출물이
> 없고(`observability/logs/` 부재), 확정 골든셋은 3건이며 그 3건에서 발표유형 8개 중
> 3개만 쓰였다. 위 등급은 **구조적 카디널리티(닫힘/열림, 값 개수, 단일/다중)와 코퍼스
> 성격에서 나온 추론**이지 실측이 아니다. 실측처럼 인용하지 않는다.

**무엇으로 확정하는가** — 첫 export의 manifest `counts`가 유일한 1차 근거다.

| 확정할 것 | 확정 근거 | 기준 |
|---|---|---|
| `release_type`이 실제로 필터로 쓸모 있는가 | `counts.by_release_type` | 상위 1개 값이 전체의 **80%를 넘으면** 등치 필터의 선택도가 없다는 뜻이다 — 필터 후보에서 내린다 |
| `tech_domains`의 선택도가 정말 없는가 | `counts.by_tech_domain` | `Agent`/`Eval` 두 값이 전체의 **70% 미만**이면 예측이 틀린 것이고 배선 후보로 올린다 |
| `companies`에 수요가 있는가 | 골든셋 질의 | 기업명을 지목하는 질의가 **1건이라도** 추가되면 배선 후보다 |
| `impact_score`가 한 값에 몰려 있는가 | `counts` + 표본 검토 | 몰려 있으면 정렬 용도로도 쓸모가 없다 |
| `MIN_TEXT_CHARS` | 전 레코드 `text_chars` 분포 | §6 참고. **분포를 보기 전에 정하지 않는다** |

임계값(80% / 70%)을 **데이터를 보기 전에 여기 적어 두는 이유**는, 분포를 본 뒤에 기준을
정하면 어떤 결과가 나와도 그에 맞는 해석을 붙일 수 있기 때문이다.

### 8.2 필터 배선은 이 계약의 범위가 아니다

이 절은 **각 필드를 어떤 등급으로 취급하는가**만 정한다. 실제로 Chroma `where`에 필드를
넣는 작업은 **Session 3에서 단독으로** 한다. 계약 도입(Session 0.5)과 필터 배선을 같이
하면 코퍼스 구성과 검색 조건이 **한 번에 둘 다 바뀌어서**, 수치가 움직였을 때 원인을
가를 수 없다 — 이 프로젝트가 모델을 고정하는 이유(ADR-002)와 같은 논리다.

- **Session 0.5:** 온톨로지 필드를 `CorpusDoc` 메타데이터로 **싣기만 한다.** 검색 경로는
  건드리지 않는다. 이 단계의 대조 기준은 "코퍼스가 늘어난 것만으로 무엇이 달라졌는가"다.
- **Session 3:** `release_type` 필터를 배선하고 **그것 하나만** 바꿔 대조한다.

## 9. 버전 관리

| 변경 | 버전 |
|---|---|
| `doc_id` 규칙·URL 정규화 변경, 필수 필드 추가/삭제, `text_origin` 의미 변경, **`text` 길이 상한(4,000자) 변경** | **major** (`2.0`) — 재인덱싱 필요 |
| 선택 필드 추가, 어휘 **값 추가**, manifest 항목 추가 | minor (`1.1`) |
| 문구·설명 수정 | 버전 유지 |

MARA는 major가 다르면 거부하고, minor가 높으면 **모르는 필드를 무시하고 진행**한다.

## 10. 이 계약이 답하지 않는 것

- **본문 fetch를 해도 되는가.** 생산자의 열린 항목 D-013(원문 페이지를 직접 가져오기)이
  실행되면 피드 발췌가 아니라 기사 전문이 반입된다. **계약 쪽은 준비돼 있다** — 4,000자
  상한과 `text_truncated`가 형식 변화를 흡수하므로 `contract_version`을 올릴 일이 없다
  (§6.1). 하지만 **저작권·반입 범위 판단은 그대로 남는다.** 이것은 계약이 아니라
  **ADR-004 재검토 사안**이다.
- **요약 색인.** §6 참고. 허용하려면 ADR이 필요하다.
- **필터 배선.** §8.2 — 이 계약은 필드의 소비 등급만 정하고, 배선은 Session 3 몫이다.
- **증분 export / 삭제.** v1은 전량 스냅샷이다. 원문이 사라진 기사를 코퍼스에서 빼는
  경로가 없다.
- **`MIN_TEXT_CHARS`의 값.** 측정 대상이다 (§6).
- **값 분포.** 미측정이다 (§8).

## 11. 예시

### 11.1 arXiv (`data/corpus/arxiv/ontology-20260917-140311.jsonl`)

```json
{"contract_version":"1.0","doc_id":"arXiv:2608.31100v1","source":"arxiv","source_name":"arXiv cs.CL (Atom API)","url":"https://arxiv.org/abs/2608.31100v1","title":"S3Gym: Can LLMs Turn Self-Testing and Self-Judging into Self-Improvement?","lang":"en","text":"Large language models (LLMs) increasingly interact with external environments and accumulate substantial behavioral experience, yet existing agent benchmarks largely evaluate them as fixed policies. ...","text_origin":"source_text","text_chars":1339,"text_truncated":false,"locator":"abstract","published_at":"2026-08-31","collected_at":"2026-08-31","indexable":true,"ontology":{"tech_domains":["Agent","Eval/Governance"],"release_type":"Paper","companies":[],"prior_art":["In-Context Learning","LLM Agent","Text-based Games","Held-out Evaluation","Negative Transfer"],"impact_score":3,"impact_rationale":"LLM 에이전트 자기개선을 다루는 벤치마크 논문으로 ...","summary":"S3Gym은 자기테스트·자기판단·자기개선 세 능력을 ..."},"provenance":{"extraction_model":"claude-opus-5","prompt_version":"extract_ontology.v4.md","prompt_sha256":"9ff5a9d39156b3d35a7b3ffa805261c78dfa262f390d051419cfaaa1711c5976","extracted_at":"2026-09-17T14:03:11+09:00","gate_model":null,"gate_prompt_version":null,"vocab_version":"config=1;schema_sha256=..."}}
```

### 11.2 GeekNews (`data/corpus/news/ontology-20260917-140311.jsonl`)

```json
{"contract_version":"1.0","doc_id":"news:89dccc528c2f0457","source":"news","source_name":"GeekNews","url":"https://news.hada.io/topic?id=33003","title":"OpenAI가 Cursor 개발사와 ...","lang":"ko","text":"OpenAI가 ... (피드 발췌, 말줄임으로 잘림)","text_origin":"source_text","text_chars":163,"text_truncated":true,"locator":"feed_excerpt","published_at":"2026-08-29","collected_at":"2026-08-29","indexable":true,"ontology":{"tech_domains":["Application/Product"],"release_type":"Partnership/Contract","companies":[{"canonical":"OpenAI","raw":"오픈AI","resolved":true,"role":"발표 주체"}],"prior_art":["Vibe Coding"],"impact_score":3,"impact_rationale":"...","summary":"..."},"provenance":{"extraction_model":"claude-opus-5","prompt_version":"extract_ontology.v4.md","prompt_sha256":"9ff5a9d39156b3d35a7b3ffa805261c78dfa262f390d051419cfaaa1711c5976","extracted_at":"2026-09-17T14:03:11+09:00","gate_model":"claude-haiku-4-5-20251001","gate_prompt_version":"relevance_gate.v1.md","vocab_version":"config=1;schema_sha256=..."}}
```

### 11.3 manifest

```json
{
  "contract_version": "1.0",
  "export_id": "ontology-20260917-140311",
  "exported_at": "2026-09-17T14:03:11+09:00",
  "exporter": "ai-news-ontology@<git sha>",
  "license_note": "공개 RSS/Atom 피드가 제공한 메타데이터와 발췌만 저장한다. 기사·논문 전문은 보관하지 않는다.",
  "counts": {
    "total": 0, "indexable": 0, "excluded_empty_text": 0,
    "by_source": {}, "by_source_name": {},
    "by_release_type": {}, "by_tech_domain": {},
    "companies_unresolved": 0
  },
  "vocab": { "vocab_version": "...", "tech_domain": [], "release_type": [] }
}
```

## 12. 적합성 검증 (conformance) — 1단계 30건

**계약이 실제로 흐르는지는 표본 30건으로 판정한다.** 스키마·형식 결함은 표본 수에
비례해서 드러나지 않는다 — 30건에서 안 걸리는 형식 결함은 250건에서도 대체로 안 걸리고,
반대로 250건을 다 쓴 뒤에 결함을 찾으면 **비용을 두 번 낸다.** 그래서 코퍼스 확보는
이 검증을 통과한 뒤에 한다.

### 12.1 표본 구성 (30건)

| 건수 | 대상 | 이 표본이 검증하는 것 |
|---:|---|---|
| **2** | **MARA 코퍼스에 이미 있는 arXiv 문서** — `arXiv:2412.05449v1`(GS-001의 `expected_doc_ids`), `arXiv:2605.21404v1` | §4.3 `doc_id` 동일화·병합. 전자는 **골든셋 기대값이 병합 후에도 살아 있는지**까지 본다 |
| 8 | arXiv cs.CL 피드 신규 | 긴 본문(`locator="abstract"`), `text_truncated=false` 경로 |
| 10 | GeekNews | 한국어 레코드, `text_truncated=true` 다수, `lang="ko"` |
| 5 | OpenAI News / Google DeepMind Blog | 짧은 본문(97~149자), 일부 0자 |
| 5 | Hugging Face Blog | **본문 0자 → `indexable=false`** 제외 경로 |

> ⚠️ **중복 2건은 "피드에 우연히 섞이길" 기대하면 안 된다.** 생산자는 arXiv **최근**
> 피드를 읽고 MARA의 16건은 **특정 주제 질의 결과**라, 겹칠 이유가 구조적으로 없다.
> 위 2건은 URL을 **명시적으로 입력에 넣어야** 한다 — 생산자 쪽에 URL 직접 입력 경로가
> 없으므로 export 스크립트에 `--urls` 입력을 만드는 것이 Session 0.5 구현 항목이다.
> 이것이 없으면 `doc_id` 동일화는 **검증되지 않은 채 통과한 것처럼 보인다.**

### 12.2 통과 기준 — **전 항목 통과해야 2단계로 넘어간다**

| # | 검증 항목 | 판정 기준 (통과) |
|---|---|---|
| 1 | **e2e 흐름** | export → JSONL 30줄 + manifest 1개 → MARA 로더 적재까지 **예외 없이** 완주 |
| 2 | **`provenance` 5키** | 30줄 **전부**에 `extraction_model`·`prompt_version`·`prompt_sha256`·`extracted_at`·`vocab_version`이 있고 빈 문자열이 아니다. 1줄이라도 누락이면 실패 |
| 3 | **`prompt_sha256` 실측 일치** | 값이 생산자 프롬프트 파일의 실제 sha256과 같다. 하드코딩된 자리표시자가 아니어야 한다 |
| 4 | **`text_origin`** | 30줄 전부 `"source_text"`. 다른 값이 하나라도 있으면 실패 |
| 5 | **arXiv 중복 병합** | 지정한 2건이 **새 `doc_id`를 만들지 않고** 기존 문서와 합쳐진다. import 후 코퍼스의 arXiv 문서 수가 **16 + (신규 arXiv 건수)** 와 정확히 같다 |
| 6 | **골든셋 보존** | 병합 후 `arXiv:2412.05449v1`이 그대로 조회되고, `docs/eval/golden-set.json`의 `expected_doc_ids`가 **한 건도 깨지지 않는다** |
| 7 | **`doc_id` 결정성** | 같은 입력으로 export를 **2회** 돌려 `doc_id` 집합이 바이트 단위로 같다 |
| 8 | **`text` 상한·`text_chars`** | 전 줄에서 `len(text) <= 4000` 이고 `text_chars == len(text)` (§6.1) |
| 9 | **`indexable` 재계산 일치** | MARA가 재계산한 값이 생산자 값과 30줄 전부 일치. HF Blog 5건이 `false`로 떨어진다 |
| 10 | **통제어휘 대조** | 레코드의 `release_type`·`tech_domains` 값이 manifest `vocab` 안에 전부 들어 있다 (§5) |
| 11 | **`published_at` null 처리** | 피드가 발행일을 주지 않은 항목이 빈 문자열이 아니라 `null`이다 |
| 12 | **manifest 합계** | `counts.total == 30`, `counts.indexable + counts.excluded_empty_text == 30` |
| 13 | **한국어 레코드 색인** | GeekNews 레코드가 실제로 인덱스에 들어가고 `lang="ko"`로 조회된다 |

**하나라도 실패하면 2단계로 넘어가지 않는다.** 고치고 1단계를 다시 돌린다.

### 12.3 재실행 비용을 두 번 내지 않기 위한 조건

1단계가 실패했을 때 **추출을 다시 호출하면 30건 비용을 또 낸다.** 생산자 파이프라인은
`NewsOntology`를 Obsidian 노트 외에 어디에도 보존하지 않으므로, 형식만 틀렸는데
LLM을 다시 부르게 된다.

→ **1단계 실행 시 추출 결과 원본을 로컬에 보존한다.** 형식 결함은 보존된 결과에서
재-export만 하면 되고, LLM 재호출은 **추출 로직 자체가 바뀐 경우에만** 한다.

> 이 보존 산출물에는 프롬프트와 LLM 응답 본문이 평문으로 남는다. 투입 데이터가 공개
> 자료뿐이라는 ADR-004 전제 하에서 `docs/governance.md`의 **로컬 산출물 (가) 기준**
> (보존기간 미정·별도 접근통제 없음·필요 없어지면 수동 삭제)을 그대로 따른다.

---

## 변경 이력

| 날짜 | 버전 | 내용 |
|---|---|---|
| 2026-09-17 | 1.0 (Draft) | session-09 최초 작성. ADR-018 |
| 2026-09-17 | 1.0 (Draft) | §6.1 본문 길이 상한을 규범 규칙으로 승격, §8.1 측정 확정 절차·§8.2 배선 범위 경계 추가, §12 적합성 검증 체크리스트 추가 |
| 2026-09-17 | 1.0 (버전 유지) | §4.3에 ADR-018 Amendment 참조 추가 — 동기 문장만 정정, 규칙 서술·형식 변경 없음 (§9의 "문구·설명 수정") |
| 2026-09-17 | 1.0 (버전 유지) | session-10: §2 manifest 위치 정정(생산자 실제 출력에 맞춤, 로더는 두 위치 모두 탐색) + manifest 보존 규칙 명시. 형식·필드·`doc_id` 규칙 변경 없음 (§9의 "문구·설명 수정") |
| 2026-09-17 | — | session-10: §12.2 판정을 **MARA 로더로** 다시 내렸다 — **12 PASS / 0 FAIL / 1 PENDING**(3번 `prompt_sha256` 실측은 생산자 레포에서만 가능). 계약 문서 자체는 바뀌지 않았다 |
