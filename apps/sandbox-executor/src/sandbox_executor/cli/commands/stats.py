from __future__ import annotations

import argparse
import json
import os

from sandbox_executor import cli
from sandbox_executor.cli.options import add_target_worktree_option

ORDER = 150
_ACTION_INDEX_DB_RELATIVE_PATH = "holon-knowledge/.index/action_index.db"


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "stats",
        help="Display operational self-metrics and consult telemetry.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output telemetry in JSON format.",
    )
    add_target_worktree_option(parser, default=".")
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    db_path = os.path.join(args.target_worktree, _ACTION_INDEX_DB_RELATIVE_PATH)
    db = cli.ActionIndexDatabase(db_path=db_path)
    telemetry_stats = cli.TelemetryCollector.get_telemetry_stats(db)
    if args.json:
        print(json.dumps(telemetry_stats, indent=2))
    else:
        print(cli.format_stats_dashboard(telemetry_stats))
    return 0
