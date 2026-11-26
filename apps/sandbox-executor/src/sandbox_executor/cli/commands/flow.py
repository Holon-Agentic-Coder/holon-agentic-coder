"""Flow command module for running the 5-stage Holon Flow lifecycle pipeline."""

from __future__ import annotations

import argparse
import sys

from sandbox_executor import cli
from sandbox_executor.cli.commands._flow_context import (
    _build_flow_context,
    _check_dry_run_and_preflight,
    _check_skip_push,
    _resolve_checkpoint_and_intent,
)
from sandbox_executor.cli.commands._flow_context import _update_flow_context as _update_flow_context
from sandbox_executor.cli.commands._flow_report import _calibrated_head_line as _calibrated_head_line
from sandbox_executor.cli.commands._flow_report import _report_flow_summary
from sandbox_executor.cli.commands._flow_report import _report_review_outcome as _report_review_outcome
from sandbox_executor.cli.commands._flow_worktree import (
    TARGET_WORKTREE_HELP,
    ensure_flow_worktree,
    redirect_default_branch_target,
)
from sandbox_executor.cli.options import (
    _AGENT_HELP,
    _MODEL_HELP,
    _SKIP_PREFLIGHT_HELP,
    _TOKEN_REDUCE_HELP,
    add_target_worktree_option,
)
from sandbox_executor.flow import FlowStage
from sandbox_executor.review_ensemble import ENSEMBLE_SIZE_HELP

ORDER = 60

_RUN_ONLY = "Applies to this run only; a resume does not inherit it from the checkpoint."

_FROM_STAGE_CHOICES = [s.value for s in FlowStage if s != FlowStage.COMPLETED]


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the flow subcommand parser."""
    parser = subparsers.add_parser(
        "flow",
        help="Run the automated 5-stage Holon Flow lifecycle pipeline.",
    )
    parser.add_argument(
        "intent_file",
        nargs="?",
        default=None,
        help="Path to local intent JSON file (e.g. intents/my-task.json)",
    )
    parser.add_argument(
        "--from-stage",
        choices=_FROM_STAGE_CHOICES,
        default=None,
        help=(
            "Resume pipeline from a specific stage. Without --checkpoint, loads the intent's checkpoint "
            "(.holon/flow/checkpoint-<slug>.json) from the target worktree when it exists."
        ),
    )
    parser.add_argument(
        "--checkpoint",
        default=None,
        help=(
            "Optional path to a flow checkpoint file. A --from-stage resume without it uses the intent's checkpoint in "
            "the target worktree when it exists."
        ),
    )
    parser.add_argument(
        "--agent",
        default=None,
        help=(
            f"{_AGENT_HELP} On a resume it applies to every remaining stage and role (the Stage 4 "
            "reviewers and resolver and the Stage 5 evaluating agent) and is recorded in the checkpoint; "
            "without it the checkpoint's runner is kept."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            f"{_MODEL_HELP} On a resume it applies to every remaining stage and role (the Stage 4 "
            "reviewers and resolver and the Stage 5 evaluating agent) and is recorded in the checkpoint; "
            "without it the checkpoint's model is kept unless --agent switches the runner."
        ),
    )
    parser.add_argument("--skip-preflight", action="store_true", help=_SKIP_PREFLIGHT_HELP)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=f"Validate stages and branches without executing external agents or git commits. {_RUN_ONLY}",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output pipeline status and stage results in structured JSON format to stdout.",
    )
    add_target_worktree_option(parser, default=".", help=TARGET_WORKTREE_HELP)
    parser.add_argument(
        "--skip-push",
        action="store_true",
        help=(
            "Skip pushing git branches to remote origin. Only valid with --dry-run: the intent, plan and "
            "execute stages dispatch role agents that fetch from origin, the review stage reviews the origin "
            "execution branch and the calibrate stage pushes to origin, so any non-dry run, including a "
            f"--from-stage or --checkpoint resume, is rejected before any stage runs. {_RUN_ONLY}"
        ),
    )
    parser.add_argument(
        "--skip-calibrate",
        action="store_true",
        help=f"Skip Stage 5 (post-execution calibration report generation). {_RUN_ONLY}",
    )
    parser.add_argument(
        "--allow-overlap",
        action="store_true",
        help=(
            "Admit Stage 3 even when the plan's touch set overlaps a flow in flight (an open flow pull request or a "
            "local flow at execute or review); the override is recorded in executions.jsonl on the plan branch."
        ),
    )
    parser.add_argument(
        "--max-review-iterations",
        type=int,
        default=25,
        help="Maximum number of autonomous review loop iterations in Stage 4 (must be >= 1, default: 25).",
    )
    parser.add_argument(
        "--ensemble-size",
        type=int,
        default=None,
        help=ENSEMBLE_SIZE_HELP,
    )
    parser.add_argument(
        "--token-reduce",
        action="store_true",
        help=_TOKEN_REDUCE_HELP,
    )
    parser.add_argument(
        "--mitm-web",
        action="store_true",
        help=(
            "Launch mitmweb dashboard on port 8081 (configurable via HOLON_MITM_WEB_PORT) "
            "for real-time traffic inspection. Automatically enables --token-reduce."
        ),
    )
    parser.set_defaults(_run=run)


def _validate_flow_args(args: argparse.Namespace) -> bool:
    if getattr(args, "max_review_iterations", None) is not None and args.max_review_iterations < 1:
        print("Error: --max-review-iterations must be at least 1.", file=sys.stderr)
        sys.exit(1)
        return False
    if getattr(args, "ensemble_size", None) is not None and args.ensemble_size < 1:
        print("Error: --ensemble-size must be at least 1.", file=sys.stderr)
        sys.exit(1)
        return False
    return True


def run(args: argparse.Namespace) -> int | None:
    """Execute the flow command."""
    try:
        if not _validate_flow_args(args):
            return None

        resolved = _resolve_checkpoint_and_intent(args)
        if resolved is None:
            return None
        context, intent_data, flow_agent, flow_model = resolved

        if not _check_skip_push(args, context) or not ensure_flow_worktree(args, context, intent_data):
            return None

        if not _check_dry_run_and_preflight(args, context, flow_agent, flow_model):
            return None
        if not redirect_default_branch_target(args, context, intent_data):
            return None

        context = _build_flow_context(args, context, intent_data, flow_agent, flow_model)
        result_dict = cli.run_flow_pipeline(
            context=context,
            from_stage=args.from_stage,
            checkpoint_path=args.checkpoint,
        )
        _report_flow_summary(context, result_dict, bool(args.json))

        has_failure = any(res.status.value == "failed" for res in context.stage_results.values())
        sys.exit(1 if has_failure else 0)
    except Exception as e:
        print(f"Error during flow execution: {cli.redact_cli_error(e)}", file=sys.stderr)
        sys.exit(1)
    return None
