"""Flow command module for running the 5-stage Holon Flow lifecycle pipeline."""

from __future__ import annotations

import argparse

from sandbox_executor import cli
from sandbox_executor.cli.options import (
    _AGENT_HELP,
    _MODEL_HELP,
    _SKIP_PREFLIGHT_HELP,
    _TOKEN_REDUCE_HELP,
)
from sandbox_executor.flow import FlowStage
from sandbox_executor.review_ensemble import ENSEMBLE_SIZE_HELP

ORDER = 60

_FROM_STAGE_CHOICES = [s.value for s in FlowStage if s != FlowStage.COMPLETED]


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the flow subcommand parser."""
    parser = subparsers.add_parser(
        "flow",
        help="Run the automated 5-stage Holon Flow lifecycle pipeline.",
    )
    parser.add_argument(
        "intent_file",
        nargs="?",
        default=None,
        help="Path to local intent JSON file (e.g. intents/my-task.json)",
    )
    parser.add_argument(
        "--from-stage",
        choices=_FROM_STAGE_CHOICES,
        default=None,
        help="Resume pipeline from a specific stage.",
    )
    parser.add_argument(
        "--checkpoint",
        default=None,
        help="Optional path to a flow checkpoint file.",
    )
    parser.add_argument(
        "--agent", default=None, help=f"{_AGENT_HELP} A --checkpoint resume keeps the checkpoint's runner by default."
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"{_MODEL_HELP} A --checkpoint resume keeps the checkpoint's model unless --agent switches the runner.",
    )
    parser.add_argument("--skip-preflight", action="store_true", help=_SKIP_PREFLIGHT_HELP)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate stages and branches without executing external agents or git commits.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output pipeline status and stage results in structured JSON format to stdout.",
    )
    parser.add_argument(
        "--repo-dir",
        default=".",
        help="Target repository directory path (default: current directory).",
    )
    parser.add_argument(
        "--skip-push",
        action="store_true",
        help=(
            "Skip pushing git branches to remote origin. Only valid with --dry-run: the intent, plan and "
            "execute stages dispatch role agents that fetch from origin, the review stage reviews the origin "
            "execution branch and the calibrate stage pushes to origin, so any non-dry run, including a "
            "--from-stage or --checkpoint resume, is rejected before any stage runs."
        ),
    )
    parser.add_argument(
        "--skip-review",
        action="store_true",
        help="Skip Stage 4 (PR review loop) and proceed to calibration.",
    )
    parser.add_argument(
        "--skip-calibrate",
        action="store_true",
        help="Skip Stage 5 (post-execution calibration report generation).",
    )
    parser.add_argument(
        "--max-review-iterations",
        type=int,
        default=25,
        help="Maximum number of autonomous review loop iterations in Stage 4 (must be >= 1, default: 25).",
    )
    parser.add_argument(
        "--ensemble-size",
        type=int,
        default=None,
        help=ENSEMBLE_SIZE_HELP,
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
            "Launch mitmweb dashboard on port 8081 (configurable via HOLON_MITM_WEB_PORT) "
            "for real-time traffic inspection. Automatically enables --token-reduce."
        ),
    )
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    """Execute the flow command."""
    return cli._dispatch_complex(args)
