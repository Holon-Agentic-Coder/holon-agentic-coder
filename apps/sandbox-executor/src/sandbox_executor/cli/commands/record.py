from __future__ import annotations

import argparse
import os

from sandbox_executor import cli
from sandbox_executor.cli.options import add_target_worktree_option

ORDER = 110
_DEFAULT_LEDGER_RELATIVE_PATH = "holon-knowledge/ledger/executions.jsonl"


def _parse_csv_labels(raw_labels: str | None) -> list[str]:
    if not raw_labels:
        return []
    return [lbl.strip() for lbl in raw_labels.split(",") if lbl.strip()]


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "record",
        help="Record a discrete action event to the ledger with credential scrubbing.",
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
        default=_DEFAULT_LEDGER_RELATIVE_PATH,
        help="Path to actions ledger JSONL file.",
    )
    add_target_worktree_option(parser, default=".")
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    ledger_path = args.ledger_path
    if not os.path.isabs(ledger_path):
        ledger_path = os.path.join(args.target_worktree, ledger_path)
    record_data = {
        "action": args.action,
        "category": args.category,
        "target": args.target,
        "verdict": args.verdict,
        "labels": _parse_csv_labels(args.labels),
        "error_context": args.context,
    }
    res = cli.append_action_event(record_data, ledger_path=ledger_path)
    print(f"Recorded action: {res['action_id']} [{res['verdict']}]")
    return 0
