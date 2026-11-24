from __future__ import annotations

import argparse
import os

from sandbox_executor import cli
from sandbox_executor.cli.options import add_target_worktree_option

ORDER = 120
_DEFAULT_EXPIRE_TTL_DAYS = 14
_DEFAULT_LEDGER_RELATIVE_PATH = "holon-knowledge/ledger/executions.jsonl"


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "reconcile",
        help="Reconcile pending action outcomes and expire stale unknown records.",
    )
    parser.add_argument(
        "--action-id",
        default=None,
        help="Target action ID to reconcile.",
    )
    parser.add_argument(
        "--verdict",
        default=None,
        help="New terminal verdict (success, failure, superseded).",
    )
    parser.add_argument(
        "--reason",
        default=None,
        help="Reason for late verdict reconciliation.",
    )
    parser.add_argument(
        "--expire-ttl",
        type=int,
        default=_DEFAULT_EXPIRE_TTL_DAYS,
        help="Days after which unknown actions expire to unresolved (default: 14).",
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
    rec_stats = cli.reconcile_records(
        ledger_path=ledger_path,
        max_age_days=args.expire_ttl,
        target_action_id=args.action_id,
        verdict=args.verdict,
        reason=args.reason,
    )
    print(
        f"Reconciled: {rec_stats['reconciled']}, Expired: {rec_stats['expired']}, Scanned: {rec_stats['total_scanned']}"
    )
    return 0
