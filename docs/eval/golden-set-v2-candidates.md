# 골든셋 v2.0 신규 21건 후보 — 검토용 (기대 근거 미확정)

- **작성:** session-13 / 2026-09-18
- **상태:** ⚠️ **후보다.** `기대 문서`는 제안이며, **사용자가 원문을 읽고 확정하기 전까지
  `golden-set.json`에 반영하지 않는다.** LLM 판정 없음 — 코퍼스 37건의 원문(초록·요약)을
  직접 읽고 작성했다 (v1.0 방식 유지, `golden-set.json` seed 주석 참조).
- **총계:** 기존 9건 + 신규 21건 = **30건** (positive 23 / negative 7)
- **층 (ADR-022):** A=필터 적격 14 · B=메타 결측 9 · C=negative 7

## ⚠️ 원문 확인 시 특히 봐주실 5곳 (판정이 갈릴 수 있는 지점)

| # | 케이스 | 무엇이 애매한가 |
|---|---|---|
| 1 | **GS-017** vs 기존 GS-004 | 둘 다 "정렬". ARCANE=해석 가능 루브릭·**재학습 없음**, ComPO=**0차 최적화**(학습은 한다). 이 구분이 질의 문면에서 성립하는지 |
| 2 | **GS-025** | AgentLAB(장기 다중 턴 공격) vs AgentHarm(`2410.09024`, 유해 과제 거부) vs 3CB(`2410.09114`, 사이버 공격 역량). 셋 다 "에이전트 안전 벤치마크"다 |
| 3 | **GS-028** (negative) | `2602.03128`이 지연·처리량을 말한다. "추론 서버 설정값 측정"과 층위가 다르다고 봤는데, 근거 있음으로 읽힐 여지 |
| 4 | **GS-023** | `f3899af`(오정렬 **공개 체계**) vs `2609.19101`(오정렬 **탐지 방법**). 질의가 전자만 가리키는지 |
| 5 | **GS-014** | `news:42f467`은 **본문이 "연산 능력보다 메…"에서 절단**돼 있다. "메모리 대역폭"은 인덱스에 없어 질의를 절단 이전 문장으로 좁혔다 |

> **본문 절단 일반 주의:** news 13건 중 **8건이 `text_truncated: true`**다
> (`1047cd`·`3dd7208`·`42f467`·`509053`·`587009`·`c076df`·`c45b89`·`dfff96`).
> news를 앵커로 쓰는 케이스는 **절단된 텍스트만으로 답할 수 있는 질의**로 제한했다.

---

## (a) tech_domain으로 갈리는 케이스 — 6건

**설계 조건:** 기대 문서는 반드시 메타 보유 문서. 필터 적용 후에도 **한국어 단문이 남아야**
한다 — 남지 않으면 `release_type` 필터와 같은 "오염 일괄 제거"가 되어 판정이 안 된다.

