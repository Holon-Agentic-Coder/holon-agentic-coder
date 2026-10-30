"""Calibrate command module for post-execution plan calibration analysis."""

from __future__ import annotations

import argparse
import sys

from sandbox_executor import cli

ORDER = 50


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the calibrate subcommand parser."""
    parser = subparsers.add_parser(
        "calibrate",
        help="Run post-execution plan calibration analysis on an execution branch.",
    )
    parser.add_argument(
        "execution_branch",
        help="Target execution branch name (e.g. I-.../P-.../E-.../_)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output calibration metrics as JSON to stdout.",
    )
    parser.add_argument(
        "--repo-dir",
        default=".",
        help="Target repository directory path (default: current directory).",
    )
    parser.add_argument(
        "--no-commit",
        dest="skip_commit",
        action="store_true",
        help="Generate report without creating a git branch or committing.",
    )
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    """Execute the calibrate command."""
    try:
        cli.run_calibrate(
            execution_branch=args.execution_branch,
            repo_dir=args.repo_dir,
            json_output=args.json,
            skip_commit=args.skip_commit,
        )
        return 0
    except Exception as e:
        print(f"Error during calibration: {cli.redact_cli_error(e)}", file=sys.stderr)
        return 1
