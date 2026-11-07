"""CLI main entrypoint for Holon.

Re-exports top-level CLI utilities and provides standalone invocation entrypoint.
"""

from __future__ import annotations

from sandbox_executor.cli import build_parser, configure_cli_logging, main

__all__ = ["build_parser", "configure_cli_logging", "main"]

if __name__ == "__main__":
    main()
