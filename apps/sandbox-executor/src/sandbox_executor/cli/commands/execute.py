"""Execute command module for running Executor to implement a plan branch."""

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
from sandbox_executor.entrypoint.executor import OVER_BUDGET_EXIT_CODE

ORDER = 30


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the execute subcommand parser."""
    parser = subparsers.add_parser("execute", help="Run Sandbox Executor to execute code changes for a plan.")
    parser.add_argument("plan_branch", help="Target plan branch name")
    add_runner_options(parser)
    add_preflight_option(parser)
    add_token_reduction_options(parser)
    add_repo_dir_option(parser)
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int | None:
    """Execute the execute command."""
    common_kwargs = prepare_role_execution(args)
    if common_kwargs is None:
        return None
    exit_code = cli.dispatch_role_agent(
        "executor",
        args.agent,
        args.model,
        [args.plan_branch, args.agent, args.model],
        **common_kwargs,
    )
    if exit_code == OVER_BUDGET_EXIT_CODE:
        print(
            f"Error: plan {args.plan_branch} is over its entropy budget and was not executed (exit code "
            f"{exit_code}); its execution branch records the over_budget row. Re-plan it within the budget, or "
            "add an explicit sub-intent list.",
            file=sys.stderr,
        )
    sys.exit(exit_code)
    return None
