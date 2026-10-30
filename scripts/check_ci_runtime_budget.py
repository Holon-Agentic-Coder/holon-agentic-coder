#!/usr/bin/env python3
"""Check CI runtime budget against maximum allowed seconds."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check CI job runtime budget.")
    parser.add_argument("--start-epoch", type=float, required=True, help="Job start epoch timestamp")
    parser.add_argument("--max-seconds", type=float, default=300.0, help="Maximum allowed seconds (default: 300)")
    parser.add_argument("--step-summary", type=str, default="", help="Path to GITHUB_STEP_SUMMARY")
    args = parser.parse_args(argv)

    now = time.time()
    elapsed = max(0.0, now - args.start_epoch)

    passed = elapsed <= args.max_seconds
    status_str = "PASSED" if passed else "FAILED"
    summary_lines = [
        f"### CI Runtime Budget Check: {status_str}",
        "",
        f"- **Elapsed Time:** {elapsed:.1f} s",
        f"- **Max Allowed:** {args.max_seconds:.1f} s",
    ]

    if args.step_summary:
        summary_path = Path(args.step_summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with summary_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(summary_lines) + "\n")

    # Record compatibility record if ci-timing directory is used
    record_file = Path("ci-timing/record.json")
    try:
        record_file.parent.mkdir(parents=True, exist_ok=True)
        record_data = {
            "duration_seconds": round(elapsed, 1),
            "max_seconds": args.max_seconds,
            "passed": passed,
            "timestamp": now,
        }
        record_file.write_text(json.dumps(record_data, indent=2), encoding="utf-8")
    except OSError:
        pass

    if not passed:
        print(f"::error::CI runtime budget exceeded: {elapsed:.1f}s > {args.max_seconds:.1f}s")
        return 1

    print(f"CI runtime budget check passed: {elapsed:.1f}s <= {args.max_seconds:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
