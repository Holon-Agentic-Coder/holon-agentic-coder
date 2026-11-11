"""Plan command module for running Planner to generate a plan branch from an intent."""

from __future__ import annotations

import argparse
import sys

from sandbox_executor import cli
from sandbox_executor.cli.commands._host_publish import prepare_worktree, push_checked_out_branch, slug_of_branch
from sandbox_executor.cli.commands._role import prepare_role_execution
from sandbox_executor.cli.options import (
    add_preflight_option,
    add_runner_options,
    add_target_worktree_option,
    add_token_reduction_options,
)

ORDER = 20


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the plan subcommand parser."""
    parser = subparsers.add_parser("plan", help="Run Plan Generator to create a detailed markdown plan branch.")
    parser.add_argument("intent_branch", help="Target intent branch name (e.g. I-1784983150-build-execution/_)")
    add_runner_options(parser)
    add_preflight_option(parser)
    add_token_reduction_options(parser)
    add_target_worktree_option(parser)
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int | None:
    """Execute the plan command."""
    common_kwargs = prepare_role_execution(args)
    if common_kwargs is None:
        return None
    try:
        common_kwargs["target_worktree"] = prepare_worktree(
            args.intent_branch, common_kwargs.get("target_worktree"), slug_of_branch(args.intent_branch)
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {cli.redact_cli_error(exc)}", file=sys.stderr)
        sys.exit(1)
    exit_code = cli.dispatch_role_agent(
        "planner",
        args.agent,
        args.model,
        [args.intent_branch, args.agent, args.model],
        **common_kwargs,
    )
    if exit_code == 0:
        try:
            push_checked_out_branch(common_kwargs["target_worktree"])
        except RuntimeError as exc:
            print(f"Error: {cli.redact_cli_error(exc)}", file=sys.stderr)
            exit_code = 1
    sys.exit(exit_code)
    return None
