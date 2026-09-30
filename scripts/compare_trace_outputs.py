"""두 bench 회차의 LLM 호출을 **트레이스에서** 호출 단위로 대조한다 (v1.2-P1 관찰, ADR-026).

`compare_p1_concurrency.py`(판정)는 bench 행만 본다 — 인용 집합·pass/fail·1회차 supporting.
이 스크립트는 그 아래 층을 본다: **같은 입력을 받은 호출이 같은 텍스트를 냈는가.**
판정 지표가 아니라 관찰이다. P1 측정 **후에** 추가했다(session-20) — 토큰 합이 6 다른 것을
설명하려고. 판정 규칙에는 들어가지 않는다.

- 짝짓기: 두 회차 모두 span에 `revision`·`item_index`가 있으면 (span 이름, revision, item_index)로,
  없으면(ADR-026 이전 트레이스) 파일 순서로 짝짓는다 — 순차 실행은 순서가 결정적이다.
- 입력 비교: `input_hash`가 양쪽에 있으면 해시로, 없으면 span `input`(4,000자에서 잘림)으로.
- 출력: 텍스트가 다른 호출 수, 그중 **구조 필드**(`supporting` / `verdict`)까지 다른 수.
- 실행 내부 반복: 한 실행 안에서 같은 입력이 두 번 이상 나온 경우(Verifier 재판정) 출력이 다른 수.

    python scripts/compare_trace_outputs.py <base.json> <after.json> [--out <out.json>]

로컬 `var/traces/`가 필요하다(커밋되지 않는다). LLM 호출 0건. 읽기 전용(`--out`만 쓴다).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.orchestrator.nodes import _extract_json  # noqa: E402
from src.providers.config import load_tracing_settings  # noqa: E402


def _generations(trace_dir: Path, run_id: str) -> list[dict] | None:
    path = trace_dir / f"{run_id}.jsonl"
    if not path.exists():
        return None
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [r for r in records if r.get("type") == "generation"]


def _key(record: dict) -> tuple[str, int, int] | None:
    meta = record.get("metadata") or {}
    if not isinstance(meta.get("revision"), int) or not isinstance(meta.get("item_index"), int):
        return None
    return record["name"], meta["revision"], meta["item_index"]


def _input_id(record: dict, *, use_hash: bool = True) -> str:
    meta = record.get("metadata") or {}
    if use_hash and meta.get("input_hash"):
        return meta["input_hash"]
    blob = json.dumps(record.get("input"), ensure_ascii=False, sort_keys=True)
    return "span:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _structured(record: dict) -> object:
    if record["name"] == "writer_call":
        return None  # 본문 자체가 출력이다 — 구조 필드가 없다
    parsed = _extract_json(record.get("output") or "")
    if not isinstance(parsed, dict):
        return ("unparsed",)
    return parsed.get("supporting"), str(parsed.get("verdict", "")).strip().lower() or None


def _pair(a: list[dict], b: list[dict]) -> list[tuple[dict, dict]] | None:
    """Outliner·Writer는 키가 없다(항목 호출이 아니다) — 이름별 파일 순서로 짝짓는다."""
    if len(a) != len(b):
        return None
    keyed = all(_key(r) is not None for r in a + b if r["name"] in ("researcher_call", "verifier_call"))
    if not keyed:
        return list(zip(a, b, strict=True))

    def index(records: list[dict]) -> dict:
        out, seen = {}, Counter()
        for r in records:
            key = _key(r) or (r["name"], -1, seen[r["name"]])
            seen[r["name"]] += 1
            out[key] = r
        return out

    ia, ib = index(a), index(b)
    if set(ia) != set(ib):
        return None
    return [(ia[k], ib[k]) for k in sorted(ia)]


def _within_run(records: list[dict]) -> tuple[int, int]:
    first: dict[str, str] = {}
    pairs = differ = 0
    for r in records:
        ident = _input_id(r)
        if ident in first:
            pairs += 1
            differ += first[ident] != r.get("output")
        else:
            first[ident] = r.get("output")
    return pairs, differ


def compare(base: dict, after: dict, trace_dir: Path) -> dict:
    rows_a = {r["case_id"]: r for r in base["rows"]}
    rows_b = {r["case_id"]: r for r in after["rows"]}
    if set(rows_a) != set(rows_b):
        raise ValueError("케이스 구성이 다르다")

    calls = input_diff = text_diff = struct_diff = 0
    by_node: Counter = Counter()
    unpaired: list[str] = []
    examples: list[dict] = []
    within = {"base": [0, 0], "after": [0, 0]}

    for case_id in sorted(rows_a):
        ga = _generations(trace_dir, rows_a[case_id]["run_id"])
        gb = _generations(trace_dir, rows_b[case_id]["run_id"])
        if ga is None or gb is None:
            unpaired.append(case_id)
            continue
        for side, g in (("base", ga), ("after", gb)):
            p, d = _within_run(g)
            within[side][0] += p
            within[side][1] += d
        pairs = _pair(ga, gb)
        if pairs is None:
            unpaired.append(case_id)
            continue
        for x, y in pairs:
            calls += 1
            # 한쪽이라도 해시가 없으면(ADR-026 이전) 양쪽 다 span 입력으로 비교한다.
            use_hash = bool((x.get("metadata") or {}).get("input_hash")) and bool(
                (y.get("metadata") or {}).get("input_hash"))
            if _input_id(x, use_hash=use_hash) != _input_id(y, use_hash=use_hash):
                input_diff += 1
                continue
            if x.get("output") != y.get("output"):
                text_diff += 1
                by_node[x["name"]] += 1
                changed = _structured(x) != _structured(y)
                struct_diff += changed
                examples.append({
                    "case_id": case_id, "call": x["name"],
                    "revision": (x.get("metadata") or {}).get("revision"),
                    "item_index": (x.get("metadata") or {}).get("item_index"),
                    "structured_changed": changed,
                    "completion_tokens": [(x.get("metadata") or {}).get("completion_tokens"),
                                          (y.get("metadata") or {}).get("completion_tokens")],
                })

    return {
        "base": base.get("label"),
        "after": after.get("label"),
        "calls_paired": calls,
        "input_differs": input_diff,
        "same_input_text_differs": text_diff,
        "same_input_structured_differs": struct_diff,
        "text_differs_by_node": dict(sorted(by_node.items())),
        "within_run_repeated_input": {
            side: {"pairs": v[0], "text_differs": v[1]} for side, v in within.items()
        },
        "unpaired_cases": unpaired,
        "calls": examples,
        "note": "관찰 — 판정 지표 아님 (ADR-026). 출력 텍스트는 내용이 아니라 호출 위치만 기록한다.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.out is not None and args.out.exists():
        print(f"[거부] 출력 파일이 이미 있다: {args.out}")
        return 2
    try:
        load = lambda p: json.loads(p.read_text(encoding="utf-8"))  # noqa: E731
        result = compare(load(args.base), load(args.after), load_tracing_settings().local_trace_dir)
    except (OSError, ValueError, KeyError) as exc:
        print(f"[거부] {exc}")
        return 2
    print(f"{result['base']} ↔ {result['after']}: 호출 {result['calls_paired']} · 입력 다름 "
          f"{result['input_differs']} · 입력 같고 텍스트 다름 {result['same_input_text_differs']} "
          f"{result['text_differs_by_node']} · 구조 필드 다름 {result['same_input_structured_differs']} · "
          f"실행 내부 반복 {result['within_run_repeated_input']} · 짝 없음 {result['unpaired_cases']}")
    if args.out is not None:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
