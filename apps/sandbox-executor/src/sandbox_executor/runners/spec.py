"""Static facts about one agent runner.

A :class:`RunnerSpec` holds the container image, default model, ticker, accepted credential variables and the
credential check of a single agent runner. Every module in this package must stay free of imports from
``agent_defaults`` and ``agent_runner``: those modules derive their tables from the specs.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from sandbox_executor import local_llm


@dataclass(frozen=True)
class RunnerSpec:
    """Immutable description of one agent runner."""

    agent_id: str
    image: str
    default_model: str
    ticker: str | None
    api_key_env_vars: tuple[str, ...]
    credential_check: Callable[[RunnerSpec], str | None] = field(repr=False, compare=False)

    def check_credentials(self) -> str | None:
        """Return ``None`` when credentials are present, otherwise the error text to print."""
        return self.credential_check(self)


def has_api_key(spec: RunnerSpec) -> bool:
    """Return whether any of the spec's accepted credential variables is set to a non-empty value."""
    return any(os.getenv(name) for name in spec.api_key_env_vars)


def any_path_exists(paths: Iterable[str]) -> bool:
    """Return whether any path exists as given or after expanding ``~``."""
    return any(os.path.exists(p) or os.path.exists(os.path.expanduser(p)) for p in paths)


def generic_missing_message(agent_id: str) -> str:
    """Return the missing-credentials error shared by runners that accept ``HOLON_AGENT_KEY``."""
    return (
        f"Error: Missing required API credentials for agent '{agent_id}'.\n"
        "Please set 'HOLON_AGENT_KEY', mount session credentials, or select a host-local model with "
        f"'{local_llm.ENV_LOCAL_MODE}=1' plus a reachable endpoint."
    )
