"""Intent command module for initialising intent branches."""

from __future__ import annotations

import argparse
import sys

from sandbox_executor import cli
from sandbox_executor.cli.options import (
    add_target_worktree_option,
    add_token_reduction_options,
)

ORDER = 10


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the intent subcommand parser."""
    parser = subparsers.add_parser("intent", help="Run Intent Creator to initialise a new intent branch.")
    parser.add_argument("intent_file", help="Path to local intent JSON/YAML file (e.g. intents/my-task.json)")
    add_target_worktree_option(parser, default=None)
    add_token_reduction_options(parser)
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int | None:
    """Execute the intent command."""
    exit_code = cli.dispatch_role_agent(
        cli.INTENT_CREATOR_ROLE,
        getattr(args, "agent", None),
        getattr(args, "model", None),
        [],
        intent_file=args.intent_file,
        target_worktree=getattr(args, "target_worktree", None),
        token_reduce=bool(getattr(args, "token_reduce", False)),
        mitm_web=bool(getattr(args, "mitm_web", False)),
    )
    sys.exit(exit_code)
    return None
