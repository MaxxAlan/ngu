#!/usr/bin/env python3
"""Run Phase 0 agent benchmark suite.

Examples:
  uv run python scripts/run_benchmarks.py --mode reference
  uv run python scripts/run_benchmarks.py --mode reference --write-baseline
  uv run python scripts/run_benchmarks.py --mode live --model qwen2.5-coder-3b
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.benchmarks.harness import (  # noqa: E402
    BASELINE_DIR,
    compare_to_baseline,
    run_suite,
    write_results,
)


async def amain() -> int:
    ap = argparse.ArgumentParser(description="NGU Phase 0 benchmark runner")
    ap.add_argument("--mode", choices=["reference", "live"], default="reference")
    ap.add_argument("--model", default="reference", help="Model name for live mode")
    ap.add_argument("--ctx", type=int, default=None)
    ap.add_argument("--task", action="append", default=None, help="Filter task id (repeatable)")
    ap.add_argument("--out", type=Path, default=None, help="Write full JSON results here")
    ap.add_argument("--write-baseline", action="store_true", help="Overwrite baseline (reference mode)")
    ap.add_argument("--compare-baseline", action="store_true", help="Exit 1 on regression vs baseline")
    args = ap.parse_args()

    suite = await run_suite(mode=args.mode, model=args.model, ctx=args.ctx, task_ids=args.task)
    summary = {
        "mode": suite.mode,
        "model": suite.model,
        "ctx": suite.ctx,
        "passed": suite.passed,
        "failed": suite.failed,
        "errors": suite.errors,
        "pass_rate": round(suite.passed / max(1, len(suite.tasks)), 4),
        "elapsed_ms": suite.elapsed_ms,
        "catalog_sha256": suite.catalog_sha256,
    }
    print(json.dumps(summary, indent=2))
    for t in suite.tasks:
        mark = {"pass": "OK", "fail": "FAIL", "error": "ERR"}[t.outcome]
        print(f"  [{mark}] {t.task_id:28s} steps={t.steps:2d} tools={t.tool_calls:2d} {t.elapsed_ms:7.1f}ms")

    out = args.out
    if args.write_baseline:
        if args.mode != "reference":
            print("write-baseline only allowed for --mode reference", file=sys.stderr)
            return 2
        out = BASELINE_DIR / "reference_results.json"
    if out:
        write_results(suite, out)
        print(f"wrote {out}")

    if args.compare_baseline:
        baseline = BASELINE_DIR / "reference_results.json"
        problems = compare_to_baseline(suite, baseline)
        if problems:
            print("BASELINE REGRESSION:", file=sys.stderr)
            for msg in problems:
                print(f"  - {msg}", file=sys.stderr)
            return 1
    return 0 if suite.errors == 0 and suite.failed == 0 else 1


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
