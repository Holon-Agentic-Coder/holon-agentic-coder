"""Execute command module for executing code changes for a plan."""

from __future__ import annotations

import argparse

from sandbox_executor import cli
from sandbox_executor.cli.options import (
    add_preflight_option,
    add_repo_dir_option,
    add_runner_options,
    add_token_reduction_options,
)

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


def run(args: argparse.Namespace) -> int:
    """Execute the execute command."""
    return cli._dispatch_complex(args)
