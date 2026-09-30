"""저장된 bench 결과의 span-항목 귀속을 다시 검사한다 (ADR-026).

bench는 `--require-trace`일 때 행마다 같은 검사(`bench_golden.attribution_problems`)를 하고,
불일치가 있으면 부분 결과 없이 멈춘다. 이 스크립트는 **이미 저장된** 결과를 로컬 트레이스와
다시 대조할 때 쓴다 — 예: ADR-026 이전에 bench가 이 검사를 하지 않던 시기의 결과.

    python scripts/check_attribution.py <bench.json> [<bench.json> ...]

종료 코드: 0 = 불일치 없음 · 1 = 불일치 있음 · 2 = 트레이스 없음 / 키 없는 옛 트레이스 / 파일 오류

로컬 `var/traces/`가 필요하다(커밋되지 않는다). LLM 호출 0건. 읽기 전용이다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.bench_golden import _read_trace, attribution_problems  # noqa: E402
from src.providers.config import load_tracing_settings  # noqa: E402


def check(result: dict, trace_dir: Path) -> dict:
    problems: dict[str, list[str]] = {}
    unreadable: list[str] = []
    items_total = 0
    for row in result["rows"]:
        records = _read_trace(trace_dir / f"{row['run_id']}.jsonl")
        if records is None or row.get("items") is None:
            unreadable.append(row["case_id"])
            continue
        if any(i.get("item_index") is None for i in row["items"]):
            unreadable.append(row["case_id"])  # ADR-026 이전 결과 — 짝지을 키가 없다
            continue
        items_total += len(row["items"])
        found = attribution_problems(row["items"], records)
        if found:
            problems[row["case_id"]] = found
    return {"label": result.get("label"), "rows": len(result["rows"]), "items": items_total,
            "problems": problems, "unreadable": unreadable}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", type=Path, nargs="+")
    args = parser.parse_args(argv)
    trace_dir = load_tracing_settings().local_trace_dir
    worst = 0
    for path in args.results:
        try:
            report = check(json.loads(path.read_text(encoding="utf-8")), trace_dir)
        except (OSError, ValueError, KeyError) as exc:
            print(f"[거부] {path.name}: {exc}")
            worst = max(worst, 2)
            continue
        n_problems = sum(len(v) for v in report["problems"].values())
        print(f"{report['label']}: 행 {report['rows']} · 항목 {report['items']} · "
              f"귀속 불일치 {n_problems} · 검사 불가 {report['unreadable']}")
        for case_id, found in report["problems"].items():
            for problem in found:
                print(f"    {case_id}: {problem}")
        if report["unreadable"]:
            worst = max(worst, 2)
        elif n_problems:
            worst = max(worst, 1)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