| ID | 질의 (ko) | 제안 기대 문서 | 겨냥 필드 | 필터 후 잔존 ko | 노리는 방해문서 |
|---|---|---|---|---:|---|
| **GS-010** | 두 언어모델이 서로 질문과 답을 주고받으며 후보를 좁혀가는 능력을 측정한 연구가 있는가? | `arXiv:2609.19113v1` | `tech_domains=Reasoning` (2/23 = 8.7%) | **1** (`news:3dd7208e`) | `news:0252e9`(세션 간 질의응답 브로커) — 어휘 정면 충돌, `Agent\|Application/Product`라 탈락 |
| **GS-011** | 모델이 보상을 속이는 행동을 내부 표현으로 탐지할 수 있다는 연구가 있는가? | `arXiv:2609.19101v1` | `Safety/Alignment` (5/23) | **2** (`1047cd`,`509053`) | `news:509053`(DeepSeek "해킹" 벤치마크) — 어휘 함정 "해킹"인데 **필터를 통과한다** |
| **GS-012** | 토크나이저의 최적화 목표와 탐색 절차 중 어느 쪽이 언어모델 성능을 좌우하는가? | `arXiv:2609.19145v1` | `Training/Finetuning` (5/23) | **2** (`587009`,`dfff96`) | `news:dfff96`(사후 학습 대시보드) |
| **GS-013** | 시각-언어 모델을 교육 맥락의 예술 이미지 이해로 평가한 벤치마크가 있는가? | `arXiv:2609.19088v1` | `Multimodal` (3/23) | **0** ⚠️ | — (**선택도 상한 arm**. 여기선 필터가 ko를 전부 걷어낸다 = `release_type`과 같은 성질이 되는 지점) |
| **GS-014** | AI 하드웨어의 초점이 대규모 학습에서 추론 수요 대응으로 옮겨가고 있다는 설명이 있는가? | `news:42f467857601213c` | `Inference/Serving`·`Hardware/Chip` (각 1/23) | 기대 문서 자신 | — (**한국어 단문이 오염원이 아니라 정답인 케이스.** `release_type=Community/Discussion`이라 Paper 필터에선 탈락 → (d)와 겹침) |
| **GS-015** | LLM 에이전트 벤치마크 논문들이 평가 실행 조건을 얼마나 공개하는지 조사한 것이 있는가? | `arXiv:2605.21404v1` | `Eval/Governance` (13/23 = 56.5%) | 2 | — (**선택도 바닥 arm.** 걸어도 절반이 남는다) |

**근거문 (원문 발췌):**

- GS-010 — "A questioner sees N Wikipedia lead paragraphs and must identify a secretly chosen target using exactly log2 N yes/no questions. An answerer sees only the target and the question, and replies with one word."
- GS-011 — "simple difference of means vectors coherently represent reward hacking in Kimi K3, GLM 5.2, and Qwen 3.8 Max ... we can use them to reliably detect reward hacking"
- GS-012 — "the search procedure -- not the objective -- is the dominant factor: bottom-up tokenisers consistently achieve lower bits-per-byte in most settings"
- GS-013 — "we introduce MUSE, a benchmark for evaluating large vision-language models on artistic image understanding in situated educational applications"
- GS-014 — "LLM 활용 증가에 추론형 모델과 AI 에이전트가 더해지면서, AI 하드웨어의 중심이 대규모 학습에서 급증하는 추론 수요 대응으로 이동하고 있음"
- GS-015 — "We designed a small audit schema (five fields: benchmark identity, harness specification, inference settings, cost reporting, failure breakdown) ... The mean audit score across the eight agent-benchmark papers is 0.38 (out of 1.0)"

---

## (b) 한국어 질의 + 영어 정답 문서 + **한국어 근접 방해문서** — 6건

**v1.0과 무엇이 다른가:** 기존 7건도 전부 ko 질의 + en 문서다. 새로운 것은
**주제·어휘가 겹치는 한국어 단문이 코퍼스에 실재하는** 케이스만 골랐다는 점이다.
session-12 §3.1의 언어 효과(길이 통제 시 ko단문 0.8000 vs en단문 0.7416)가
**정답을 실제로 밀어내는지**를 보는 것이 목적이다.

