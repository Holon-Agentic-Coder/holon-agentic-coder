"""Doctor command module for environment and agent readiness checks."""

from __future__ import annotations

import argparse

from sandbox_executor import cli
from sandbox_executor.cli.options import add_runner_options

ORDER = 80


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the doctor subcommand parser."""
    parser = subparsers.add_parser(
        "doctor",
        help=(
            "Check that the selected agent runner is ready (one minimal real model call in its "
            "container), and show the effective budgets, the local-LLM model envelope and the "
            "local gateway state."
        ),
    )
    add_runner_options(parser)
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
