# why: this package must not import agent_defaults or agent_runner; they derive tables from these specs (cycle).
from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from sandbox_executor import local_llm

VALID_EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high")
_FALSY_VALUES = frozenset({"", "0", "false", "no", "off"})


@dataclass(frozen=True)
class ModelCapability:
    runner: str
    canonical_model: str
    aliases: list[str]
    effort_levels: list[str]
    fallback_tier: str


def _is_set(value: str | None) -> bool:
    return value is not None and value.strip().lower() not in _FALSY_VALUES


def _empty_session_mounts(_home: str) -> list[str]:
    return []


def capability_matches_model(capability: ModelCapability, model_lower: str) -> bool:
    return model_lower == capability.canonical_model.lower() or model_lower in (
        alias.lower() for alias in capability.aliases
    )


def readonly_session_mount_builder(
    host_rel_path: str,
    container_path: str,
    writable_tmpfs_subpaths: tuple[str, ...] = (),
) -> Callable[[str], list[str]]:
    def _build(home: str) -> list[str]:
        host_path = os.path.join(home, host_rel_path)
        if not os.path.exists(host_path):
            return []
        mounts = ["-v", f"{host_path}:{container_path}:ro"]
        for subpath in writable_tmpfs_subpaths:
            mounts.extend(["--tmpfs", f"{container_path}/{subpath}:uid=1000,gid=1000"])
        return mounts

    return _build


@dataclass(frozen=True)
class RunnerSpec:
    agent_id: str
    image: str
    default_model: str
    ticker: str | None
    api_key_env_vars: tuple[str, ...]
    credential_check: Callable[[RunnerSpec], str | None] = field(repr=False, compare=False)
    model_capabilities: tuple[ModelCapability, ...] = ()
    model_discovery_hook: Callable[[RunnerSpec], tuple[ModelCapability, ...]] | None = field(
        default=None, repr=False, compare=False
    )
    effort_normaliser: Callable[[str, str | None], tuple[str, str | None]] | None = field(
        default=None, repr=False, compare=False
    )
    supports_effort_suffix: bool = False
    session_mounts_builder: Callable[[str], list[str]] = field(
        default=_empty_session_mounts, repr=False, compare=False
    )
    auth_api_key_env_vars: tuple[str, ...] = ()
    accepts_generic_api_key: bool = True
    api_key_detector: Callable[[Mapping[str, str]], bool] | None = field(default=None, repr=False, compare=False)
    session_detection_paths: tuple[str, ...] = ()
    harness_markers: tuple[tuple[str, str], ...] = ()
    harness_marker_priority: int = 100
    non_marker_env_vars: tuple[str, ...] = ()
    local_gateway_detector: Callable[[Mapping[str, str]], bool] | None = field(default=None, repr=False, compare=False)

    def check_credentials(self) -> str | None:
        return self.credential_check(self)

    def discover_models(self) -> tuple[ModelCapability, ...]:
        if self.model_discovery_hook is not None:
            return self.model_discovery_hook(self)
        return self.model_capabilities

    def build_session_mounts(self, home: str) -> list[str]:
        return self.session_mounts_builder(home)

    def detect_api_key(self, env: Mapping[str, str], secret_bundle_path: str) -> bool:
        if self.accepts_generic_api_key and (
            os.path.isfile(secret_bundle_path) or _is_set(env.get("HOLON_AGENT_KEY"))
        ):
            return True
        if self.api_key_detector is not None:
            return self.api_key_detector(env)
        return any(_is_set(env.get(key)) for key in self.auth_api_key_env_vars)

    def detect_session_dir(self) -> bool:
        return any(os.path.exists(os.path.expanduser(path)) for path in self.session_detection_paths)

    def detect_local_gateway(self, env: Mapping[str, str]) -> bool:
        if self.local_gateway_detector is not None:
            return self.local_gateway_detector(env)
        return False

    def is_valid_model(self, model: str) -> bool:
        caps = self.discover_models()
        if not caps:
            return True
        model_lower = model.strip().lower()
        for cap in caps:
            if capability_matches_model(cap, model_lower):
                return True
            if self.supports_effort_suffix and any(
                model_lower == f"{cap.canonical_model.lower()}-{eff}" for eff in VALID_EFFORT_LEVELS
            ):
                return True
        return False

    def normalise_model_and_effort(self, model: str | None, effort: str | None = None) -> tuple[str, str | None]:
        raw_model = (model or "").strip() or self.default_model
        raw_lower = raw_model.lower()
        canonical = raw_model
        for cap in self.discover_models():
            if capability_matches_model(cap, raw_lower):
                canonical = cap.canonical_model
                break
        if self.effort_normaliser is not None:
            return self.effort_normaliser(canonical, effort)
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
