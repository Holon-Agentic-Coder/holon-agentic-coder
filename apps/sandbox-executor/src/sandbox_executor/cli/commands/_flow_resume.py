"""Resume policy for ``holon flow``: checkpoint discovery, per-run switches and the resumed runner.

A ``--from-stage`` resume without ``--checkpoint`` loads the intent's default checkpoint
(``.holon/flow/checkpoint-<slug>.json``) when one exists, so the resumed stage gets the recorded branches and earlier
stage results instead of an empty flow context. The target worktree is probed first, then the dedicated
``flow-<slug>`` worktree a default-branch target is redirected to.

On resume, the per-run switches come only from the current flags (a stored skip is dropped and logged), and
``--agent`` / ``--model`` override the checkpoint's runner and model for every remaining stage and role.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from typing import Any

from sandbox_executor import cli
from sandbox_executor.agent_defaults import Resolution
from sandbox_executor.cli.commands._flow_worktree import _extract_flow_slug
from sandbox_executor.flow.checkpoint_discovery import _find_intent_checkpoint
from sandbox_executor.git_flow import flow_worktree_layout

logger = logging.getLogger(__name__)

RUN_SWITCHES = ("dry_run", "skip_push", "skip_calibrate")
"""Per-run switches a resume takes only from the current flags, never from the checkpoint."""


def _candidate_worktrees(args: argparse.Namespace, intent_data: dict[str, Any]) -> list[str]:
    """The target worktree, then its ``flow-<slug>`` sibling; only existing directories."""
    target = getattr(args, "target_worktree", None) or "."
    if not os.path.isdir(target):
        return []
    dedicated, _ = flow_worktree_layout(target, _extract_flow_slug(args, None, intent_data))
    return [path for path in (target, dedicated) if os.path.isdir(path)]


def discover_resume_checkpoint(args: argparse.Namespace) -> str | None:
    """The intent's existing checkpoint for a ``--from-stage`` run without ``--checkpoint``, or ``None``."""
    if not args.from_stage or args.checkpoint or not args.intent_file or not os.path.isfile(args.intent_file):
        return None
    with open(args.intent_file, encoding="utf-8") as f:
        intent_data = json.load(f)
    if not isinstance(intent_data, dict):
        return None
    return _find_intent_checkpoint(intent_data, _candidate_worktrees(args, intent_data))


def apply_run_switches(context: Any, args: argparse.Namespace) -> None:
    """Set the per-run switches on a resumed ``context`` from this run's flags, logging each dropped stored skip."""
    for name in RUN_SWITCHES:
        given = bool(getattr(args, name, False))
        if getattr(context, name) and not given:
            flag = "--" + name.replace("_", "-")
            message = f"Resume dropped the stored {name} from the checkpoint: this run did not pass {flag}."
            logger.warning(message)
            context.log(message)
        setattr(context, name, given)
    context.auto_calibrate = not context.skip_calibrate


def apply_proxy_switches(context: Any, spawn_sidecar: bool, attach_running_proxy: bool, mitm_web: bool) -> None:
    """Set the proxy routing on a resumed ``context`` from this run, logging each dropped stored sidecar opt-in.

    ``spawn_sidecar`` and ``attach_running_proxy`` come from this run's resolved proxy choice, and ``mitm_web`` is
    kept only when this run passes ``--mitm-web`` and the choice spawns a sidecar, so a checkpoint never spawns one.
    """
    for name, given in (("token_reduce", spawn_sidecar), ("mitm_web", mitm_web)):
        if getattr(context, name) and not given:
            flag = "--" + name.replace("_", "-")
            message = f"Resume dropped the stored {name} from the checkpoint: this run did not pass {flag}."
            logger.warning(message)
            context.log(message)
        setattr(context, name, given)
    context.proxy_attach = attach_running_proxy


def resolve_resumed_runner(args: argparse.Namespace, context: Any, checkpoint_path: str) -> tuple[str, str]:
    """The runner and model for a checkpoint resume: ``--agent`` / ``--model`` when given, else the checkpoint's."""
    recorded = f"recorded in {checkpoint_path}"
    runner_switched = False
    if args.agent is None:
        agent = context.agent
        logger.info(Resolution(agent, "checkpoint", recorded).describe("Agent runner"))
    else:
        agent = cli.resolve_agent_runner(args.agent).value
        runner_switched = cli.normalise_agent_id(agent) != cli.normalise_agent_id(context.agent)
        if runner_switched:
            logger.info(
                "Agent runner %s from --agent overrides %s %s for every remaining stage and role.",
                agent,
                context.agent,
                recorded,
            )
    if args.model is None and not runner_switched:
        model = context.model
        logger.info(Resolution(model, "checkpoint", recorded).describe("Model"))
        return agent, model
    resolution = cli.resolve_model(agent, args.model)
    model = resolution.value
    if model != context.model:
        logger.info(
            "Model %s from %s (%s) overrides %s %s for every remaining stage and role.",
            model,
            resolution.source,
            resolution.reason,
            context.model,
            recorded,
        )
    return agent, model