| ID | 질의 (ko) | 제안 기대 문서 | 층 | ko 방해문서 (어휘 겹침) |
|---|---|---|---|---|
| **GS-016** | 과학 코드 저장소를 에이전트가 학습할 수 있는 실행 환경으로 바꾸는 접근이 있는가? | `arXiv:2609.19134v1` | A | `0252e9`(레포·에이전트), `c076df`(프로그래밍 학습) |
| **GS-017** | 선호 쌍의 차이가 작을 때 기울기 계산 없이 비교만으로 모델을 정렬하는 방법이 있는가? | `arXiv:2609.19144v1` | A | `587009`, `dfff96` — ⚠️ **GS-004(ARCANE)와 구분 확인 필요** |
| **GS-018** | 방사선 판독문 생성 모델의 평가 점수가 참조 보고서의 작성 방식에 따라 흔들린다는 연구가 있는가? | `arXiv:2609.19093v1` | A | `509053`, `dfff96`(벤치마크·평가 어휘) |
| **GS-019** | 작은 모델을 계획·호출·요약 역할로 나눠 도구 사용 성능을 끌어올린 연구가 있는가? | `arXiv:2401.07324v3` | **B** | ★ `587009` "**4B 소형 모델**을 학습시켜" — 어휘 함정 |
| **GS-020** | 에이전트가 병렬로 진행되거나 중단되는 장기 과제를 비동기적으로 계획하는 능력을 측정한 벤치마크가 있는가? | `arXiv:2502.05227v1` | **B** | ★★ `3dd7208` "어느 시도를 이어가고, **병렬로 실행하거나 중단**할지 정하는 전략" — 거의 동일 어휘 |
| **GS-021** | 이미지 캡션의 각 구절을 픽셀 마스크에 연결하는 문제를 다룬 연구가 있는가? | `arXiv:2609.19143v1` | A | ko 약함. 대신 `2609.19088`(같은 `Multimodal`)과의 **필터 내부 변별** 테스트 |

**근거문 (원문 발췌):**

- GS-016 — "We introduce ScienceIDE, infrastructure for turning the world's scientific code into programmable environments for scientific agents."
- GS-017 — "we propose and analyze Comparison-based Preference Optimization (ComPO), a zeroth-order alignment method based on comparison oracles. ComPO extracts directional information from these pairs without directly optimizing a differentiable preference loss on them."
- GS-018 — "we quantify the sensitivity of established evaluation metrics to variations in reporting practices, revealing impacts large enough to alter the rankings of models ... causes Libra to drop from first to second place while CheXOne rises from third to first"
- GS-019 — "we propose a novel approach that decomposes the aforementioned capabilities into a planner, caller, and summarizer. Each component is implemented by a single LLM that focuses on a specific capability"
- GS-020 — "We introduce Robotouille, a challenging benchmark environment designed to test LLM agents' ability to handle long-horizon asynchronous scenarios ... ReAct (gpt4-o) achieves 47% on synchronous tasks but only 11% on asynchronous tasks"
- GS-021 — "we formulate phrase grounding as selection from a phrase-conditioned pool of mask proposals and introduce PANORAMA, a VLM that conditions a pretrained segmenter on contextualized phrase representations to obtain candidate masks"

---

## (d) release_type이 근거 등급을 가르는 케이스 — 4건

| ID | 질의 (ko) | 제안 기대 문서 | `release_type` | 층 | 이 케이스가 보는 것 |
|---|---|---|---|---|---|
| **GS-022** | 코딩 에이전트의 탐색 전략을 과거 시도 기록 위에서 시험해 개선하는 프레임워크가 있는가? | `news:3dd7208e3f2a1dde` | **Paper** | A | **`source=news`인데 `Paper` 등급이다.** 출처로 근거 등급을 대신하면 틀린다 — 코퍼스 유일 사례 |
| **GS-023** | 모델의 오정렬 사례를 추적·조사·공개하는 운영 체계를 제시한 곳이 있는가? | `news:f3899afd4d6a0a2d` | Benchmark/Report | A | Paper 필터를 걸면 **정답이 사라진다.** en 단문(§3.1 밀어내기 0회 군)이라 무필터에선 순위가 낮을 것 — 필터가 **올려주는** 효과를 보는 유일한 케이스 |
| **GS-024** | 광고를 클릭하면 기업이 후원하는 에이전트와 대화하는 기능이 시험되고 있는가? | `news:14d2c76967ca2b60` | ProductLaunch | A | 제품 발표는 근거급이 아니다. Paper 필터에서 **정당하게** 빠지는 대조 |
| **GS-025** | 여러 턴에 걸친 상호작용을 악용하는 공격에 LLM 에이전트가 얼마나 취약한지 측정한 벤치마크가 있는가? | `arXiv:2602.16901v1` | **(공백)** | **B** | **진짜 arXiv 논문인데 Paper 필터에서 탈락한다.** ADR-022 층 B의 대표 케이스 — ⚠️ AgentHarm·3CB와 변별 확인 필요 |

