"""Shared role agent preparation and dispatch helper for plan and execute commands."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from sandbox_executor import cli


def prepare_role_execution(args: argparse.Namespace) -> dict[str, Any] | None:
    """Resolve runner and model and run pre-flight gate for role commands.

    Args:
        args: Command arguments namespace.

    Returns:
        dict[str, Any] | None: Common keyword arguments for dispatch_role_agent,
            or None if pre-flight gate failed or runner could not be resolved.
    """
    try:
        agent_res, model_res = cli.resolve_runner_and_model(args.agent, args.model)
    except (cli.UnmappedAgentRunnerError, cli.MissingAgentRunnerError, cli.MissingModelError) as exc:
        print(f"Error: {cli.redact_cli_error(exc)}", file=sys.stderr)
        sys.exit(1)
        return None
    args.agent, args.model = agent_res.value, model_res.value
    preflight = cli.preflight_gate(args.agent, args.model, skip=args.skip_preflight)
    if preflight is not None and not preflight.ok:
        sys.exit(1)
        return None
    return {
        "repo_dir": getattr(args, "repo_dir", None),
        "token_reduce": bool(getattr(args, "token_reduce", False)),
        "mitm_web": bool(getattr(args, "mitm_web", False)),
    }
