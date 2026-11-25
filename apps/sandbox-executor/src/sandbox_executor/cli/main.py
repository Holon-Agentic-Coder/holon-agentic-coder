"""CLI main entrypoint for Holon."""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import sys

from sandbox_executor import config_env
from sandbox_executor.cli.logging import configure_cli_logging
from sandbox_executor.launcher.messages import print_encodable, redact_cli_error

__all__ = ["build_parser", "configure_cli_logging", "main", "resolve_layered_configuration"]


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser and register all discovered subcommands."""
    from sandbox_executor.cli.commands import discover

    try:
        pkg_version = importlib.metadata.version("holon")
    except importlib.metadata.PackageNotFoundError:
        pkg_version = "0.1.0"
    parser = argparse.ArgumentParser(
        prog="holon",
        description="Holon CLI: Host wrapper for running containerized Holon AI agent roles.",
    )
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {pkg_version}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for cmd in discover():
        cmd.register(subparsers)
    return parser


def resolve_layered_configuration() -> config_env.EffectiveConfig:
    """Resolve the layered configuration once for this invocation and fold it into the host environment.

    Each warning goes to stderr (redacted as defence in depth; it names keys, paths and modes only). The file-sourced
    values fill only the variables the process environment leaves unset, so the process environment and explicit CLI
    flags keep winning, and every role container and the pre-flight probe inherit them through the 0600 env-file.
    The resolution is remembered with its pre-apply provenance for ``holon doctor``.
    """
    config = config_env.load_effective_config()
    for warning in config.warnings:
        print_encodable(redact_cli_error(warning), file=sys.stderr)
    config_env.apply_to_environ(config, os.environ)
    config_env.remember(config)
    return config


def main(argv: list[str] | None = None) -> None:
    """Run the Holon CLI."""
    configure_cli_logging()
    # Before the parser is built: argparse defaults may read the environment.
    resolve_layered_configuration()
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "_run"):
        ret = args._run(args)
        if ret is not None:
            sys.exit(ret)
        return
    sys.exit(0)


if __name__ == "__main__":
    main()
