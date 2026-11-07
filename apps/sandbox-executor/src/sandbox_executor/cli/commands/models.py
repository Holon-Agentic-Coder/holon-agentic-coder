"""Models command module to list supported models, aliases, and effort levels across runners."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from sandbox_executor.agent_defaults import (
    RUNNER_CAPABILITIES,
    detect_runner_auth_status,
    get_model_capabilities,
    normalise_agent_id,
)

ORDER = 75


def _valid_runner_ids() -> set[str]:
    valid: set[str] = set()
    for cap in RUNNER_CAPABILITIES:
        valid.add(normalise_agent_id(cap.runner))
        valid.add(cap.runner)
    valid.add("local")
    return valid


def _format_table(rows: list[dict[str, str]]) -> str:
    headers = [
        "Runner",
        "Canonical Model",
        "Aliases",
        "Supported Effort Levels",
        "Fallback Tier",
        "Auth / Session Detection",
    ]
    keys = ["runner", "canonical_model", "aliases", "effort_levels", "fallback_tier", "auth_status"]
    widths = {k: len(h) for k, h in zip(keys, headers, strict=True)}
    for row in rows:
        for k in keys:
            widths[k] = max(widths[k], len(row[k]))

    header_line = "| " + " | ".join(h.ljust(widths[k]) for k, h in zip(keys, headers, strict=True)) + " |"
    separator_line = "|-" + "-|-".join("-" * widths[k] for k in keys) + "-|"
    body_lines = ["| " + " | ".join(row[k].ljust(widths[k]) for k in keys) + " |" for row in rows]
    return "\n".join([header_line, separator_line, *body_lines])


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the models subcommand parser."""
    parser = subparsers.add_parser(
        "models",
        aliases=["list-models"],
        help="List supported models, aliases, effort levels, and fallback tiers across runners.",
    )
    parser.add_argument(
        "--agent",
        type=str,
        default=None,
        help="Filter models by agent runner (e.g. antigravity, claude, pi, codex, gemini, opencode, local).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output supported models and capabilities as JSON.",
    )
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    """Execute the models command."""
    agent_filter = args.agent.strip() if args.agent and args.agent.strip() else None

    if agent_filter:
        norm_filter = normalise_agent_id(agent_filter)
        valid_ids = _valid_runner_ids()
        if norm_filter not in valid_ids and agent_filter not in valid_ids:
            canonical_runners = sorted({cap.runner for cap in RUNNER_CAPABILITIES})
            print(
                f"Error: Unknown agent runner '{agent_filter}'. Supported runners: {', '.join(canonical_runners)}.",
                file=sys.stderr,
            )
            return 1

    capabilities = get_model_capabilities(agent_filter)

    if args.json:
        records: list[dict[str, Any]] = [
            {
                "runner": cap.runner,
                "canonical_model": cap.canonical_model,
                "aliases": cap.aliases,
                "effort_levels": cap.effort_levels,
                "fallback_tier": cap.fallback_tier,
                "auth_status": detect_runner_auth_status(cap.runner),
            }
            for cap in capabilities
        ]
        print(json.dumps(records, indent=2))
        return 0

    table_rows: list[dict[str, str]] = [
        {
            "runner": cap.runner,
            "canonical_model": cap.canonical_model,
            "aliases": ", ".join(cap.aliases) if cap.aliases else "-",
            "effort_levels": ", ".join(cap.effort_levels) if cap.effort_levels else "-",
            "fallback_tier": cap.fallback_tier,
            "auth_status": detect_runner_auth_status(cap.runner),
        }
        for cap in capabilities
    ]

    print(_format_table(table_rows))
    return 0
