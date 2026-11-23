"""Init command module for scaffolding project configuration."""

from __future__ import annotations

import argparse

from sandbox_executor import cli

ORDER = 40


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the init subcommand parser."""
    parser = subparsers.add_parser(
        "init",
        help=(
            "Scaffold Holon configuration and knowledge directories in a target project, a commented .env.example "
            "for the layered configuration, and a .gitignore entry for .env."
        ),
    )
    parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Target directory to initialise (default: current directory).",
    )
    parser.add_argument(
        "--template",
        choices=["python", "generic"],
        default="python",
        help="Project template type (default: python).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing configuration files (ledger files are never overwritten).",
    )
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    """Execute the init command."""
    return cli.init_project(
        target_dir=args.target_dir,
        template=args.template,
        force=args.force,
    )
