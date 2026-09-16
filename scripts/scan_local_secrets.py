"""로컬 산출물에 엔드포인트 URL이 평문으로 남아 있는지 검사한다.

**왜 필요한가.** `docs/governance.md`의 커밋 전 확인은 `git diff --cached`만 본다 —
즉 **커밋되는 것**만 검사한다. 그런데 `var/traces/`·`var/llm_cache/`는 `.gitignore`
대상이라 그 검사를 영원히 통과한다. **커밋되지 않는 것과 디스크에 없는 것은 다르다**
(governance "로컬 산출물 취급"). 이 스크립트가 그 구멍을 검사한다.

session-06에서 `ProviderSettings.redacted()`가 호스트를 가리지 않고 있었던 것이
발견됐고(ADR-010 / handoff §1), 그 출력이 파일로도 샜는지 확인할 필요가 생겼다.

**자기참조 방식.** 검사할 문자열을 이 파일에 적지 않는다 — 적는 순간 그 자체가
평문 노출이고, 이 스크립트는 커밋된다. `.env`의 실제 값을 읽어 대조한다.
governance의 커밋 전 확인 명령과 같은 원리다.

**양성 대조를 반드시 함께 돈다.** "0건 검출"은 검사기가 고장 나도 똑같이 나오는
결과다. 그래서 호스트가 확실히 들어 있는 입력(`.env` 자체 + 합성 JSONL)에서
검출되는지 먼저 확인하고, 그게 실패하면 **스캔 결과를 신뢰하지 않고 즉시 실패**한다.

사용:
    python scripts/scan_local_secrets.py           # 검사만
    python scripts/scan_local_secrets.py --redact  # 발견 시 마스킹 치환 (var/ 한정)

종료 코드: 0 = 깨끗함, 1 = 검출됨, 2 = 검사기 자체가 신뢰 불가
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 검사 대상. `var/`가 핵심이다 — git 검사가 닿지 않는 유일한 곳이다.
SCAN_GLOBS = (
    "var/traces/*.jsonl",
    "var/llm_cache/**/*",
    "docs/**/*.md",
    "docs/**/*.json",
    "*.md",
)

# 치환은 `var/` 산출물에만 한다. 문서·코드에서 발견되면 사람이 직접 고쳐야 한다
# (왜 거기 있는지부터 따져야 하므로 자동 치환이 오히려 위험하다).
REDACT_GLOBS = ("var/traces/*.jsonl", "var/llm_cache/**/*")


def load_needles() -> dict[str, str]:
    """`.env`에서 대조 문자열을 만든다. 값은 절대 출력하지 않는다."""
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        print("[FAIL] .env가 없습니다 — 대조할 값을 만들 수 없습니다.", file=sys.stderr)
        raise SystemExit(2)

    base = ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("VLLM_BASE="):
            base = line.split("=", 1)[1].strip().rstrip("/")

    if not base:
        print("[FAIL] .env에 VLLM_BASE가 없습니다.", file=sys.stderr)
        raise SystemExit(2)

    host = base.split("://")[-1].split("/")[0].split(":")[0]
    labels = host.split(".")

    needles = {"base_url": base, "host": host}
    # 앞 라벨이 추측 불가능한 부분이다 — 호스트 전체가 아니라 이것만 새는 경우를 잡는다.
    if len(labels) > 2 and labels[0] != host:
        needles["host_first_label"] = labels[0]
    return needles


def masked_replacement(needles: dict[str, str]) -> dict[str, str]:
    """`ProviderSettings.redacted()`와 **같은 형식**으로 치환한다.

    형식이 다르면 나중에 트레이스를 읽는 사람이 "이건 뭐지"로 멈춘다.
    """
    host = needles["host"]
    labels = host.split(".")
    public_suffix = ".".join(labels[-2:]) if len(labels) > 2 else host
    masked_host = f"***.{public_suffix}" if len(labels) > 2 else "***"

    scheme = needles["base_url"].split("://")[0] if "://" in needles["base_url"] else "https"
    return {
        # 긴 것부터 치환해야 한다 — host를 먼저 바꾸면 base_url이 깨진 채로 남는다.
        "base_url": f"{scheme}://{masked_host}/v1",
        "host": masked_host,
        "host_first_label": "***",
    }


def self_test(needles: dict[str, str]) -> bool:
    """검사기가 실제로 검출하는지 확인한다. 이게 실패하면 스캔 결과는 무의미하다."""
    ok = True

    env_text = (REPO_ROOT / ".env").read_text(encoding="utf-8")
    for name in ("base_url", "host"):
        found = env_text.count(needles[name])
        print(f"  [양성대조] .env 에서 {name} 검출: {found}건 -> {'OK' if found else 'FAIL'}")
        ok = ok and found > 0

    with tempfile.TemporaryDirectory() as d:
        probe = Path(d) / "synthetic.jsonl"
        probe.write_text(
            '{"type":"generation","metadata":{"base_url":"%s"}}\n' % needles["base_url"],
            encoding="utf-8",
        )
        found = probe.read_text(encoding="utf-8").count(needles["base_url"])
        print(f"  [양성대조] 합성 JSONL에서 base_url 검출: {found}건 -> {'OK' if found else 'FAIL'}")
        ok = ok and found > 0

    return ok


def iter_files(globs: tuple[str, ...]):
    seen = set()
    for pattern in globs:
        for path in REPO_ROOT.glob(pattern):
            if path.is_file() and path not in seen:
                seen.add(path)
                yield path


def scan(needles: dict[str, str]) -> dict[Path, dict[str, int]]:
    hits: dict[Path, dict[str, int]] = {}
    for path in iter_files(SCAN_GLOBS):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        counts = {k: text.count(v) for k, v in needles.items()}
        if any(counts.values()):
            hits[path] = counts
    return hits


def redact(needles: dict[str, str]) -> int:
    """발견된 것을 마스킹 형식으로 **제자리 치환**한다.

    백업 파일을 남기지 않는다. `.bak`을 남기면 평문이 그대로 디스크에 남아
    작업 자체가 무의미해진다.
    """
    replacements = masked_replacement(needles)
    # 긴 문자열부터 — 짧은 것을 먼저 바꾸면 긴 것이 깨진 채 남는다.
    order = sorted(needles, key=lambda k: len(needles[k]), reverse=True)

    changed = 0
    for path in iter_files(REDACT_GLOBS):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        original = text
        for key in order:
            text = text.replace(needles[key], replacements[key])
        if text != original:
            path.write_text(text, encoding="utf-8")
            changed += 1
            print(f"  [치환] {path.relative_to(REPO_ROOT)}")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="로컬 산출물 엔드포인트 URL 검사 (ADR-010)")
    parser.add_argument("--redact", action="store_true", help="발견 시 var/ 산출물을 마스킹 치환")
    args = parser.parse_args()

    needles = load_needles()
    print(f"대조 대상 {len(needles)}종 (값은 출력하지 않는다)\n")

    print("검사기 자체 검증:")
    if not self_test(needles):
        print("\n[FAIL] 양성 대조 실패 — 검사기를 신뢰할 수 없습니다. 결과를 무시하세요.", file=sys.stderr)
        return 2
    print()

    hits = scan(needles)
    scanned = sum(1 for _ in iter_files(SCAN_GLOBS))

    if not hits:
        print(f"[OK] 검사 파일 {scanned}개 — 엔드포인트 URL 평문 **0건**.")
        return 0

    total = sum(sum(c.values()) for c in hits.values())
    # 주의: 니들끼리 포함 관계다 (host_first_label ⊂ host ⊂ base_url). 같은 한 곳이
    # 여러 니들에 잡히므로 아래 합계는 **겹쳐 세어진 값**이고 실제 위치 수보다 크다.
    # 치환은 긴 것부터 처리하므로 이 겹침이 문제가 되지 않는다.
    print(f"[검출] 파일 {len(hits)}개 / 니들 적중 {total}건 (겹침 포함, 검사 파일 {scanned}개)")
    for path, counts in hits.items():
        shown = {k: v for k, v in counts.items() if v}
        print(f"  {path.relative_to(REPO_ROOT)}  {shown}")

    if not args.redact:
        print("\n치환하려면 --redact 를 붙이세요 (var/ 산출물만 치환합니다).")
        return 1

    print()
    changed = redact(needles)
    print(f"\n[치환 완료] {changed}개 파일. 재검사합니다...")

    remaining = scan(needles)
    if remaining:
        print(f"[FAIL] 아직 {len(remaining)}개 파일에 남아 있습니다 (var/ 밖일 수 있습니다).")
        for path in remaining:
            print(f"  {path.relative_to(REPO_ROOT)}")
        return 1

    print("[OK] 재검사 결과 0건.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
