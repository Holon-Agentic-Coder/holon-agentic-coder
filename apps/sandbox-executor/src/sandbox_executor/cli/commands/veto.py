"""Veto command module for inspecting and managing failure vetoes."""

from __future__ import annotations

import argparse
import json
import os
import sys

from sandbox_executor import cli

ORDER = 140


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the veto subcommand parser."""
    veto_parser = subparsers.add_parser(
        "veto",
        help="Inspect and manage deterministic failure vetoes (Bean 0052).",
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
    veto_list_parser.add_argument(
        "--repo-dir",
        default=".",
        help="Repository root directory.",
    )
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
    veto_lift_parser.add_argument(
        "--repo-dir",
        default=".",
        help="Repository root directory.",
    )
    veto_parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    """Execute the veto command."""
    db_path = os.path.join(args.repo_dir, "holon-knowledge/.index/action_index.db")
    db = cli.ActionIndexDatabase(db_path=db_path)
    if getattr(args, "veto_command", None) == "list":
        vetoes = cli.list_vetoes(db)
        if args.json:
            print(json.dumps(vetoes, indent=2))
        else:
            if not vetoes:
                print("No active or shadowed failure vetoes recorded.")
            else:
                for v in vetoes:
                    shadow_str = "SHADOW" if v.get("is_shadow") else "ENFORCED"
                    print(
                        f" - [{v.get('state')}/{shadow_str}] Fingerprint: {v.get('context_fingerprint')} "
                        f"(reproductions: {v.get('reproduction_count')}, decay: {v.get('decay_weight'):.2f})"
                    )
        return 0
    elif getattr(args, "veto_command", None) == "lift":
        lifted = cli.lift_veto(db, context_fingerprint=args.context, reason=args.reason)
        if lifted:
            print(f"Successfully lifted veto for fingerprint: {args.context}")
        else:
            print(f"No active veto found for fingerprint: {args.context}", file=sys.stderr)
        return 0
    return 0
