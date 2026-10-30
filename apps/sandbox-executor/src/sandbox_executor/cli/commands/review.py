"""Review command module for running native review loop on execution branch or PR."""

from __future__ import annotations

import argparse

from sandbox_executor import cli
from sandbox_executor.cli.options import (
    _AGENT_HELP,
    _MODEL_HELP,
    _TOKEN_REDUCE_HELP,
)
from sandbox_executor.review_ensemble import ENSEMBLE_SIZE_HELP

ORDER = 70


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the review-loop subcommand parser."""
    parser = subparsers.add_parser(
        "review-loop",
        aliases=["review", "pr-review-loop"],
        help=(
            "Run autonomous native review loop on an execution branch or pull request (Bean 0118). "
            "Fails closed: exits 1 unless the consensus is approved and posted."
        ),
    )
    parser.add_argument(
        "target",
        help="Target execution branch name, PR number, or PR URL.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=25,
        help="Maximum number of review loop iterations (default: 25).",
    )
    parser.add_argument(
        "--ensemble-size",
        type=int,
        default=3,
        help=ENSEMBLE_SIZE_HELP,
    )
    parser.add_argument(
        "--auto-pr",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Automatically create PR if only a branch exists (default: True). "
            "The empty-diff guard runs whatever this is."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run review evaluation without applying changes or git commits.",
    )
    parser.add_argument(
        "--repo-dir",
        default=".",
        help="Target repository directory path (default: current directory).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output review loop results in JSON format.",
    )
    parser.add_argument(
        "--token-reduce",
        action="store_true",
        help=_TOKEN_REDUCE_HELP,
    )
    parser.add_argument("--agent", default=None, help=_AGENT_HELP)
    parser.add_argument("--model", default=None, help=_MODEL_HELP)
    parser.add_argument(
        "--local",
        action="store_true",
        help=(
            "Run the review loop on the host rather than inside the Docker container; reviewer agent turns are "
            "refused outside a sandbox, so only --dry-run is useful here."
        ),
    )
    parser.add_argument(
        "--skip-push",
        action="store_true",
        help="Skip pushing git commits or branches to remote.",
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
    """Execute the review-loop command."""
    return cli._dispatch_complex(args)
