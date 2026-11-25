from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import sys
from typing import Any

from sandbox_executor import cli
from sandbox_executor.calibration import CalibrationReport
from sandbox_executor.cli.commands._worktree_guard import DEFAULT_BRANCH_GUARD_HELP, default_branch_worktree_allowed
from sandbox_executor.cli.options import add_target_worktree_option
from sandbox_executor.flow.stage5 import Stage5Outcome, run_stage5, standalone_context
from sandbox_executor.flow.stage5_verdict import resolve_execution_verdict
from sandbox_executor.flow.stages.calibrate import stage5_steps

ORDER = 50


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "calibrate",
        help=(
            "Run Stage 5 (calibration report, execution reconciliation in executions.jsonl, knowledge/wisdom "
            "promotion) on an execution branch, committing each step on its /calibrated branch."
        ),
    )
    parser.add_argument(
        "execution_branch",
        help="Target execution branch name (e.g. I-.../P-.../E-.../_)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "Print the calibration report plus a stage5 object (mode, reconciliation outcome, newly_promoted_count) "
            "as one JSON document on stdout."
        ),
    )
    add_target_worktree_option(parser, default=".", help=DEFAULT_BRANCH_GUARD_HELP)
    parser.add_argument(
        "--no-commit",
        dest="skip_commit",
        action="store_true",
        help=(
            "Create no branch or commit for any step; the report stays in the working tree and no ledger rows are "
            "appended."
        ),
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help=(
            "Only generate the calibration report (the old behaviour); skip the execution reconciliation and "
            "knowledge/wisdom promotion."
        ),
    )
    parser.set_defaults(_run=run)


def _run_stage5(args: argparse.Namespace) -> Stage5Outcome:
    context = standalone_context(args.execution_branch, args.target_worktree, no_commit=args.skip_commit)
    report_only = bool(getattr(args, "report_only", False))
    verdict = None if report_only else resolve_execution_verdict(args.target_worktree, args.execution_branch)

    def invoke_cli_calibrate_without_json(**kwargs: Any) -> CalibrationReport:
        return cli.run_calibrate(
            execution_branch=kwargs["execution_branch"],
            target_worktree=kwargs["target_worktree"],
            json_output=False,
            skip_commit=kwargs["skip_commit"],
        )

    steps = dataclasses.replace(stage5_steps(), calibrate=invoke_cli_calibrate_without_json)
    return run_stage5(context, steps, verdict, report_only)


def _run_stage5_redirecting_stdout_to_stderr(args: argparse.Namespace) -> Stage5Outcome:
    with contextlib.redirect_stdout(sys.stderr):
        return _run_stage5(args)


def _print_outcome(outcome: Stage5Outcome) -> None:
    if outcome.report_only:
        print("Report only: Stage 5 reconciliation and knowledge/wisdom promotion were skipped (--report-only).")
        return
    reconciliation = outcome.reconciliation
    print(f"Stage 5 reconciliation: {reconciliation['status']} (verdict: {reconciliation['verdict'] or 'none'})")
    print(f"Promoted {outcome.newly_promoted_count} new knowledge/wisdom candidate(s).")


def run(args: argparse.Namespace) -> int:
    try:
        if not default_branch_worktree_allowed(args.target_worktree, "holon calibrate"):
            return 1
        if not args.json:
            _print_outcome(_run_stage5(args))
            return 0
        outcome = _run_stage5_redirecting_stdout_to_stderr(args)
        print(json.dumps({**outcome.report_dict, "stage5": outcome.to_json_dict()}, indent=2))
        return 0
    except Exception as e:
        print(f"Error during calibration: {cli.redact_cli_error(e)}", file=sys.stderr)
        return 1
