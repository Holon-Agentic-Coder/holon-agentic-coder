from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

from sandbox_executor import cli
from sandbox_executor.agent_defaults import Resolution
from sandbox_executor.cli.commands._flow_worktree import _extract_flow_slug
from sandbox_executor.flow.checkpoint_discovery import _find_intent_checkpoint
from sandbox_executor.git_flow import flow_worktree_layout
from sandbox_executor.intent_refresh import IntentContractError, refresh_intent_text

logger = logging.getLogger(__name__)

RUN_SWITCHES = ("dry_run", "skip_push", "skip_calibrate")
_PROXY_SWITCHES = ("token_reduce", "mitm_web")


def _cli_flag_for_switch(name: str) -> str:
    return "--" + name.replace("_", "-")


def _candidate_worktrees(args: argparse.Namespace, intent_data: dict[str, Any]) -> list[str]:
    target = getattr(args, "target_worktree", None) or "."
    if not os.path.isdir(target):
        return []
    dedicated, _ = flow_worktree_layout(target, _extract_flow_slug(args, None, intent_data))
    return [path for path in (target, dedicated) if os.path.isdir(path)]


def discover_resume_checkpoint(args: argparse.Namespace) -> str | None:
    if not args.from_stage or args.checkpoint or not args.intent_file or not os.path.isfile(args.intent_file):
        return None
    with open(args.intent_file, encoding="utf-8") as f:
        intent_data = json.load(f)
    if not isinstance(intent_data, dict):
        return None
    return _find_intent_checkpoint(intent_data, _candidate_worktrees(args, intent_data))


def _refresh_intent_from_file(context: Any, args: argparse.Namespace) -> None:
    intent_file = getattr(args, "intent_file", None)
    if not intent_file:
        return
    try:
        refresh_intent_text(context, intent_file)
    except IntentContractError as error:
        logger.error(str(error))
        context.log(str(error))
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


def apply_run_switches(context: Any, args: argparse.Namespace) -> None:
    _refresh_intent_from_file(context, args)
    for name in RUN_SWITCHES:
        given = bool(getattr(args, name, False))
        if getattr(context, name) and not given:
            flag = _cli_flag_for_switch(name)
            message = f"Resume dropped the stored {name} from the checkpoint: this run did not pass {flag}."
            logger.warning(message)
            context.log(message)
        setattr(context, name, given)
    context.auto_calibrate = not context.skip_calibrate


def apply_proxy_switches(
    context: Any,
    spawn_sidecar: bool,
    attach_running_proxy: bool,
    mitm_web: bool,
    requested: tuple[bool, bool] = (False, False),
) -> None:
    for name, given, asked in zip(_PROXY_SWITCHES, (spawn_sidecar, mitm_web), requested, strict=True):
        if getattr(context, name) and not given:
            flag = _cli_flag_for_switch(name)
            if asked:
                message = f"The HOLON_TOKEN_REDUCE opt-out overrides the stored {name} from the checkpoint and {flag}."
            else:
                message = f"Resume dropped the stored {name} from the checkpoint: this run did not pass {flag}."
            logger.warning(message)
            context.log(message)
        setattr(context, name, given)
    context.proxy_attach = attach_running_proxy


def resolve_resumed_runner(args: argparse.Namespace, context: Any, checkpoint_path: str) -> tuple[str, str]:
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
