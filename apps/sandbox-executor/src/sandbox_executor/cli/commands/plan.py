"""Plan command module for running Planner to generate a plan branch from an intent."""

from __future__ import annotations

import argparse
import sys

from sandbox_executor import cli
from sandbox_executor.cli.commands._role import prepare_role_execution
from sandbox_executor.cli.options import (
    add_preflight_option,
    add_repo_dir_option,
    add_runner_options,
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
    add_repo_dir_option(parser)
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int | None:
    """Execute the plan command."""
    common_kwargs = prepare_role_execution(args)
    if common_kwargs is None:
        return None
    exit_code = cli.dispatch_role_agent(
        "planner",
        args.agent,
        args.model,
        [args.intent_branch, args.agent, args.model],
        **common_kwargs,
    )
    sys.exit(exit_code)
    return None
