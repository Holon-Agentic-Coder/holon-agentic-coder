"""Intent command module for initialising intent branches."""

from __future__ import annotations

import argparse

from sandbox_executor import cli
from sandbox_executor.cli.options import _TOKEN_REDUCE_HELP

ORDER = 10


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the intent subcommand parser."""
    parser = subparsers.add_parser("intent", help="Run Intent Creator to initialise a new intent branch.")
    parser.add_argument("intent_file", help="Path to local intent JSON/YAML file (e.g. intents/my-task.json)")
    parser.add_argument(
        "--repo-dir",
        default=None,
        help="Target repository directory path (default: current directory).",
    )
    parser.add_argument(
        "--token-reduce",
        action="store_true",
        help=_TOKEN_REDUCE_HELP,
    )
    parser.add_argument(
        "--mitm-web",
        action="store_true",
        help=(
            "Launch mitmweb dashboard on port 8081 (or HOLON_MITM_WEB_PORT) for real-time traffic inspection. "
            "For shared staging environments, pass HOLON_MITM_WEB_PASSWORD to restrict access."
        ),
    )
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    """Execute the intent command."""
    return cli._dispatch_complex(args)
