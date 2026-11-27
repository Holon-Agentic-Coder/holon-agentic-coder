# why: this package must not import agent_defaults, agent_runner, or launcher.*; they derive tables from these specs (cycle).
from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from sandbox_executor import local_llm

VALID_EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high")


@dataclass(frozen=True)
class ModelCapability:
    runner: str
    canonical_model: str
    aliases: list[str]
    effort_levels: list[str]
    fallback_tier: str


@dataclass(frozen=True)
class SessionMountRule:
    host_rel: str
    container_path: str
    tmpfs_subdirs: tuple[str, ...] = ()
    tmpfs_options: str = "uid=1000,gid=1000"


def build_standard_session_mounts(home: str, rules: Iterable[SessionMountRule]) -> list[str]:
    mounts: list[str] = []
    for rule in rules:
        host_path = os.path.join(home, rule.host_rel)
        if not os.path.exists(host_path):
            continue
        mounts.extend(["-v", f"{host_path}:{rule.container_path}:ro"])
        for subdir in rule.tmpfs_subdirs:
            mounts.extend(["--tmpfs", f"{rule.container_path}/{subdir}:{rule.tmpfs_options}"])
    return mounts


@dataclass(frozen=True)
class RunnerSpec:
    agent_id: str
    image: str
    default_model: str
    ticker: str | None
    api_key_env_vars: tuple[str, ...]
    credential_check: Callable[[RunnerSpec], str | None] = field(repr=False, compare=False)
    host_api_key_env_vars: tuple[str, ...] = ()
    uses_holon_agent_key: bool = True
    api_key_detector: Callable[[Mapping[str, str]], bool] | None = field(default=None, repr=False, compare=False)
    session_detect_paths: tuple[str, ...] = ()
    supports_local_llm: bool = False
    harness_markers: tuple[tuple[str, str], ...] = ()
    harness_marker_priority: int = 100
    non_marker_vars: tuple[str, ...] = ()
    model_capabilities: tuple[ModelCapability, ...] = ()
    supports_effort_suffix: bool = False
    normalise_model_effort: Callable[[str, str | None], tuple[str, str | None]] | None = field(
        default=None, repr=False, compare=False
    )
    session_mount_rules: tuple[SessionMountRule, ...] = ()
    session_mounts_builder: Callable[[str], list[str]] | None = field(default=None, repr=False, compare=False)

    def check_credentials(self) -> str | None:
        return self.credential_check(self)

    def build_session_mounts(self, home: str) -> list[str]:
        if self.session_mounts_builder is not None:
            return self.session_mounts_builder(home)
        return build_standard_session_mounts(home, self.session_mount_rules)

    def detect_session_dir(self) -> bool:
        return any(os.path.exists(os.path.expanduser(p)) for p in self.session_detect_paths)

    def normalise_model(self, canonical: str, effort: str | None) -> tuple[str, str | None]:
        if self.normalise_model_effort is not None:
            return self.normalise_model_effort(canonical, effort)
        return canonical, None


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
