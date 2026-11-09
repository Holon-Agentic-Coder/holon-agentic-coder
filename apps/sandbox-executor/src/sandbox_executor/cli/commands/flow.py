"""Flow command module for running the 5-stage Holon Flow lifecycle pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from typing import Any

from sandbox_executor import cli
from sandbox_executor.agent_defaults import Resolution
from sandbox_executor.cli.options import (
    _AGENT_HELP,
    _MODEL_HELP,
    _SKIP_PREFLIGHT_HELP,
    _TOKEN_REDUCE_HELP,
    add_target_worktree_option,
)
from sandbox_executor.flow import FlowContext, FlowStage
from sandbox_executor.git_flow import (
    ensure_worktree,
    is_bare_repository,
    is_working_tree,
    target_worktree_problem,
)
from sandbox_executor.review_ensemble import DEFAULT_ENSEMBLE_SIZE, ENSEMBLE_SIZE_HELP

logger = logging.getLogger(__name__)

ORDER = 60

_FROM_STAGE_CHOICES = [s.value for s in FlowStage if s != FlowStage.COMPLETED]

_CHECKPOINT_TARGET_HINT = ", or pass --target-worktree to resume against a different attached worktree"


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
    add_target_worktree_option(parser, default=".")
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


def _check_skip_push(args: argparse.Namespace, context: Any | None) -> bool:
    """Reject --skip-push (given or recorded in the checkpoint) outside a dry run, before any other gate."""
    effective_dry_run = bool(args.dry_run or (context is not None and context.dry_run))
    if (args.skip_push or (context is not None and context.skip_push)) and not effective_dry_run:
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
    effective_dry_run = bool(args.dry_run or (context is not None and context.dry_run))
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
    target_worktree = getattr(args, "target_worktree", None)
    if target_worktree and target_worktree != ".":
        context.target_worktree = target_worktree
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
    target_worktree = getattr(args, "target_worktree", ".")
    kw = {"target_worktree": target_worktree}
    if context is None:
        return FlowContext(
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
            **kw,
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
        print("[SAFETY HALT] Pipeline safely halted for human review (autonomous merge prohibited).")
        if review_res.payload.get("human_approval_command"):
            print(f"To approve manually, run: {review_res.payload['human_approval_command']}\n")


def _extract_flow_slug(
    args: argparse.Namespace,
    context: Any | None,
    intent_data: dict[str, Any],
) -> str:
    """Extract or derive a sanitised slug identifier for flow worktree naming."""
    slug: str | None = None
    if intent_data and isinstance(intent_data, dict):
        slug = intent_data.get("slug")
    if not slug and context is not None:
        slug = getattr(context, "slug", None)
        if not slug and hasattr(context, "intent_data") and isinstance(context.intent_data, dict):
            slug = context.intent_data.get("slug")
    if not slug and getattr(args, "intent_file", None):
        stem = os.path.splitext(os.path.basename(args.intent_file))[0]
        slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", stem).strip("-")
    if not slug:
        repo_base = os.path.basename(os.path.abspath(getattr(args, "target_worktree", ".") or "."))
        slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", repo_base).strip("-") or "task"
    return slug


def _flow_target_branch(intent_data: dict[str, Any], context: Any | None) -> str:
    """Return the base branch a newly provisioned flow worktree branches from (default: 'origin/main')."""
    if intent_data and isinstance(intent_data, dict) and intent_data.get("target_branch"):
        return str(intent_data["target_branch"])
    if (
        context is not None
        and getattr(context, "intent_data", None)
        and isinstance(context.intent_data, dict)
        and context.intent_data.get("target_branch")
    ):
        return str(context.intent_data["target_branch"])
    return "origin/main"


def _effective_flow_target(args: argparse.Namespace, context: Any | None) -> tuple[str, bool]:
    """Return the target worktree the run will use and whether it was restored from a checkpoint.

    Mirrors ``_update_flow_context``: an explicit --target-worktree other than '.' wins, otherwise a checkpoint
    resume keeps the checkpoint's recorded target_worktree.
    """
    explicit = getattr(args, "target_worktree", None)
    if explicit and explicit != ".":
        return explicit, False
    if context is not None:
        return getattr(context, "target_worktree", None) or ".", True
    return explicit or ".", False


def _should_provision(target_worktree: str, from_checkpoint: bool) -> bool:
    """Return True for the auto-provisioned cases: a bare clone, or an absent CLI target.

    A plain non-git directory is never provisioned, because the base repository lookup would otherwise fall back to
    the current working directory's repository. An absent checkpoint target is not re-provisioned either, so that a
    removed run worktree is reported rather than silently replaced.
    """
    if is_bare_repository(target_worktree):
        return True
    return not from_checkpoint and not os.path.exists(target_worktree)


def _provision_flow_worktree(
    args: argparse.Namespace,
    context: Any | None,
    intent_data: dict[str, Any],
    target_worktree: str,
    from_checkpoint: bool,
) -> tuple[str, str]:
    """Auto-provision or attach a flow worktree.

    Returns:
        tuple[str, str]: The worktree path and an empty hint, or target_worktree and a hint naming the redacted
            provisioning error when provisioning fails.
    """
    slug = _extract_flow_slug(args, context, intent_data)
    try:
        worktree_path = ensure_worktree(
            target_worktree, slug=slug, target_branch=_flow_target_branch(intent_data, context)
        )
    except (RuntimeError, ValueError) as e:
        error = cli.redact_cli_error(e)
        logger.warning("Could not auto-provision a git worktree from '%s': %s", target_worktree, error)
        return target_worktree, f" (auto-provisioning from this target failed: {error})"
    logger.info("Provisioned or attached git worktree for flow '%s' at: %s", slug, worktree_path)
    if not from_checkpoint:
        args.target_worktree = worktree_path
    if context is not None:
        context.target_worktree = worktree_path
    return worktree_path, ""


def _ensure_flow_worktree(
    args: argparse.Namespace,
    context: Any | None,
    intent_data: dict[str, Any],
) -> bool:
    """Ensure the run's target worktree is an attached, non-bare git working tree before any stage runs.

    A bare or absent target is auto-provisioned as before (a checkpoint-restored target only when bare). Any other
    target that is not a working tree, or one provisioning could not fix, prints an actionable error and exits 1.

    Returns:
        bool: True when the target is usable, False after the failure exit (for a patched ``sys.exit``).
    """
    target_worktree, from_checkpoint = _effective_flow_target(args, context)
    if is_working_tree(target_worktree):
        return True

    resolved, provision_hint = target_worktree, ""
    if _should_provision(target_worktree, from_checkpoint):
        resolved, provision_hint = _provision_flow_worktree(
            args, context, intent_data, target_worktree, from_checkpoint
        )

    if from_checkpoint:
        problem = target_worktree_problem(
            resolved,
            subject=f"Checkpoint '{args.checkpoint}' target_worktree",
            hint=provision_hint + _CHECKPOINT_TARGET_HINT,
        )
    else:
        problem = target_worktree_problem(resolved, hint=provision_hint)
    if problem is not None:
        print(f"Error: {problem}", file=sys.stderr)
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

        if not _check_skip_push(args, context) or not _ensure_flow_worktree(args, context, intent_data):
            return None

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
