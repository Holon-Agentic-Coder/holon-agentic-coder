"""Promote command module for evaluating and promoting knowledge candidates."""

from __future__ import annotations

import argparse
import os

from sandbox_executor import cli

ORDER = 130


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the promote subcommand parser."""
    parser = subparsers.add_parser(
        "promote",
        help="Evaluate and promote qualified knowledge to KB and WB (Bean 0052).",
    )
    parser.add_argument(
        "--pending",
        action="store_true",
        help="Promote all qualified pending candidates.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate promotion without persisting files.",
    )
    parser.add_argument(
        "--min-trials",
        type=int,
        default=2,
        help="Minimum execution trials required for promotion consideration (default: 2).",
    )
    parser.add_argument(
        "--repo-dir",
        default=".",
        help="Repository root directory.",
    )
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    """Execute the promote command."""
    db_path = os.path.join(args.repo_dir, "holon-knowledge/.index/action_index.db")
    db = cli.ActionIndexDatabase(db_path=db_path)
    promoted = cli.promote_pending_candidates(
        index_db=db,
        kb_dir=os.path.join(args.repo_dir, "holon-knowledge/kb"),
        wb_dir=os.path.join(args.repo_dir, "holon-knowledge/wisdom"),
        min_trials=args.min_trials,
        dry_run=args.dry_run,
    )
    mode = "Dry-run: candidate" if args.dry_run else "Promoted"
    print(f"{mode} knowledge items: {len(promoted)}")
    for item in promoted:
        item_id = item.get("kb_id") or item.get("wb_id")
        item_type = item.get("kb_type") or item.get("wb_type")
        name = item.get("payload", {}).get("name", "")
        print(f" - {item_id} [{item_type}]: {name}")
    return 0
