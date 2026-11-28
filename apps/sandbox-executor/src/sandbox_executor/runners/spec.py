# why: this package must not import agent_defaults or agent_runner; they derive tables from these specs (cycle).
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
class SessionMountSpec:
    host_rel_path: str
    container_path: str
    writable_tmpfs_subdirs: tuple[str, ...] = ()
    read_only: bool = True
    tmpfs_options: str = "uid=1000,gid=1000"


def build_declarative_session_mounts(home: str, specs: Iterable[SessionMountSpec]) -> list[str]:
    mounts: list[str] = []
    for spec in specs:
        host_path = os.path.join(home, spec.host_rel_path)
        if os.path.exists(host_path):
            mode = "ro" if spec.read_only else "rw"
            mounts.extend(["-v", f"{host_path}:{spec.container_path}:{mode}"])
            for subdir in spec.writable_tmpfs_subdirs:
                mounts.extend(["--tmpfs", f"{spec.container_path}/{subdir}:{spec.tmpfs_options}"])
    return mounts


@dataclass(frozen=True)
class RunnerSpec:
    agent_id: str
    image: str
    default_model: str
    ticker: str | None
    api_key_env_vars: tuple[str, ...]
    credential_check: Callable[[RunnerSpec], str | None] = field(repr=False, compare=False)
    model_capabilities: tuple[ModelCapability, ...] = ()
    model_normalizer: Callable[[str, str | None], tuple[str, str | None]] | None = field(
        default=None, repr=False, compare=False
    )
    model_discovery_hook: Callable[[RunnerSpec], tuple[ModelCapability, ...]] | None = field(
        default=None, repr=False, compare=False
    )
    provider_api_key_env_vars: tuple[str, ...] = ()
    accepts_generic_agent_key: bool = True
    api_key_detector: Callable[[Mapping[str, str]], bool] | None = field(default=None, repr=False, compare=False)
    session_detection_paths: tuple[str, ...] = ()
    supports_local_gateway: bool = False
    harness_markers: tuple[tuple[str, str], ...] = ()
    harness_marker_priority: int = 100
    non_marker_env_vars: frozenset[str] = frozenset()
    session_mount_specs: tuple[SessionMountSpec, ...] = ()
    session_mounts_builder: Callable[[str], list[str]] | None = field(default=None, repr=False, compare=False)

    def check_credentials(self) -> str | None:
        return self.credential_check(self)

    def build_session_mounts(self, home: str) -> list[str]:
        if self.session_mounts_builder is not None:
            return self.session_mounts_builder(home)
        return build_declarative_session_mounts(home, self.session_mount_specs)

    def discovered_models(self) -> tuple[ModelCapability, ...]:
        if self.model_discovery_hook is not None:
            return self.model_discovery_hook(self)
        return self.model_capabilities

    def normalize_model(self, canonical: str, effort: str | None = None) -> tuple[str, str | None]:
        if self.model_normalizer is not None:
            return self.model_normalizer(canonical, effort)
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
