"""Consult command module for knowledge records and wisdom queries."""

from __future__ import annotations

import argparse
import json
import os

from sandbox_executor import cli

ORDER = 100


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the consult subcommand parser."""
    parser = subparsers.add_parser(
        "consult",
        help="Consult knowledge records and wisdom using multi-label queries (Bean 0052).",
    )
    parser.add_argument(
        "--all",
        dest="all_labels",
        nargs="+",
        default=None,
        help="Conjunctive filter: records matching all specified labels.",
    )
    parser.add_argument(
        "--any",
        dest="any_labels",
        nargs="+",
        default=None,
        help="Disjunctive filter: records matching any specified labels.",
    )
    parser.add_argument(
        "--not",
        dest="not_labels",
        nargs="+",
        default=None,
        help="Negated filter: exclude records matching any of these labels.",
    )
    parser.add_argument(
        "--query",
        "-q",
        dest="query",
        default=None,
        help="Full-text FTS5 search query.",
    )
    parser.add_argument(
        "--top-k",
        dest="top_k",
        type=int,
        default=6,
        help="Maximum records to retrieve (ceiling 6).",
    )
    parser.add_argument(
        "--max-tokens",
        dest="max_tokens",
        type=int,
        default=1200,
        help="Maximum total payload tokens injected (default: 1200).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON results.",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (table or json; default: table).",
    )
    parser.add_argument(
        "--repo-dir",
        default=".",
        help="Target repository directory path (default: current directory).",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Override SQLite index database path.",
    )
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    """Execute the consult command."""
    db_path = args.db_path
    if not db_path:
        db_path = os.path.join(args.repo_dir, "holon-knowledge/.index/action_index.db")
    engine = cli.ConsultEngine(project_db_path=db_path)
    result = engine.consult_records(
        all_labels=args.all_labels,
        any_labels=args.any_labels,
        not_labels=args.not_labels,
        query_text=args.query,
        top_k=args.top_k,
        max_tokens=args.max_tokens,
    )
    if args.json or args.format == "json":
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(cli.format_consult_results_table(result))
    return 0
