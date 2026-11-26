"""``holon intent-check``: a read-only check of a draft intent's scope before the intent is launched."""

from __future__ import annotations

import argparse
import json
import os
import sys

from sandbox_executor.cli.options import add_target_worktree_option
from sandbox_executor.intent_scope_check import check_intent_scope, load_intent, render_text, to_json

ORDER = 11
_INVALID_INPUT_EXIT_CODE = 2


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "intent-check",
        help=(
            "Check a draft intent's scope without changing anything: report missing paths, propose companion files "
            "and warn about files no acceptance criterion names. Mark a file the intent creates with a trailing "
            "' (new)' or a leading 'new:'. Exits 1 when companions are proposed, 0 otherwise."
        ),
    )
    parser.add_argument("intent_file", help="Path to the draft intent JSON file (e.g. intents/my-task.json)")
    add_target_worktree_option(parser, default=None)
    parser.add_argument("--json", action="store_true", help="Print the report as JSON (missing, proposed, warnings).")
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    worktree = args.target_worktree or os.getcwd()
    try:
        report = check_intent_scope(load_intent(args.intent_file), worktree)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return _INVALID_INPUT_EXIT_CODE
    print(json.dumps(to_json(report), indent=2) if args.json else render_text(report))
    return 1 if report.has_proposals else 0
