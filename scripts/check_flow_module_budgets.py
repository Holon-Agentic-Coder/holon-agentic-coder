#!/usr/bin/env python3
"""Size ratchet for the flow engine: fail when a sandbox_executor.flow module grows past its recorded line budget.

Budgets live in apps/sandbox-executor/flow_module_budgets.json, recorded at the post-split sizes. A module may shrink,
never grow; lowering a budget is a deliberate manual edit, so there is no update flag. A new module needs a budget
entry recorded at its creation size, and a deleted module's entry must be removed with it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PACKAGE_DIR = REPO_ROOT / "apps" / "sandbox-executor" / "src" / "sandbox_executor" / "flow"
DEFAULT_BUDGETS = REPO_ROOT / "apps" / "sandbox-executor" / "flow_module_budgets.json"


def module_line_counts(package_dir: Path) -> dict[str, int]:
    """Return the line count of every ``.py`` file under ``package_dir``, keyed by its POSIX path inside it."""
    return {
        path.relative_to(package_dir).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted(package_dir.rglob("*.py"))
    }


def budget_violations(line_counts: dict[str, int], budgets: dict[str, int]) -> list[str]:
    """Return one message per module over budget, per module without a budget and per budget without a module."""
    violations = [
        f"{module}: {lines} lines exceeds its budget of {budgets[module]}"
        for module, lines in line_counts.items()
        if module in budgets and lines > budgets[module]
    ]
    violations += [
        f"{module}: no budget entry; record its creation size ({lines} lines)"
        for module, lines in line_counts.items()
        if module not in budgets
    ]
    violations += [
        f"{module}: budget entry names a missing module; remove it" for module in budgets if module not in line_counts
    ]
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the sandbox_executor.flow per-module line budgets.")
    parser.add_argument("--package-dir", type=Path, default=DEFAULT_PACKAGE_DIR, help="Flow package directory")
    parser.add_argument("--budgets", type=Path, default=DEFAULT_BUDGETS, help="Per-module budget JSON file")
    args = parser.parse_args(argv)

    budgets = json.loads(args.budgets.read_text(encoding="utf-8"))["budgets"]
    violations = budget_violations(module_line_counts(args.package_dir), budgets)
    for violation in violations:
        print(f"::error::flow module budget: {violation}")
    if violations:
        return 1
    print(f"Flow module budgets passed: {len(budgets)} modules within their recorded line counts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
