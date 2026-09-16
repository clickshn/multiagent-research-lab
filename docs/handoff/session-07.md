# Session 07 핸드오프 — 실제 배포 · 승인 하네스 · EFS 결정

- **날짜:** 2026-09-16
- **범위:** AWS CLI/Terraform 설치, 첫 `terraform validate`, 승인 하네스(deny + PreToolUse 훅),
  `var/` 저장소 EFS 결정, Langfuse 배포 범위 제외, 리전 확정, **실제 `apply`**
- **신규 ADR:** ADR-015(EFS) / ADR-016(Langfuse 로컬 전용) + **ADR-013에 Amendment**
- **테스트:** 114건 전부 통과 (변경 없음 — 이번 세션은 인프라·문서 작업이다).
  별도로 **승인 훅 게이트 검사 20건** 추가 (스크래치패드, 레포에 커밋 안 함).
- **⚠️ 이번 세션은 실제 유료 리소스를 만들었다.** 아래 §4 참조.

---

## 이번 세션의 핵심

세 가지가 드러났고, 셋 다 **"코드를 읽는 것과 돌려 보는 것이 다르다"**는 세션 6의 교훈이
반복된 사례다.

1. **세션 6의 HCL은 문법조차 검증된 적이 없었다.** CLI를 설치하고 처음 `validate`를 돌렸다.
2. **내가 세운 승인 게이트에 구멍이 있었다.** 훅을 등록한 직후 PowerShell 도구로 `aws`가
   그대로 실행됐다 — 매처가 `Bash`뿐이었다.
3. **`terraform validate`가 통과해도 `apply`는 실패한다.** IAM role description의 한글이
   서버 측 검증에서 걸렸다. validate는 스키마만 본다.

---

## 1. 도구 설치 + 첫 `terraform validate`

| | 결과 |
| --- | --- |
| Terraform | **v1.16.2** (`~> 1.9` 충족) — `Hashicorp.Terraform` |
| AWS CLI | **v2.36.46** — `Amazon.AWSCLI` |

⚠️ **winget 패키지 ID는 대소문자를 구분한다.** `HashiCorp.Terraform`(공식 표기)으로는
`-e` 정확 일치가 실패한다(exit 20). 실제 ID는 **`Hashicorp.Terraform`**이다.

⚠️ **winget이 PATH를 갱신해도 이미 떠 있는 셸에는 반영되지 않는다.** 이 세션 내내
`export PATH="$PATH:/c/Users/USER/AppData/Local/Microsoft/WinGet/Packages/Hashicorp.Terraform_*"`
를 붙여야 했다. **새 터미널을 열면 필요 없다.**

### 검증 결과

| 검사 | 결과 |
| --- | --- |
| `terraform fmt -recursive` | `outputs.tf` 1곳 정렬 수정 |
| `terraform init` | hashicorp/aws **v5.100.0** |
| `terraform validate` | **통과** (세션 6 HCL + 이번 세션 추가분 전부) |
| `terraform plan` | **27 to add, 0 to change, 0 to destroy** |

세션 6이 "문법은 검토했지만 검증되지 않았다"고 남긴 항목이 해소됐다.

---

## 2. 승인 하네스 (`.claude/`)

### 구성

- `.claude/settings.json` — `permissions.deny` 4개 + `PreToolUse` 훅 등록
- `.claude/hooks/check-deploy-approval.sh` — 승인 파일 없으면 exit 2로 도구 호출 차단
- `.claude/deploy-approved`는 이미 `.gitignore`에 있었다 (세션 1, 확인 완료)

### 지시에서 벗어난 것 두 가지 — 둘 다 게이트가 실제로는 작동하지 않아서다

**(가) `jq` → python 폴백을 넣었다.**
지시받은 스크립트는 `jq`로 JSON을 판다. **이 머신에 `jq`가 없다.** 훅은 모든 Bash 호출마다
도는데, 파서가 없으면 **Bash 전체가 막힌다** — 실제로 등록 직후 그렇게 됐다.
`jq` → `python3`/`python`/`py` 순으로 시도하고, **셋 다 없으면 통과가 아니라 차단**이다.
python은 이 프로젝트의 하드 의존성이라 항상 있다.

