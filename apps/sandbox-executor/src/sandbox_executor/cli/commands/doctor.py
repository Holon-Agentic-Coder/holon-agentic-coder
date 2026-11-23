"""Doctor command module for environment and agent readiness checks."""

from __future__ import annotations

import argparse

from sandbox_executor import cli
from sandbox_executor.cli.options import add_runner_options, add_target_worktree_option

ORDER = 80


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the doctor subcommand parser."""
    parser = subparsers.add_parser(
        "doctor",
        help=(
            "Check that the selected agent runner is ready (one minimal real model call in its "
            "container), and show the effective budgets ('unlimited' for a budget variable set to "
            "'unlimited'), the GitHub token type and scopes (never its value), the local-LLM model envelope, the "
            "local gateway state and every effective HOLON_* setting with the layer it came from (~/.holon/.env, "
            "<repo>/.env, process env or flag) and the layers it shadowed; secrets are shown only as 'set (<layer>)'."
        ),
    )
    add_runner_options(parser)
    add_target_worktree_option(parser, default=".")
    parser.add_argument("--json", action="store_true", help="Output the resolution and probe result as JSON.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Probe timeout in seconds (default: HOLON_PREFLIGHT_TIMEOUT, then 60).",
    )
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    """Execute the doctor command."""
    return cli.run_doctor(args)
