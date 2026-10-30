"""Shared option-group helpers and help text constants for CLI commands."""

from __future__ import annotations

import argparse

_TOKEN_REDUCE_HELP = (
    "Cut agent token usage by routing sandbox egress through a locally-owned mitmproxy sidecar "
    "(context deduplication, local caching, and provider prompt cache optimisation). "
    "Requires 'docker' and 'openssl' and performs LOCAL TLS INTERCEPTION using an auto-generated "
    "Holon Root CA. All wire transactions and credentials are sanitised prior to logging."
)

_AGENT_HELP = (
    "Agent runner to execute (e.g. antigravity-agent, pi-agent, claude-agent, codex-agent). "
    "Default: HOLON_AGENT, then the detected harness agent, then antigravity-agent."
)

_MODEL_HELP = "Model name to pass to agent. Default: HOLON_MODEL, then the runner's default model."

_SKIP_PREFLIGHT_HELP = (
    "Skip the pre-flight readiness probe (one minimal real model call). HOLON_SKIP_PREFLIGHT=1 does the same."
)

_DEFAULT_MITM_WEB_HELP = (
    "Launch mitmweb dashboard on port 8081 (or HOLON_MITM_WEB_PORT) for real-time traffic inspection. "
    "For shared staging environments, pass HOLON_MITM_WEB_PASSWORD to restrict access."
)


def add_repo_dir_option(
    parser: argparse.ArgumentParser,
    default: str | None = None,
    help: str = "Target repository directory path (default: current directory).",
) -> None:
    """Add standard --repo-dir argument to a parser."""
    parser.add_argument(
        "--repo-dir",
        default=default,
        help=help,
    )


def add_runner_options(parser: argparse.ArgumentParser) -> None:
    """Add standard --agent and --model runner options."""
    parser.add_argument("--agent", default=None, help=_AGENT_HELP)
    parser.add_argument("--model", default=None, help=_MODEL_HELP)


def add_preflight_option(parser: argparse.ArgumentParser) -> None:
    """Add the --skip-preflight flag."""
    parser.add_argument("--skip-preflight", action="store_true", help=_SKIP_PREFLIGHT_HELP)


def add_token_reduction_options(parser: argparse.ArgumentParser) -> None:
    """Add --token-reduce and --mitm-web options."""
    parser.add_argument(
        "--token-reduce",
        action="store_true",
        help=_TOKEN_REDUCE_HELP,
    )
    parser.add_argument(
        "--mitm-web",
        action="store_true",
        help=_DEFAULT_MITM_WEB_HELP,
    )
