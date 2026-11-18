"""Migrate-plans command module: the one-off nested-plan-path migration (thin CLI over ``plan_migration``)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sandbox_executor.plan_migration import MigrationError, migrate_plans

ORDER = 95


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the migrate-plans subcommand parser."""
    parser = subparsers.add_parser(
        "migrate-plans",
        help=(
            "Move every top-level plans/ file to its branch path (plans/{intent}/) and rewrite only plan_file and "
            "calibration_report_path in the ledgers. Dry run by default; repeatable and a no-op on a migrated tree."
        ),
    )
    parser.add_argument("--repo-root", default=".", help="Repository root (default: current directory).")
    parser.add_argument(
        "--pr-branches",
        default=None,
        help="File listing pull-request head branch names, one per line, for files with no ledger row.",
    )
    parser.add_argument("--apply", action="store_true", help="Perform the moves and ledger rewrites (default: dry run).")
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    """Execute the migrate-plans command and print its full report."""
    pr_branches: list[str] = []
    if args.pr_branches:
        text = Path(args.pr_branches).read_text(encoding="utf-8")
        pr_branches = [line.strip() for line in text.splitlines() if line.strip()]
    try:
        report = migrate_plans(args.repo_root, pr_branches, apply=args.apply)
    except MigrationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(report.render(applied=args.apply))
    return 0
