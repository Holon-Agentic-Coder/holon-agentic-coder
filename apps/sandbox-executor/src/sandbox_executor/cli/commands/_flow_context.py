from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

from sandbox_executor import cli
from sandbox_executor.cli.commands._flow_resume import (
    apply_proxy_switches,
    apply_run_switches,
    discover_resume_checkpoint,
    resolve_resumed_runner,
)
from sandbox_executor.flow import FlowContext, FlowStage
from sandbox_executor.launcher import coherence
from sandbox_executor.review_ensemble import DEFAULT_ENSEMBLE_SIZE

logger = logging.getLogger(__name__)


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
    # why: a resumed context already carries this run's switches (a stored skip was dropped), so it decides
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
    # why: an opt-out wins over a stale checkpointed token_reduce or mitm_web, so a resume must repeat the flag
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
