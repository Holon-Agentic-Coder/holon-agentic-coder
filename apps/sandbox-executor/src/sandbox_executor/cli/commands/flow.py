"""Flow command module for running the 5-stage Holon Flow lifecycle pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Mapping
from typing import Any

from sandbox_executor import cli
from sandbox_executor.cli.commands._flow_resume import (
    apply_proxy_switches,
    apply_run_switches,
    discover_resume_checkpoint,
    resolve_resumed_runner,
)
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
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus, format_final_head_ci
from sandbox_executor.launcher import coherence
from sandbox_executor.review_ensemble import DEFAULT_ENSEMBLE_SIZE, ENSEMBLE_SIZE_HELP

logger = logging.getLogger(__name__)

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


def _resolve_checkpoint_and_intent(
    args: argparse.Namespace,
) -> tuple[Any | None, dict[str, Any], str, str] | None:
    context = None
    discovered = discover_resume_checkpoint(args)
    if discovered is not None:
        logger.info("Resuming from checkpoint %s (found for --from-stage %s).", discovered, args.from_stage)
        args.checkpoint = discovered
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
        flow_agent, flow_model = resolve_resumed_runner(args, context, args.checkpoint)
        apply_run_switches(context, args)
        intent_data = {}

    return context, intent_data, flow_agent, flow_model


def _check_skip_push(args: argparse.Namespace, context: Any | None) -> bool:
    """Reject --skip-push outside a dry run, before any other gate.

    A resumed ``context`` already carries this run's switches (a stored skip was dropped), so it is the source of truth.
    """
    effective_dry_run = bool(context.dry_run if context is not None else args.dry_run)
    effective_skip_push = bool(context.skip_push if context is not None else args.skip_push)
    if effective_skip_push and not effective_dry_run:
        print(f"Error: {cli.SKIP_PUSH_DISPATCH_ERROR}", file=sys.stderr)
        sys.exit(1)
        return False
    return True


def _check_dry_run_and_preflight(
    args: argparse.Namespace,
    context: Any | None,
    flow_agent: str,
    flow_model: str,
) -> bool:
    effective_dry_run = bool(context.dry_run if context is not None else args.dry_run)
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
    proxy_choice: coherence.FlowProxyChoice,
) -> None:
    """Apply this run's flags to a resumed context.

    The proxy routing is overwritten from this run's ``proxy_choice``, so an opt-out wins and a stale checkpointed
    ``token_reduce=True`` or ``mitm_web=True`` no longer spawns a sidecar: a resumed run must repeat
    ``--token-reduce`` or ``--mitm-web`` to keep one, and each dropped opt-in is logged.
    """
    target_worktree = getattr(args, "target_worktree", None)
    if target_worktree and target_worktree != ".":
        context.target_worktree = target_worktree
    context.agent = flow_agent
    context.model = flow_model
    apply_run_switches(context, args)
    context.allow_overlap = bool(context.allow_overlap or getattr(args, "allow_overlap", False))
    if args.max_review_iterations is not None:
        context.max_review_iterations = args.max_review_iterations
    if args.ensemble_size is not None:
        context.review_ensemble_size = args.ensemble_size
    apply_proxy_switches(
        context,
        proxy_choice.spawn_sidecar,
        proxy_choice.attach_running_proxy,
        bool(args.mitm_web) and proxy_choice.spawn_sidecar,
        (bool(args.token_reduce or args.mitm_web), bool(args.mitm_web)),
    )


def _build_flow_context(
    args: argparse.Namespace,
    context: Any | None,
    intent_data: dict[str, Any],
    flow_agent: str,
    flow_model: str,
) -> Any:
    proxy_choice = coherence.resolve_flow_proxy_choice(bool(args.token_reduce or args.mitm_web))
    target_worktree = getattr(args, "target_worktree", ".")
    kw = {"target_worktree": target_worktree}
    if context is None:
        return FlowContext(
            intent_data=intent_data,
            agent=flow_agent,
            model=flow_model,
            dry_run=args.dry_run,
            skip_push=args.skip_push,
            skip_calibrate=args.skip_calibrate,
            auto_calibrate=not args.skip_calibrate,
            max_review_iterations=args.max_review_iterations,
            review_ensemble_size=args.ensemble_size or DEFAULT_ENSEMBLE_SIZE,
            token_reduce=proxy_choice.spawn_sidecar,
            proxy_attach=proxy_choice.attach_running_proxy,
            mitm_web=bool(args.mitm_web) and proxy_choice.spawn_sidecar,
            allow_overlap=bool(getattr(args, "allow_overlap", False)),
            **kw,
        )
    _update_flow_context(context, args, flow_agent, flow_model, proxy_choice)
    return context


def _calibrated_head_line(calibrate_result: Any) -> str:
    """Name the execution head the calibration report covers, or say it is stale or was not refreshed."""
    payload = calibrate_result.payload
    head = payload.get("calibrated_head")
    if payload.get("calibration_stale"):
        stale = payload.get("stale_reason") or "the pull request head moved after calibration"
        covers = f"covers {head[:12]}, " if head else ""
        return f"calibration report is STALE: {covers}{stale} — not ready to merge on this report"
    if head:
        return f"calibration report covers execution head {head[:12]}"
    return "calibration report not refreshed for the current head"


def _report_flow_summary(context: Any, result_dict: dict[str, Any], json_output: bool) -> None:
    result_dict.setdefault("target_worktree", context.target_worktree)
    if json_output:
        print(json.dumps(result_dict, indent=2))
        return

    print("\n================== Holon Flow Summary ==================")
    print(f"  target worktree: {context.target_worktree}")
    stage_order = ["intent", "plan", "execute", "review", "calibrate"]
    for st in stage_order:
        res = context.stage_results.get(st)
        print(f"  {res.summary_line()}" if res else f"  {st}: not run")
        if res and res.status.value == "failed" and res.error:
            print(f"    -> Error: {cli.redact_cli_error(res.error)}")
        if st == "calibrate" and res:
            print(f"    {_calibrated_head_line(res)}")
        final_head_ci = res.payload.get("final_head_ci") if res else None
        if st == "calibrate" and final_head_ci and final_head_ci.get("state") != "skipped":
            print(f"    {format_final_head_ci(final_head_ci)}")
    print("========================================================\n")
    decomposition_lines = cli.format_decomposition_summary(context.stage_results.get("execute"))
    for line in decomposition_lines:
        print(line)
    if decomposition_lines:
        print()
    has_failure = any(res.status == StageStatus.FAILED for res in context.stage_results.values())
    _report_review_outcome(context.stage_results.get("review"), has_failure=has_failure)


def _report_review_outcome(review_res: StageResult | None, *, has_failure: bool = False) -> None:
    if not review_res:
        return
    raw_consensus = review_res.payload.get("consensus")
    consensus = raw_consensus if isinstance(raw_consensus, Mapping) else {}
    is_halt = review_res.status == StageStatus.HALTED_FOR_HUMAN or bool(review_res.payload.get("halted_for_human"))
    is_approved = review_res.status == StageStatus.APPROVED or (is_halt and bool(consensus.get("approved")))
    if is_approved:
        if not has_failure:
            print(
                "[REVIEW APPROVED] Review Loop reached consensus approval; "
                "the PR is ready for the maintainer to merge (the flow never merges to main).\n"
            )
    elif is_halt:
        print("[SAFETY HALT] Pipeline safely halted for human review (autonomous merge prohibited).")
        cmd = review_res.payload.get("human_approval_command")
        if cmd:
            print(f"To approve manually, run: {cmd}\n")


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
