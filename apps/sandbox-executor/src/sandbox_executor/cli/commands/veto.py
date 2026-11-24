from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from sandbox_executor import cli
from sandbox_executor.cli.options import add_target_worktree_option

ORDER = 140
_ACTION_INDEX_DB_RELATIVE_PATH = "holon-knowledge/.index/action_index.db"


def _run_veto_list(db: Any, json_output: bool) -> int:
    vetoes = cli.list_vetoes(db)
    if json_output:
        print(json.dumps(vetoes, indent=2))
        return 0
    if not vetoes:
        print("No active or shadowed failure vetoes recorded.")
        return 0
    for veto in vetoes:
        shadow_str = "SHADOW" if veto.get("is_shadow") else "ENFORCED"
        print(
            f" - [{veto.get('state')}/{shadow_str}] Fingerprint: {veto.get('context_fingerprint')} "
            f"(reproductions: {veto.get('reproduction_count')}, decay: {veto.get('decay_weight'):.2f})"
        )
    return 0


def _run_veto_lift(db: Any, context_fingerprint: str, reason: str) -> int:
    lifted = cli.lift_veto(db, context_fingerprint=context_fingerprint, reason=reason)
    if lifted:
        print(f"Successfully lifted veto for fingerprint: {context_fingerprint}")
    else:
        print(f"No active veto found for fingerprint: {context_fingerprint}", file=sys.stderr)
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    veto_parser = subparsers.add_parser(
        "veto",
        help="Inspect and manage deterministic failure vetoes.",
    )
    veto_subparsers = veto_parser.add_subparsers(
        dest="veto_command",
        required=True,
    )
    veto_list_parser = veto_subparsers.add_parser(
        "list",
        help="List active and shadowed failure vetoes.",
    )
    veto_list_parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format.",
    )
    add_target_worktree_option(veto_list_parser, default=".")
    veto_lift_parser = veto_subparsers.add_parser(
        "lift",
        help="Manually lift an active or shadowed failure veto.",
    )
    veto_lift_parser.add_argument(
        "--context",
        required=True,
        help="Context fingerprint to lift.",
    )
    veto_lift_parser.add_argument(
        "--reason",
        default="Manual operator override",
        help="Audit reason for lifting veto.",
    )
    add_target_worktree_option(veto_lift_parser, default=".")
    veto_parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    db_path = os.path.join(args.target_worktree, _ACTION_INDEX_DB_RELATIVE_PATH)
    db = cli.ActionIndexDatabase(db_path=db_path)
    veto_command = getattr(args, "veto_command", None)
    if veto_command == "list":
        return _run_veto_list(db, bool(args.json))
    if veto_command == "lift":
        return _run_veto_lift(db, args.context, args.reason)
    return 0
