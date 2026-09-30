#!/usr/bin/env python3
"""Run a real, cost-capped repeatability benchmark against claude-review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=50)
    parser.add_argument("--min-success-rate", type=float, default=0.95)
    parser.add_argument("--max-total-cost", type=float, required=True)
    args = parser.parse_args()
    if args.runs < 1 or args.runs > 200:
        raise SystemExit("--runs must be between 1 and 200")
    worst_case = args.runs * server.MAX_BUDGET_USD
    if worst_case > args.max_total_cost + 1e-9:
        raise SystemExit(f"Refusing benchmark: worst-case ${worst_case:.2f} exceeds --max-total-cost ${args.max_total_cost:.2f}")

    root = Path(__file__).resolve().parents[1]
    artifact = "tests/fixtures/benchmark.txt"
    pending: list[dict] = []
    completed: list[dict] = []
    next_run = 0
    started = time.monotonic()
    while len(completed) < args.runs:
        while next_run < args.runs and len(pending) < server.MAX_CONCURRENT_JOBS:
            job = server._start_job({
                "task": "读取唯一指定文件；若其中包含 CLAUDE_REVIEW_BENCHMARK_SENTINEL=4F2A9C7D，只返回 BENCHMARK_OK 4F2A9C7D。",
                "working_directory": str(root),
                "artifacts": [artifact],
            })
            pending.append(job)
            next_run += 1
        for job in list(pending):
            if job["done"].wait(timeout=0.1):
                completed.append(job)
                pending.remove(job)

    success = [job for job in completed if job["phase"] == "completed" and "BENCHMARK_OK 4F2A9C7D" in job.get("result", "")]
    cost = sum(float((job.get("metadata") or {}).get("total_cost_usd") or 0) for job in completed)
    result = {
        "recorded_at": server._utc_now(),
        "runs": args.runs,
        "successes": len(success),
        "success_rate": len(success) / args.runs,
        "total_cost_usd": round(cost, 6),
        "duration_seconds": round(time.monotonic() - started, 3),
        "model": server.MODEL or "inherited",
        "max_turns": server.MAX_TURNS,
        "per_run_budget_usd": server.MAX_BUDGET_USD,
        "failures": [{"phase": job["phase"], "error": job.get("error", ""), "metadata": job.get("metadata", {})} for job in completed if job not in success][:20],
    }
    (server.STATE_DIR / "benchmark-latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success_rate"] >= args.min_success_rate and cost <= args.max_total_cost else 1


if __name__ == "__main__":
    raise SystemExit(main())
