"""임베딩 가중치를 **고정된 리비전으로** 내려받고 sha256을 검증한다 (ADR-011).

런타임에 HuggingFace `main`을 받으면 두 가지가 동시에 깨진다.

1. **재현성** — 같은 이름으로 다른 가중치가 재배포되면 과거 측정치를 다시 낼 수 없다.
   이 프로젝트는 하네스의 효과를 재는 것이 목적이라(ADR-002) 임베딩이 조용히 바뀌면
   측정 자체가 무의미해진다.
2. **공급망 무결성** — 바뀌었다는 사실을 탐지할 수단이 없다 (owasp-notes §3.1 LLM03).

그래서 `infra/model-pin.json`의 `revision`(git 커밋 해시)으로만 받고, LFS 파일은
`sha256`까지 대조한다. **리비전은 "무엇을 받을지"를, sha256은 "받은 것이 맞는지"를
고정한다** — 둘은 대체 관계가 아니다.

사용:
    python scripts/fetch_model.py                      # 기본 경로(vendor/models/...)로
    python scripts/fetch_model.py --dest /opt/models/e5-small
    python scripts/fetch_model.py --verify-only        # 이미 받은 것만 검증

컨테이너는 **빌드 시점**에 이걸 돌려 가중치를 이미지에 굽는다. 런타임에는 네트워크를
타지 않는다 (ADR-010).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

PIN_FILE = REPO_ROOT / "infra" / "model-pin.json"
DEFAULT_DEST = REPO_ROOT / "vendor" / "models" / "multilingual-e5-small"

_CHUNK = 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def load_pin() -> dict:
    try:
        return json.loads(PIN_FILE.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"[FAIL] 핀 매니페스트를 읽을 수 없습니다: {PIN_FILE} ({exc})")
    except ValueError as exc:
        raise SystemExit(f"[FAIL] 핀 매니페스트 JSON 오류: {PIN_FILE} ({exc})")


def verify(dest: Path, pin: dict) -> int:
    """매니페스트의 sha256과 실제 파일을 대조한다.

    불일치는 경고가 아니라 **실패**다. 가중치가 다르면 인덱스와 벡터 공간이 어긋나
    에러 없이 검색 품질만 나빠지는 조용한 실패가 나기 때문이다.
    """
    files = pin.get("files") or {}
    if not files:
        print("[WARN] 매니페스트에 sha256 항목이 없습니다 — 리비전 고정만 유효합니다.")
        return 0

    failed = 0
    for rel, expected in files.items():
        path = dest / rel
        if not path.exists():
            print(f"[FAIL] 없음: {rel}")
            failed += 1
            continue
        size = path.stat().st_size
        if expected.get("size") and size != expected["size"]:
            print(f"[FAIL] 크기 불일치: {rel} ({size} != {expected['size']})")
            failed += 1
            continue
        actual = _sha256(path)
        if actual != expected["sha256"]:
            print(f"[FAIL] sha256 불일치: {rel}\n       기대 {expected['sha256']}\n       실제 {actual}")
            failed += 1
            continue
        print(f"[OK]   {rel}  sha256={actual[:16]}…  ({size:,} bytes)")

    if failed:
        print(f"\n[FAIL] {failed}개 파일이 매니페스트와 다릅니다. 가중치를 신뢰할 수 없습니다.")
        return 1
    print(f"\n[OK] 검증 통과 — {len(files)}개 파일이 매니페스트와 일치합니다.")
    return 0


def fetch(dest: Path, pin: dict) -> None:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:  # pragma: no cover - 환경 문제
        raise SystemExit(
            f"[FAIL] huggingface_hub가 없습니다 ({exc}). "
            "`pip install -r requirements.txt`를 먼저 실행하세요."
        )

    repo_id = pin["repo_id"]
    revision = pin["revision"]
    print(f"[INFO] repo     : {repo_id}")
    print(f"[INFO] revision : {revision}")
    print(f"[INFO] dest     : {dest}")

    dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo_id,
        revision=revision,
        local_dir=str(dest),
        allow_patterns=pin.get("allow_patterns") or None,
    )
    # 받은 리비전을 디렉터리에 남긴다. 나중에 "이 경로가 어느 리비전인가"를
    # 파일시스템만 보고 답할 수 있어야 한다.
    (dest / "PINNED_REVISION").write_text(f"{repo_id}\n{revision}\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="임베딩 가중치 고정 다운로드 (ADR-011)")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="다운로드 없이 기존 디렉터리만 검증한다",
    )
    args = parser.parse_args()

    pin = load_pin()
    if not pin.get("revision"):
        print("[FAIL] 매니페스트에 revision이 비어 있습니다 — 고정되지 않은 상태입니다.")
        return 2

    if not args.verify_only:
        fetch(args.dest, pin)

    print()
    return verify(args.dest, pin)


if __name__ == "__main__":
    raise SystemExit(main())
