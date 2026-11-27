from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

# why: agent_defaults and agent_runner derive their tables from these specs; importing them here is an import cycle.
from sandbox_executor import local_llm


@dataclass(frozen=True)
class RunnerSpec:
    agent_id: str
    image: str
    default_model: str
    ticker: str | None
    api_key_env_vars: tuple[str, ...]
    credential_check: Callable[[RunnerSpec], str | None] = field(repr=False, compare=False)

    def check_credentials(self) -> str | None:
        return self.credential_check(self)


def has_api_key(spec: RunnerSpec) -> bool:
    return any(os.getenv(name) for name in spec.api_key_env_vars)


def any_path_exists(paths: Iterable[str]) -> bool:
    return any(os.path.exists(p) or os.path.exists(os.path.expanduser(p)) for p in paths)


def generic_missing_message(agent_id: str) -> str:
    return (
        f"Error: Missing required API credentials for agent '{agent_id}'.\n"
        "Please set 'HOLON_AGENT_KEY', mount session credentials, or select a host-local model with "
        f"'{local_llm.ENV_LOCAL_MODE}=1' plus a reachable endpoint."
    )
