from __future__ import annotations

import argparse
import importlib
import json
import os
import pkgutil
import sys

from sandbox_executor import cli
from sandbox_executor.cli.options import add_target_worktree_option
from sandbox_executor.ledger.fragments import iter_fragment_files

ORDER = 90
_DEFAULT_LEDGER_RELATIVE_PATH = "holon-knowledge/ledger/executions.jsonl"
_LEDGER_SUBCOMMAND_PREFIX = "ledger_"


def _resolve_worktree_path(path: str, target_worktree: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.join(target_worktree, path)


def _register_ledger_subcommands(ledger_subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    package = __name__.rpartition(".")[0]
    names = sorted(
        info.name
        for info in pkgutil.iter_modules(importlib.import_module(package).__path__)
        if info.name.startswith(_LEDGER_SUBCOMMAND_PREFIX)
    )
    for name in names:
        register_subcommand = getattr(importlib.import_module(f"{package}.{name}"), "register_ledger_subcommand", None)
        if callable(register_subcommand):
            register_subcommand(ledger_subparsers)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    ledger_parser = subparsers.add_parser(
        "ledger",
        help="Query and inspect Holon ledgers.",
    )
    ledger_subparsers = ledger_parser.add_subparsers(
        dest="ledger_command",
        required=True,
    )
    query_parser = ledger_subparsers.add_parser(
        "query",
        help="Query action and summary records from execution ledger, including its pending fragments.",
    )
    query_parser.add_argument(
        "--ledger-file",
        default=_DEFAULT_LEDGER_RELATIVE_PATH,
        help=(
            "Path to execution ledger JSONL file (default: holon-knowledge/ledger/executions.jsonl); a canonical "
            "ledger also reads its holon-knowledge/ledger/pending/<ledger>/*.jsonl fragments."
        ),
    )
    query_parser.add_argument(
        "--entry-type",
        choices=["action", "summary", "all"],
        default="all",
        help="Filter records by entry type (action, summary, or all; default: all).",
    )
    query_parser.add_argument(
        "--category",
        choices=["code", "doc", "config", "test", "build", "dependency"],
        default=None,
        help="Filter action records by category.",
    )
    query_parser.add_argument(
        "--labels",
        default=None,
        help="Filter records by comma-separated labels (matches if any label exists).",
    )
    query_parser.add_argument(
        "--target",
        default=None,
        help="Filter records by substring match on target or plan branch.",
    )
    query_parser.add_argument(
        "--outcome",
        choices=["success", "failure", "reverted", "blocked"],
        default=None,
        help="Filter records by outcome or status.",
    )
    query_parser.add_argument(
        "--origin",
        choices=["initial_implementation", "pr_review", "ci_fix", "calibration", "operator"],
        default=None,
        help="Filter records by action origin.",
    )
    query_parser.add_argument(
        "--review-severity",
        choices=["critical", "important", "nit"],
        default=None,
        help="Filter records by review severity.",
    )
    query_parser.add_argument(
        "--query",
        "-q",
        dest="query",
        default=None,
        help="Case-insensitive substring search across action descriptions, summaries, and error contexts.",
    )
    query_parser.add_argument(
        "--keyword",
        dest="keyword",
        default=None,
        help="Alias for --query.",
    )
    query_parser.add_argument(
        "--json",
        action="store_true",
        help="Output matching records in structured JSON format.",
    )
    query_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of records to return.",
    )
    add_target_worktree_option(query_parser, default=".")
    _register_ledger_subcommands(ledger_subparsers)
    ledger_parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    ledger_run = getattr(args, "_ledger_run", None)
    if callable(ledger_run):
        return ledger_run(args)
    if getattr(args, "ledger_command", None) == "query":
        ledger_file = _resolve_worktree_path(args.ledger_file, args.target_worktree)

        if not os.path.exists(ledger_file) and not iter_fragment_files(ledger_file):
            print(f"Error: Ledger file '{ledger_file}' does not exist.", file=sys.stderr)
            return 1

        search_query = args.query or args.keyword
        results = cli.query_ledger(
            ledger_path=ledger_file,
            entry_type=args.entry_type,
            category=args.category,
            labels=args.labels,
            target=args.target,
            outcome=args.outcome,
            origin=args.origin,
            review_severity=args.review_severity,
            query=search_query,
            limit=args.limit,
        )

        if args.json:
            print(json.dumps(results, indent=2))
        else:
            table = cli.format_query_results_table(results)
            print(table)
        return 0
    return 0
