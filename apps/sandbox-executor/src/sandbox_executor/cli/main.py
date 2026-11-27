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
    config = config_env.load_effective_config()
    for warning in config.warnings:
        print_encodable(redact_cli_error(warning), file=sys.stderr)
    config_env.apply_to_environ(config, os.environ)
    config_env.remember(config)
    return config


def main(argv: list[str] | None = None) -> None:
    configure_cli_logging()
    # why: argparse defaults read the environment, so the layered configuration must be folded in before build_parser.
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
