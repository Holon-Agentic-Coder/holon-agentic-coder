"""Record command module for logging action events into the execution ledger."""

from __future__ import annotations

import argparse
import os

from sandbox_executor import cli

ORDER = 110


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the record subcommand parser."""
    parser = subparsers.add_parser(
        "record",
        help="Record a discrete action event to the ledger with credential scrubbing (Bean 0052).",
    )
    parser.add_argument(
        "--action",
        required=True,
        help="Description of the action performed.",
    )
    parser.add_argument(
        "--category",
        default="code",
        help="Action category (code, doc, config, test, build, dependency).",
    )
    parser.add_argument(
        "--target",
        default="",
        help="Action target or affected component.",
    )
    parser.add_argument(
        "--verdict",
        default="attempted",
        help="Action outcome verdict (attempted, success, failure, unknown, inconclusive, abandoned).",
    )
    parser.add_argument(
        "--labels",
        default=None,
        help="Comma-separated labels associated with this action.",
    )
    parser.add_argument(
        "--context",
        default=None,
        help="Error context or execution details.",
    )
    parser.add_argument(
        "--ledger-path",
        default="holon-knowledge/ledger/executions.jsonl",
        help="Path to actions ledger JSONL file.",
    )
    parser.add_argument(
        "--repo-dir",
        default=".",
        help="Repository root directory.",
    )
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    """Execute the record command."""
    ledger_path = args.ledger_path
    if not os.path.isabs(ledger_path):
        ledger_path = os.path.join(args.repo_dir, ledger_path)
    labels_list = [lbl.strip() for lbl in args.labels.split(",") if lbl.strip()] if args.labels else []
    record_data = {
        "action": args.action,
        "category": args.category,
        "target": args.target,
        "verdict": args.verdict,
        "labels": labels_list,
        "error_context": args.context,
    }
    res = cli.append_action_event(record_data, ledger_path=ledger_path)
    print(f"Recorded action: {res['action_id']} [{res['verdict']}]")
    return 0
