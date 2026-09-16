# 배포자 IAM 최소 권한 (ADR-017)

배포자 IAM 사용자에 붙은 관리형 정책 다발을, 이 레포가 **실제로 쓰는 액션만** 담은
고객 관리형 정책 2개로 교체한다.

| 파일 | 내용 |
| --- | --- |
| `deploy-core.json` | 네트워크(EC2/VPC) · EFS · Budgets. `aws:RequestedRegion`으로 서울에 가둔다 |
| `deploy-app.json` | ECR · ECS · Logs · SSM · IAM 역할. 대부분 프로젝트 접두사 ARN으로 가둔다 |
| `apply_least_privilege.sh` | `show` / `free-slots` / `attach` / `detach` / `rollback` |

계정 ID 자리는 `ACCOUNT_ID` 자리표시자로 두고 커밋한다. 렌더링 결과(`.rendered/`)는
`.gitignore` 대상이다 — 거기엔 계정 ID가 들어간다.

⚠️ 이 스크립트는 AWS CLI를 쓴다. `.claude/settings.json`의 deny 규칙상 **에이전트가 아니라
사람이 실행한다.**

---

## 절차

```bash
bash infra/iam/apply_least_privilege.sh show         # 현재 상태 기록 (롤백 근거)
bash infra/iam/apply_least_privilege.sh free-slots    # 한도 10개 — 중복 정책부터 뗀다
bash infra/iam/apply_least_privilege.sh attach
cd infra/terraform && ./apply.sh plan                 # 기존 정책 병존 상태에서 확인
CONFIRM=yes bash infra/iam/apply_least_privilege.sh detach
# ⚠️ 여기서 최소 몇 분 기다린다 (아래 "전파 지연" 참조)
cd infra/terraform && ./apply.sh plan                 # ← 이것이 진짜 검증
```

### ⚠️ 전파 지연 — session-08에서 실제로 속았다

**IAM 정책 변경은 즉시 반영되지 않는다.** `detach` 직후에 돌린 `plan`이 `No changes`로
통과했고 그것을 "최소 권한만으로 동작 확인"으로 기록했는데, **같은 명령을 나중에 다시
돌리니 `AccessDenied`로 실패했다.** 처음 통과는 아직 살아 있던 옛 권한으로 돈 결과였다.

> **분리 직후의 성공은 검증이 아니다.** 시간을 두고 한 번 더 돌린 결과만 믿는다.

### ⚠️ 관리형 정책은 사용자당 10개까지다

이미 10개가 붙어 있으면 `attach`가 `LimitExceeded`로 실패한다 —
**"붙이고 나서 뗀다"는 안전 순서가 성립하지 않는다.** `free-slots`는 함께 붙어 있는
FullAccess의 **부분집합인 중복 정책**만 떼어 유효 권한 손실 없이 슬롯을 비운다.
중복이 없는 계정에서는 이 우회가 불가능하며, 그때는 루트 콘솔을 써야 한다.

---

## 정책을 고쳐야 할 때 — **루트 콘솔이 필요하다**

새 정책에는 `iam:AttachUserPolicy`도 `iam:CreatePolicyVersion`도 **없다.** 일부러 뺐다 —
있으면 배포자가 자기 정책에 관리자 상당 권한을 써 넣을 수 있어 "최소 권한"이 허구가 된다.

**그 대가로, 부족한 액션을 발견해도 배포자가 스스로 고칠 수 없다.**
이것은 자기제한(self-limiting)의 본질적 비용이며 우회하면 목적 자체가 사라진다.
빈도가 낮으므로(정책 수정은 리소스를 새로 추가할 때뿐) 감당 가능하다고 판단했다.

### 루트 콘솔 절차

1. 루트 계정으로 AWS 콘솔 로그인 (MFA 필요)
2. **IAM → 정책 → `multiagent-research-lab-deploy-core`** (또는 `-app`)
3. **[권한 편집] → JSON** 에 이 레포의 해당 `deploy-*.json` 내용을 붙여넣는다.
   **`ACCOUNT_ID` 자리표시자를 실제 계정 ID로 바꿔야 한다.**
4. **[새 버전을 기본값으로 설정]** 체크 후 저장
5. 몇 분 기다린 뒤 `cd infra/terraform && ./apply.sh plan`으로 확인

> 정책당 버전은 5개까지다. 꽉 차면 오래된 비기본 버전을 먼저 지운다.

### 미분리 정책 1개

`AWSBudgetsActionsWithAWSResourceControlAccess`가 붙은 채로 남아 있다. 배포자에게
`iam:DetachUserPolicy`가 없어 스스로 뗄 수 없다(자기제한이 의도대로 작동한 결과).
**루트 콘솔 → IAM → 사용자 → 권한**에서 분리한다. 예산 알람 1개를 만들려고 붙기에는
넓은 정책이며, 정책을 적용하는 권한을 포함하므로 남겨두면 `IAMFullAccess`를 뗀 의미가
일부 상쇄된다.

---

## 알려진 공백

- **`plan` 통과가 `apply`/`destroy` 통과를 보장하지 않는다.** 액션 목록은 Terraform 구성에서
  역산한 것이고 `plan`은 읽기만 한다. 세션 7이 "validate 통과 ≠ apply 통과"로 겪은 것과
  같은 클래스다. 다음 배포·철거 때 드러난다.
- **`detach` 루프가 IAM 관련 정책을 마지막에 떼도록 정렬돼 있지 않다.** `IAMFullAccess`를
  중간에 떼면 그 시점 이후의 분리 권한이 사라진다. session-08에서 뒤의 3개가 성공한 것은
  전파 지연 덕분이지 설계가 옳아서가 아니다.