**근거문 (원문 발췌):**

- GS-022 — "과거 시도와 결과를 저장해 다른 탐색 전략을 기록 위에서 시험하므로, 전략을 평가할 때마다 코드를 새로 생성하고 실행하는 비용…" *(절단)*
- GS-023 — "OpenAI shares a framework for tracking, investigating, and disclosing model misalignment, alongside six reports of unexpected or concerning model behavior."
- GS-024 — "OpenAI가 ChatGPT 광고를 클릭하면 기업이 후원하는 에이전트와 대화할 수 있는 Sponsored Agents를 미국 일부 광고주와 테스트 중임"
- GS-025 — "we present AgentLAB, the first benchmark dedicated to evaluating LLM agent susceptibility to adaptive, long-horizon attacks ... five novel attack types including intent hijacking, tool chaining, task injection, objective drifting, and memory poisoning"

---

## (c) negative — 5건 (기존 2건 → 총 7건)

**설계 원칙:** GS-008(완전 무관)은 이미 있다. 신규 5건은 **주제가 가까운데 답은 없는**
근접 negative로 채운다 — 간격 측정의 표본이자 "비슷해 보이는 문서를 물어오는가"의 시험이다.

| ID | 질의 (ko) | 끌려올 문서 (의도) | 왜 근거 없음인가 |
|---|---|---|---|
| **GS-026** | 국내 기업이 사내 문서로 한국어 LLM을 파인튜닝해 얻은 성능 수치가 있는가? | ko 단문 전반 (**언어 견인 최대**) | `dfff96`는 Xiaomi(중국), `587009`는 4B SQL 모델. **한국어 LLM도 사내 문서도 코퍼스에 없다** |
| **GS-027** | LLM 에이전트를 실제 공장 설비에 붙였을 때 발생한 안전 사고가 보고돼 있는가? | `2405.16887`(physical shopfloor), `2406.01893` | 두 논문 모두 **적용과 성능(makespan)**만 보고한다. 안전 사고 기술 없음 |
| **GS-028** | 추론 서버의 배치 크기나 KV 캐시 설정을 바꿔 처리량을 측정한 수치가 있는가? | ★ `42f467`(상습 밀어내기 문서) | `42f467`은 절단된 경향 서술뿐, 설정값·측정치 없음. ⚠️ `2602.03128`의 지연·처리량은 **프레임워크 아키텍처** 층위 — 판정 확인 필요 |
| **GS-029** | LLM 에이전트가 임상시험에서 전문의의 진단 정확도를 넘었다는 결과가 있는가? | `2609.19093`(흉부 X선) | 판독문 **평가 지표**의 민감도 연구다. 임상시험도, 전문의 대비 진단 정확도도 없다 |
| **GS-030** | 멀티에이전트 시스템 운영의 전력 소비량이나 탄소 배출량을 측정한 연구가 있는가? | `42f467`, `2602.03128` | 지연·처리량·비용 미공개 서술은 있으나 **전력·탄소 수치는 어디에도 없다** |

---

## 앵커 사용 현황

- **신규 앵커 16건** (positive 16건 × 1건씩): 메타 보유 13 · 결측 3
- **누적 앵커 24/37 = 64.9%.** 나머지 **13건은 순수 방해문서로 남는다**:
  `2406.00215`, `2410.09024`, `2410.09114`, `2512.02230`,
  `0252e9`, `1047cd`, `509053`, `587009`, `c076df`, `c45b89`, `dfff96`, `4410122`, `7d28727`
- **문서당 질의 ≈ 0.65.** "1개 가까이"라는 제약 안이며, negative 7건의 배경 문서도 확보된다.
