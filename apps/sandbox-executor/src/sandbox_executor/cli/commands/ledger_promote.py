from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys

from sandbox_executor.cli.options import add_target_worktree_option
from sandbox_executor.ledger.promote import PromotionRaceError, PromotionResult, promote
from sandbox_executor.ledger.promote_threshold import (
    DEFAULT_PROMOTE_EVERY,
    PROMOTE_EVERY_ENV,
    pending_root_intents,
    promote_every_from_env,
)

_HELP = (
    "Append pending ledger rows (holon-knowledge/ledger/pending/<ledger>/<root-intent-id>.jsonl) to the canonical "
    "ledgers, additions only, then delete the promoted fragments. Run it on a checkout with no active flows "
    "(normally main)."
)


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {value!r}")
    return number


def register_ledger_subcommand(ledger_subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = ledger_subparsers.add_parser("promote", help=_HELP, description=_HELP)
    parser.add_argument("--force", action="store_true", help="Promote now, whatever the root-intent threshold.")
    parser.add_argument(
        "--every",
        type=_positive_int,
        default=None,
        help=(
            "Minimum number of root intents with pending fragments before promoting "
            f"(default: {PROMOTE_EVERY_ENV}, else {DEFAULT_PROMOTE_EVERY})."
        ),
    )
    parser.add_argument("--check-only", action="store_true", help="Report the threshold state and change nothing.")
    parser.add_argument("--json", action="store_true", help="Print the promotion result as JSON.")
    add_target_worktree_option(parser, default=".")
    parser.set_defaults(_ledger_run=run_promote)


def _threshold_state(count: int, every: int) -> str:
    return f"{count} / {every} (threshold {'met' if count >= every else 'not met'})"


def _check_only(args: argparse.Namespace, every: int) -> int:
    count = len(pending_root_intents(args.target_worktree))
    if args.json:
        print(json.dumps({"root_intents": count, "every": every, "threshold_met": count >= every}))
    else:
        print(f"root intents with fragments: {_threshold_state(count, every)}")
    return 0


def _print_result(result: PromotionResult) -> None:
    if not (result.forced or result.threshold_met):
        print(f"below threshold, nothing promoted ({result.root_intents} / {result.every}); use --force to promote now")
        return
    for ledger_name, count in sorted(result.appended.items()):
        print(f"{ledger_name}: appended {count} row(s)")
    print(f"deleted {len(result.deleted)} promoted fragment(s)")


def run_promote(args: argparse.Namespace) -> int:
    try:
        every = args.every if args.every is not None else promote_every_from_env(os.environ)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if args.check_only:
        return _check_only(args, every)
    try:
        result = promote(args.target_worktree, force=args.force, every=every)
    except PromotionRaceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    for path in result.held:
        print(f"Warning: held malformed fragment {path}; it was neither promoted nor deleted", file=sys.stderr)
    if args.json:
        print(json.dumps(dataclasses.asdict(result)))
    else:
        _print_result(result)
    return 0
