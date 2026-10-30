"""Plan command module for generating markdown plans."""

from __future__ import annotations

import argparse

from sandbox_executor import cli
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


def run(args: argparse.Namespace) -> int:
    """Execute the plan command."""
    return cli._dispatch_complex(args)