**(나) `PowerShell` 매처를 추가했다 — 이게 더 중요하다.**
훅을 `Bash` 매처로만 걸고 나서 `aws sts get-caller-identity`를 **PowerShell 도구로 실행했더니
그냥 됐다.** `deny`의 `Bash(aws:*)`도 PowerShell 도구에는 걸리지 않는다.

> **게이트를 세운 직후에 그 게이트를 우회하는 경로가 남아 있었다.**
> 세션 6의 `redacted()`와 같은 구조다 — 규칙을 집행하는 장치가 규칙을 만족하는지는
> 따로 확인해야 한다. 이번에는 **실제로 우회를 시도해 봐서** 드러났다.

`deny`에 `PowerShell(...)` 2개를 추가하고 매처를 `Bash|PowerShell`로 바꿨다.
같은 명령이 이제 차단되는 것을 확인했다.

### 게이트 검사 20건 — 전부 통과

차단(exit 2)되어야 하는 것 9 / 통과(exit 0)해야 하는 것 8 / 승인 파일 있을 때 통과 2 / 정리 1.

오탐 검사를 일부러 넣었다: `pip install awscli-local`, `grep awsx README.md`는 **통과해야 한다.**
`aws`를 단어 경계로 잡기 때문이다.

**`apply.sh apply`/`destroy`도 훅이 잡도록 추가했다.** `apply.sh`가 자체 게이트를 갖고 있지만,
도구 계층이 **정상 경로만 못 보는** 상태가 되면 게이트의 적용 범위를 사람이 머릿속으로
따라가야 한다.

### ⚠️ 자기참조 문제 — 알고 있어야 한다

**훅은 명령 문자열을 검사하므로, 트리거 문자열이 들어간 명령은 그 자체가 차단된다.**
이번 세션에서 세 번 걸렸다:

- `settings.json`을 heredoc으로 쓰려는데 내용에 `terraform apply`가 있어서 차단
- `outputs.tf`에 `aws ecs run-task` 예시를 append하려다 차단
- 게이트 검사 스크립트를 인라인으로 돌리려다 차단

**회피가 아니라 우회 없는 해법을 썼다**: 그런 파일은 Write/Edit 도구로 쓰고, 검사는
스크립트 파일로 만들어 `bash <파일>`로 돌린다(명령줄에 트리거 문자열이 안 들어간다).
governance의 "검사 명령에 URL 조각을 직접 적지 않는다"와 같은 종류의 문제다.

### ⚠️ `deny`는 승인 파일로 풀리지 않는다 — 사용자 결정

