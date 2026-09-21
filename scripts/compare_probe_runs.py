"""두 프로브 산출물이 **검색 결과로서 동일한지** 대조한다 (session-16).

검색 계층을 고칠 때마다 물어야 하는 질문은 하나다 — **무엇이 몇 위로 왔는지가
바뀌었는가.** 두 JSON을 통째로 비교하면 항상 다르다: 측정 시각과 지연이 들어 있기
때문이다. 그래서 **벽시계 값만 제외하고 나머지를 바이트 단위로** 비교한다.

제외 목록을 여기 고정해 둔 이유: 비교할 때마다 손으로 고르면 "이번엔 이것도 달라도
괜찮다"가 슬금슬금 늘어난다. 제외는 **시간 값뿐**이고, 그 목록은 아래 `VOLATILE_KEYS`가
전부다. 문서 ID·순위·점수·재현율은 하나도 제외되지 않는다.

    python scripts/compare_probe_runs.py <before.json> <after.json>

종료 코드: 0 = 동일 · 1 = 다름(차이를 출력한다) · 2 = 파일을 읽을 수 없음

LLM 호출 0건. 외부 API 호출 0건. 읽기 전용이다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

VOLATILE_KEYS: frozenset[str] = frozenset(
    {
        "label",  # 라벨은 실행을 구분하려고 붙이는 이름이다
        "measured_at",
        "generated_at",
        "elapsed_s",
        "cold_load_s",
        "warm_encode_s",
        "embedding_cold_load_s",
        "retrieval_latency_s",
    }
)
"""비교에서 빼는 키. **전부 시간 값 또는 실행 이름이다.**

지연은 같은 코드·같은 인덱스에서도 실행마다 다르다(§ADR-005 Amendment 2 주의:
임베딩 콜드 로딩은 질의 지연이 아니다). 검색 결과가 아니므로 대조 대상에서 뺀다.
"""


def strip_volatile(value: object) -> object:
    """휘발성 키를 재귀적으로 걷어낸다."""
    if isinstance(value, dict):
        return {k: strip_volatile(v) for k, v in value.items() if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [strip_volatile(v) for v in value]
    return value


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def diffs(left: object, right: object, path: str = "") -> list[str]:
    """첫 몇 건이 아니라 **전건**을 모은다. 요약하면 놓친 것이 생긴다."""
    if type(left) is not type(right):
        return [f"{path}: 타입 다름 {type(left).__name__} -> {type(right).__name__}"]
    if isinstance(left, dict):
        out: list[str] = []
        for key in sorted(set(left) | set(right)):
            out.extend(diffs(left.get(key), right.get(key), f"{path}.{key}"))
        return out
    if isinstance(left, list):
        if len(left) != len(right):
            return [f"{path}: 길이 다름 {len(left)} -> {len(right)}"]
        out = []
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            out.extend(diffs(a, b, f"{path}[{index}]"))
        return out
    return [] if left == right else [f"{path}: {left!r} -> {right!r}"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    args = parser.parse_args()

    try:
        before = json.loads(args.before.read_text(encoding="utf-8"))
        after = json.loads(args.after.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"읽을 수 없습니다: {exc}")
        return 2

    left, right = strip_volatile(before), strip_volatile(after)
    left_canon, right_canon = canonical(left), canonical(right)
    left_sha = hashlib.sha256(left_canon.encode("utf-8")).hexdigest()
    right_sha = hashlib.sha256(right_canon.encode("utf-8")).hexdigest()

    cases = before.get("cases")
    print(f"before : {args.before.name}")
    print(f"after  : {args.after.name}")
    print(f"케이스 : {len(cases) if isinstance(cases, list) else '?'}건")
    print(f"제외한 키(시간·라벨만): {', '.join(sorted(VOLATILE_KEYS))}")
    print(f"sha256 before: {left_sha}")
    print(f"sha256 after : {right_sha}")

    if left_canon == right_canon:
        print("\n[OK] 시간 값을 제외한 전 필드가 바이트 단위로 동일하다.")
        return 0

    found = diffs(left, right)
    print(f"\n[DIFF] 차이 {len(found)}건:")
    for line in found:
        print(f"  {line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
