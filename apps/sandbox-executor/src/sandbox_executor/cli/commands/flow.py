"""Flow command module for running the 5-stage Holon Flow lifecycle pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

from sandbox_executor import cli
from sandbox_executor.agent_defaults import Resolution
from sandbox_executor.cli.options import (
    _AGENT_HELP,
    _MODEL_HELP,
    _SKIP_PREFLIGHT_HELP,
    _TOKEN_REDUCE_HELP,
)
from sandbox_executor.flow import FlowContext, FlowStage
from sandbox_executor.review_ensemble import DEFAULT_ENSEMBLE_SIZE, ENSEMBLE_SIZE_HELP

logger = logging.getLogger(__name__)

ORDER = 60

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
        help="Resume pipeline from a specific stage.",
    )
    parser.add_argument(
        "--checkpoint",
        default=None,
        help="Optional path to a flow checkpoint file.",
    )
    parser.add_argument(
        "--agent", default=None, help=f"{_AGENT_HELP} A --checkpoint resume keeps the checkpoint's runner by default."
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"{_MODEL_HELP} A --checkpoint resume keeps the checkpoint's model unless --agent switches the runner.",
    )
    parser.add_argument("--skip-preflight", action="store_true", help=_SKIP_PREFLIGHT_HELP)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate stages and branches without executing external agents or git commits.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output pipeline status and stage results in structured JSON format to stdout.",
    )
    parser.add_argument(
        "--repo-dir",
        default=".",
        help="Target repository directory path (default: current directory).",
    )
    parser.add_argument(
        "--skip-push",
        action="store_true",
        help=(
            "Skip pushing git branches to remote origin. Only valid with --dry-run: the intent, plan and "
            "execute stages dispatch role agents that fetch from origin, the review stage reviews the origin "
            "execution branch and the calibrate stage pushes to origin, so any non-dry run, including a "
            "--from-stage or --checkpoint resume, is rejected before any stage runs."
        ),
    )
    parser.add_argument(
        "--skip-review",
        action="store_true",
        help="Skip Stage 4 (review loop) and proceed to calibration.",
    )
    parser.add_argument(
        "--skip-calibrate",
        action="store_true",
        help="Skip Stage 5 (post-execution calibration report generation).",
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


def _resolve_checkpoint_and_intent(
    args: argparse.Namespace,
) -> tuple[Any | None, dict[str, Any], str, str] | None:
    context = None
    if args.checkpoint:
        if not os.path.exists(args.checkpoint):
            print(f"Error: Checkpoint file '{args.checkpoint}' does not exist.", file=sys.stderr)
            sys.exit(1)
            return None
        checkpoint = cli.load_checkpoint(args.checkpoint)
        context = checkpoint.context
        if not args.from_stage and checkpoint.current_stage != FlowStage.COMPLETED:
            args.from_stage = checkpoint.current_stage.value

    if context is None:
        intent_data = {}
        if args.intent_file:
            if not os.path.exists(args.intent_file):
                print(f"Error: Intent file '{args.intent_file}' does not exist.", file=sys.stderr)
                sys.exit(1)
                return None
            with open(args.intent_file, encoding="utf-8") as f:
                intent_data = json.load(f)
        agent_res, model_res = cli.resolve_runner_and_model(args.agent, args.model)
        flow_agent, flow_model = agent_res.value, model_res.value
    else:
        from_checkpoint = f"recorded in {args.checkpoint}"
        if args.agent is not None:
            flow_agent = cli.resolve_agent_runner(args.agent).value
        else:
            flow_agent = context.agent
            logger.info(Resolution(flow_agent, "checkpoint", from_checkpoint).describe("Agent runner"))
        if args.model is not None or (
            args.agent is not None and cli.normalise_agent_id(flow_agent) != cli.normalise_agent_id(context.agent)
        ):
            flow_model = cli.resolve_model(flow_agent, args.model).value
        else:
            flow_model = context.model
            logger.info(Resolution(flow_model, "checkpoint", from_checkpoint).describe("Model"))
        intent_data = {}

    return context, intent_data, flow_agent, flow_model


def _check_dry_run_and_preflight(
    args: argparse.Namespace,
    context: Any | None,
    flow_agent: str,
    flow_model: str,
) -> bool:
    effective_dry_run = bool(args.dry_run or (context is not None and context.dry_run))
    if (args.skip_push or (context is not None and context.skip_push)) and not effective_dry_run:
        print(f"Error: {cli.SKIP_PUSH_DISPATCH_ERROR}", file=sys.stderr)
        sys.exit(1)
        return False

    if effective_dry_run:
        logger.info("Pre-flight probe skipped for a dry-run flow (no agent is called).")
    elif not cli.flow_dispatches_agent(args.from_stage):
        logger.info(
            "Pre-flight probe skipped: no remaining stage from '%s' dispatches an agent runner.",
            args.from_stage,
        )
    else:
        preflight = cli.preflight_gate(flow_agent, flow_model, skip=args.skip_preflight)
        if preflight is not None and not preflight.ok:
            sys.exit(1)
            return False
    return True


def _update_flow_context(
    context: Any,
    args: argparse.Namespace,
    flow_agent: str,
    flow_model: str,
    effective_token_reduce: bool,
) -> None:
    if args.repo_dir and args.repo_dir != ".":
        context.repo_dir = args.repo_dir
    context.agent = flow_agent
    context.model = flow_model
    if args.dry_run:
        context.dry_run = True
    if args.skip_push:
        context.skip_push = True
    if args.skip_review:
        context.skip_review = True
    if args.skip_calibrate:
        context.skip_calibrate = True
        context.auto_calibrate = False
    if args.max_review_iterations is not None:
        context.max_review_iterations = args.max_review_iterations
    if args.ensemble_size is not None:
        context.review_ensemble_size = args.ensemble_size
    if effective_token_reduce:
        context.token_reduce = True
    if args.mitm_web:
        context.mitm_web = True


def _build_flow_context(
    args: argparse.Namespace,
    context: Any | None,
    intent_data: dict[str, Any],
    flow_agent: str,
    flow_model: str,
) -> Any:
    effective_token_reduce = cli.detect_holon_coherence(bool(args.token_reduce or args.mitm_web))
    if context is None:
        return FlowContext(
            repo_dir=args.repo_dir,
            intent_data=intent_data,
            agent=flow_agent,
            model=flow_model,
            dry_run=args.dry_run,
            skip_push=args.skip_push,
            skip_review=args.skip_review,
            skip_calibrate=args.skip_calibrate,
            auto_calibrate=not args.skip_calibrate,
            max_review_iterations=args.max_review_iterations,
            review_ensemble_size=args.ensemble_size or DEFAULT_ENSEMBLE_SIZE,
            token_reduce=effective_token_reduce,
            mitm_web=args.mitm_web,
        )
    _update_flow_context(context, args, flow_agent, flow_model, effective_token_reduce)
    return context


def _report_flow_summary(context: Any, result_dict: dict[str, Any], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result_dict, indent=2))
        return

    print("\n================== Holon Flow Summary ==================")
    stage_order = ["intent", "plan", "execute", "review", "calibrate"]
    for st in stage_order:
        res = context.stage_results.get(st)
        print(f"  {res.summary_line()}" if res else f"  {st}: not run")
        if res and res.status.value == "failed" and res.error:
            print(f"    -> Error: {cli.redact_cli_error(res.error)}")
    print("========================================================\n")
    decomposition_lines = cli.format_decomposition_summary(context.stage_results.get("execute"))
    for line in decomposition_lines:
        print(line)
    if decomposition_lines:
        print()
    review_res = context.stage_results.get("review")
    if review_res and review_res.payload.get("halted_for_human"):
        print("[BEAN 0034 SAFETY HALT] Pipeline safely halted for human review (autonomous merge prohibited).")
        if review_res.payload.get("human_approval_command"):
            print(f"To approve manually, run: {review_res.payload['human_approval_command']}\n")


def run(args: argparse.Namespace) -> int | None:
    """Execute the flow command."""
    try:
        if not _validate_flow_args(args):
            return None

        resolved = _resolve_checkpoint_and_intent(args)
        if resolved is None:
            return None
        context, intent_data, flow_agent, flow_model = resolved

        if not _check_dry_run_and_preflight(args, context, flow_agent, flow_model):
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