`permissions.deny`는 **하드 차단**이다. `.claude/deploy-approved`가 있어도, 사용자가
대화형으로 승인해도 풀리지 않는다. 실제로 확인했다 (`aws` → "Permission to use Bash
with command aws has been denied", 훅 메시지와 다른 계층의 메시지다).

**사용자 결정: 그대로 둔다 — `aws` 명령은 사람이 실행한다.**
따라서 ECR 푸시 · SSM 등록 · `run-task` · 로그 조회는 **에이전트가 대신 할 수 없다.**
`terraform`은 `./apply.sh`를 경유하면 실행 가능하다(`deny` 패턴이 `terraform apply`로
시작하는 명령만 잡으므로). 이것은 구멍이 아니라 `.claude/rules/iac.md`가 요구하는
"직접 `terraform apply`를 치지 말 것"을 도구 계층에서 강제하는 형태다.

---

## 3. 결정 세 가지

### ADR-015 — `var/`는 EFS (S3 아님)

**판단 기준은 비용이 아니라 "기존 코드를 고치지 않고 올릴 수 있는가"다.**
`Tracer`는 JSONL에 append하고, 캐시는 파일 경로로 조회하며, Chroma는 로컬 디렉터리를
SQLite로 연다. EFS는 마운트하면 그대로 돈다. S3는 **세 모듈을 전부 고쳐야 한다.**

저장 단가 차이는 월 $0.04 대 $0.003 — **연 $0.5 미만**이다. 그 금액을 위해 검증된 코드를
바꾸는 셈이 된다. (Mountpoint for S3도 검토했으나 append/랜덤 쓰기를 지원하지 않아
"고칠 필요 없다"는 장점 자체를 얻지 못한다.)

**접근 통제가 서술에서 집행으로 바뀐 것이 부수 소득이다:**
파일시스템 정책이 태스크 역할만 허용 + 비TLS 거부, 태스크 역할은 **액세스 포인트 경유만**
(루트 마운트 차단), `ClientRootAccess` 미부여, 액세스 포인트가 uid/gid를 10001로 강제.

### ADR-016 — Langfuse는 클라우드에 올리지 않는다

컨테이너 6개 상시 실행 = **월 ~$170** (4vCPU/8GB 기준 하한 추정). 오케스트레이터 유휴가
$0.14인데 관측 도구가 그 1,200배가 된다. ADR-013이 기각한 EKS($73)보다도 두 배 이상이다.

**ADR-007의 파사드 덕분에 제외에 코드 변경이 0이다** — `LANGFUSE_HOST=""` 하나면 된다.
같은 이미지가 로컬에서는 Langfuse로 보내고 클라우드에서는 JSONL만 쓴다.

### ADR-013 Amendment — 리전 확정 + 비용 갱신 + "URL 데모" 충돌 해소

- 리전 **`ap-northeast-2`** 확정.
- 비용표 갱신. **세션 6에 없던 항목: 퍼블릭 IPv4 $0.005/h** (2024-02부터 과금).
- **⚠️ "URL 하나로 데모"는 이 구조로 안 된다.** 오케스트레이터는 포트를 열지 않는 배치
  태스크다. URL을 만들려면 상시 서비스 + ALB(합계 월 ~$100)가 필요하고, 그건 ADR-013이
  App Runner를 기각한 바로 그 이유다. **사용자 결정: 배치 유지, URL 없음.**
  데모는 "명령 한 줄 + CloudWatch 로그 + EFS 트레이스"의 형태다.

### governance.md — `var/` 정책을 환경별로 분리

(가) 로컬 = 세션 6 기준 유지 / (나) 클라우드 = **접근 주체는 IAM 역할**.

클라우드 기준에서 로컬과 실질적으로 다른 점 두 가지를 명시했다:

- **삭제 책임자가 사람이다.** EFS에는 자동 만료가 없다. 30일 미접근 시 IA로 **내려갈 뿐
  지워지지 않는다.** 아무도 지우지 않으면 영구히 남고, 로컬과 달리 **눈에 띄지도 않는다.**
- **IAM 통제 위에 계정 관리자가 있다.** `iam:*` 보유자는 역할을 새로 만들어 우회할 수 있다.
  IAM 통제는 계정 밖과 계정 안의 일반 주체를 막는 것이지 관리자를 막지 않는다.

---

## 4. 실제 배포 (`apply`)

### 승인 절차 — 기록

1. 리소스 27개 목록 + 비용표를 제시하고 **사용자 승인을 받았다.**
2. 승인 후 `.claude/deploy-approved`를 만들었다.
3. `./apply.sh apply`로 실행 (직접 `terraform apply`를 치지 않았다).
4. **배포 확인 후 승인 파일을 삭제했다** — 다음 배포에 다시 승인을 받는다.

### 1차 시도 — 9개 생성, 3개 실패

**실패 원인이 두 종류였고 성격이 다르다.**

**(가) IAM role `description`의 한글 — 내 실수.**
IAM은 ASCII 인쇄가능문자와 라틴-1 보충만 받는다 (`\u0020-\u007E`, `\u00A1-\u00FF`). 한글이 거부됐다.

> **`terraform validate`는 이걸 잡지 못한다.** 스키마 검사이지 서버 측 제약 검사가 아니다.
> 보안그룹에서 같은 문제를 이미 한 번 만났는데(그건 프로바이더가 로컬에서 정규식을 검사해
> `validate`에 걸렸다), **IAM은 API를 때려야만 드러난다.** 같은 클래스의 문제가 검출
> 시점이 다르다는 것을 기억할 것.

AWS로 나가는 `description` 필드를 전수 점검했다: IAM 2곳 수정, ECR 수명주기 규칙 2곳은
**AWS가 받아들였으므로 그대로 둔다**, `variable`/`output`의 description은 Terraform
내부용이라 무관하다.

**(나) `ec2:CreateTags` 권한 없음 — 계정 설정.**
IAM 사용자 `ai-portfolio`에 VPC 관련 권한이 없었다. `AmazonVPCFullAccess`를 붙여 해결했다.

### 최종 결과

**리소스 27/27 생성, drift 0** (`terraform plan` -> "No changes").

권한이 **네 번에 걸쳐 하나씩 드러났다** — `ec2:CreateTags` -> `ssm:PutParameter` ->
ECR/ECS/Logs. 미리 열거했어야 했다. 최종적으로 `ai-portfolio` 사용자에 관리형 정책
5개를 붙였다: `AmazonVPCFullAccess` / `AmazonSSMFullAccess` /
`AmazonEC2ContainerRegistryPowerUser` / `AmazonECS_FullAccess` /
`CloudWatchLogsReadOnlyAccess`.

### 배포 후 검증 — 헬스체크 `exitCode=0`

태스크 하나로 네 가지가 동시에 확인됐다.

| 경로 | 결과 |
| --- | --- |
| SSM 시크릿 주입 | `VLLM_BASE`가 `secrets`로 주입돼 엔드포인트 도달 (`latency=0.874s`) |
| EFS 마운트 | 볼륨 3개 마운트 실패 시 태스크가 뜨지 않는다 — 떴다 |
| 아웃바운드 | NAT 없이 퍼블릭 서브넷+퍼블릭 IP로 AWS 밖 KT Cloud까지 도달 |
| **시크릿 마스킹** | CloudWatch Logs에 `host='***.ktcloud.com'`, `endpoint_fp='1897f0ecc081'` |

**마지막 줄이 이번 배포의 보안 확인이다.** CloudWatch Logs는 이번에 새로 생긴 노출면이고
로그 그룹 권한자 누구나 읽는다. session-06이 고친 `redacted()`가 그 경로에서도
버티는 것을 **실제 출력으로** 확인했다.

### 콜드 스타트 실측 (n=1) — 예상이 두 번 빗나갔다

| 구간 | 실측 |
| --- | ---: |
| 프로비저닝 | 19.19s |
| 이미지 풀 (ECR 862MB) | **22.22s** |
| 기동 | 19.38s |
| **콜드 스타트 합계** | **60.79s** |
| 실행 (헬스체크) | 42.37s |
| 전체 | 103.16s |

1. **이미지 크기가 지배 요인이 아니다.** 풀 22.22s < 프로비저닝+기동 38.57s.
   session-06의 "3.46GB라 첫 기동이 느릴 것"은 **방향은 맞고 원인 지목이 틀렸다.**
   이미지를 절반으로 줄여도 11초쯤 준다 — **"이미지 축소 검토"의 우선순위를 내릴 근거다.**
2. **실행 42.37s 중 LLM 호출은 0.874s.** 첫 로그가 `startedAt` 38초 뒤에 찍혔다 —
   파이썬 import 오버헤드(torch·langchain)다. README §4.1이 로컬에서 분리해 둔 콜드 로딩이
   Fargate에서도 같은 크기로 나타나고, **프로세스가 매번 새로 뜨므로 매번 낸다.**

⚠️ **Fargate에는 노드 이미지 캐시가 없다 — 모든 실행이 콜드 스타트다.** "두 번째부터
빨라진다"가 성립하지 않는다. 하루 몇 번 도는 배치에서는 문제없지만 **대화형으로 쓰려는
순간 이 60초가 사용자 대기 시간이 된다.**

실행 1회 실제 비용: 103.16s x ($0.11356 + $0.005)/h = **$0.0034**.

### ⚠️ 이미지 3.46GB는 ECR 비용의 근거가 아니었다 — 4배 과대평가

| 측정 지점 | 값 |
| --- | ---: |
| `docker images` SIZE | 3.46GB (압축 해제, 로컬 디스크) |
| **`ecr describe-images`** | **862MB (압축 레이어 — ECR이 과금하는 값)** |

ECR 저장 $0.35 -> **$0.086/월**, 유휴 합계 ~$0.40 -> **~$0.14/월**.
결정을 뒤집는 크기는 아니지만 **"3.46GB"를 비용 문맥에서 인용하지 않는다.**
그 숫자가 유효한 곳은 콜드 스타트와 로컬 디스크뿐이다.

### 존속 결정 (item 12)

**이대로 유지 — 온디맨드.** 상시 실행 0개, 유휴 ~$0.14/월.
**`.claude/deploy-approved`는 삭제했고, 게이트가 다시 닫힌 것을 확인했다**
(승인 파일 없는 상태에서 배포 명령이 exit 2로 차단됨).

---

## 5. 열린 이슈 / 결정 대기

### 이번 세션에 새로 생긴 것

- ⚠️ **IAM 관리형 정책 5개가 `ai-portfolio`에 붙어 있다 — 필요한 것보다 넓다.**
  배포를 진행하려고 붙인 것이고, 이제 이 사용자가 계정의 VPC·SSM·ECR·ECS·Logs 전반을
  만질 수 있다. **최소 권한 인라인 정책으로 좁히거나 떼어내는 것이 다음 세션 항목이다.**
  아이러니하게도 이번 세션은 태스크 역할을 최소 권한으로 설계하는 데 공을 들였는데
  (ADR-015), **그것을 배포한 사람의 권한은 반대 방향으로 갔다.**
- ⚠️ **EFS에 실제로 쓰이는지는 확인하지 않았다.** 헬스체크는 마운트를 증명하지만
  (마운트 실패 시 태스크가 안 뜬다) **non-root uid 10001이 세 디렉터리에 쓸 수 있는지는
  별개다.** `bash infra/run_task.sh index` 또는 `research`를 돌려 트레이스가 남는지 봐야
  확정된다. 액세스 포인트의 `creation_info` 소유권 설정이 의도대로 먹었는지가 초점이다.
- **`terraform validate`가 잡지 못하는 제약이 있다.** IAM role `description`의 한글이
  apply에서야 거부됐다. 같은 클래스의 제약인데 보안그룹은 validate에서 걸렸다
  (프로바이더가 로컬에서 정규식 검사). **서버 측 검증은 `plan`으로도 안 잡힌다.**
- **Git Bash(MSYS) 경로 변환이 두 번 물었다.** `--name /multiagent-research-lab/...`이
  `C:/Program Files/Git/...`으로 바뀌고, `MSYS_NO_PATHCONV=1`로 끄니 이번엔 Windows
  python이 `/tmp/...`를 못 읽었다. `run_task.sh`에 `cygpath` 변환을 넣어 해결했다.
  **`terraform.exe`에 MSYS 절대경로를 넘기면 `-chdir`이 깨진다** — 같은 원인이다.
- **콜드 스타트 n=1이다.** Fargate 프로비저닝은 가용량에 따라 흔들린다. 범위 지표로 읽을 것.
- **이미지 축소의 기대 이득이 작다는 것이 확인됐다** (위 참조). 열린 이슈에서 우선순위를 내린다.

### 계속 열려 있는 것 (이월)

- **방어 적용 후 비용·지연 미측정** (session-05부터). README §4는
  `PROMPT_VERSION=2026-09-15.1` 기준이라 직접 비교 불가. **컨테이너 안에서 재는 것이 맞다.**
- **공급망:** 의존성 해시 미고정(Python 쪽), 엔드포인트 모델 지문 확인 수단 없음.
  **Terraform 프로바이더 쪽은 이번에 닫혔다** — `.terraform.lock.hcl`을 커밋 대상으로
  전환했다(아래 §6).
- **사내 보안 정책상 무인증 엔드포인트 사용 허용 여부 미확인** (session-03부터).
  **배포로 노출 지점이 하나 늘었다** — 이제 SSM Parameter Store에도 URL이 있다.
- **골든셋에 영어 질의 대조군 미추가** (ADR-005 Amendment §6).
- **arXiv 429 지속** — 코퍼스 16건, `news` 출처 비어 있음.
- **2026년 말 대체 서빙 경로 미확보.** 배포는 이 문제를 건드리지 않았다.
- **체크포인터 백엔드 미선택.**
- **재검색 루프 실효성 없음** (ADR-006 Amendment) — `max_revisions=0` 비교가 다음 후보.
- `min_citations=1` / `max_revisions=2` / `top_k=4` 여전히 근거 없는 출발점.
- **Langfuse SDK 스팬 컨텍스트 경고** — 원인 미확인. ADR-016은 이 문제를 해결하지 않고
  클라우드 경로에서 마주치지 않게 할 뿐이다.

---

## 6. `.terraform.lock.hcl`을 커밋 대상으로 전환

세션 6이 `.gitignore`에 넣었는데, **Terraform이 커밋하도록 설계한 파일이다.**
프로바이더의 sha256을 고정해 다른 머신·CI에서 같은 바이너리를 받게 한다.
`versions.tf`의 `~> 5.60`은 범위일 뿐이라 잠금 파일이 없으면 다음 `init`에서 다른 버전이 온다.

`versions.tf` 주석이 "버전 고정 — requirements.txt와 같은 이유다(재현성)"라고 적은 의도와
어긋나 있었고, `owasp-notes` §3.1의 "의존성 해시 미고정" 중 Terraform 조각이기도 하다.
**사용자 승인 후 전환했다.** 지금 고정된 것은 `hashicorp/aws v5.100.0`이다.

---

## 주의 (이월 + 갱신)

- 코드 실행은 **레포 루트를 cwd로**.
- **`aws` 명령은 에이전트가 실행할 수 없다** (`deny`). 사람이 `!` 로 직접 친다.
- **새 터미널을 열 것** — winget이 갱신한 PATH가 기존 셸에는 안 들어온다.
- 지연 측정 전 **`LLM_CACHE` 상태 확인**, **임베딩 콜드 로딩 분리** (governance "측정 방법론").
- **컨테이너 수치와 호스트 수치를 직접 비교하지 말 것** (가중치가 로컬이라 콜드 로딩이 다르다).
  **이제 Fargate 수치도 별개다** — CPU가 다르다.
- 인덱스·트레이스·캐시는 커밋하지 않는다. **클라우드에서는 EFS에 있다** — 태스크를 지워도,
  이미지를 지워도 남는다. `terraform destroy`가 **데이터째** 지운다.
- `terraform.tfvars`는 `.gitignore` 대상이다 (알람 메일 주소가 들어 있다).
- **`VLLM_BASE`는 Terraform이 다루지 않는다.** SSM에 수동으로 넣는다 —
  `aws_ssm_parameter`를 쓰면 값이 `terraform.tfstate`에 평문으로 들어간다.
- 임베딩 모델을 바꾸면 `build_index.py --reset` **+ `EMBEDDING_REVISION` 설정**.
- **모델이 바뀌면 캐시를 비울 것.** `endpoint_fp`로 엔드포인트 변경을 확인할 수 있다.

## 승인 게이트

`docs/governance.md`대로 진행했다.

- **새 유료 리소스를 만들었다.** 목록·비용을 먼저 제시하고 **명시적 승인을 받은 뒤**
  `.claude/deploy-approved`를 만들고 `./apply.sh apply`로 실행했다.
- **배포 확인 후 승인 파일을 삭제했다.**
- 엔드포인트 호출은 기존 무료 할당 자원이라 사전 승인 불필요.
- **커밋 전 시크릿 검사 실행함** — `scan_local_secrets.py` 162개 파일 **0건**
  (양성 대조 3종 통과).
