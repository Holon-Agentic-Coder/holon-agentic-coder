"""Stats command module for displaying operational telemetry and self-metrics."""

from __future__ import annotations

import argparse
import json
import os

from sandbox_executor import cli

ORDER = 150


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the stats subcommand parser."""
    parser = subparsers.add_parser(
        "stats",
        help="Display operational self-metrics and consult telemetry (Bean 0052).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output telemetry in JSON format.",
    )
    parser.add_argument(
        "--repo-dir",
        default=".",
        help="Repository root directory.",
    )
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    """Execute the stats command."""
    db_path = os.path.join(args.repo_dir, "holon-knowledge/.index/action_index.db")
    db = cli.ActionIndexDatabase(db_path=db_path)
    telemetry_stats = cli.TelemetryCollector.get_telemetry_stats(db)
    if args.json:
        print(json.dumps(telemetry_stats, indent=2))
    else:
        print(cli.format_stats_dashboard(telemetry_stats))
    return 0
