from __future__ import annotations

import os
from collections.abc import Mapping

from sandbox_executor import local_llm
from sandbox_executor.runners.spec import (
    ModelCapability,
    RunnerSpec,
    any_path_exists,
    generic_missing_message,
    has_api_key,
)

CONTAINER_HOME = "/home/holon"
OWNED_TMPFS_OPTIONS = "uid=1000,gid=1000,mode=0700"
_MODELS_JSON_FILENAME = "models.json"
_MODELS_JSON_CANDIDATES = (
    (os.path.join(".pi", "agent"), f"{CONTAINER_HOME}/.pi/agent"),
    (os.path.join(".config", "pi"), f"{CONTAINER_HOME}/.config/pi"),
)
_ALWAYS_WRITABLE_DIRS = (f"{CONTAINER_HOME}/.pi", f"{CONTAINER_HOME}/.pi/agent")
_LOCAL_LLM_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})

# why: the launcher mounts only models.json, not its directory holding auth.json and transcripts, so it is the session.
_MODELS_JSON_PATHS = (
    "/home/holon/.pi/agent/models.json",
    "~/.pi/agent/models.json",
    "/home/holon/.config/pi/models.json",
    "~/.config/pi/models.json",
)


def _owned_tmpfs(container_dir: str) -> list[str]:
    return ["--tmpfs", f"{container_dir}:{OWNED_TMPFS_OPTIONS}"]


def _owned_parent_tmpfs(container_dir: str) -> list[str]:
    return [
        *_owned_tmpfs(os.path.dirname(container_dir)),
        *_owned_tmpfs(container_dir),
    ]


def get_pi_mounts(home: str) -> list[str]:
    args: list[str] = []
    for container_dir in _ALWAYS_WRITABLE_DIRS:
        args.extend(_owned_tmpfs(container_dir))
    for host_dir, container_dir in _MODELS_JSON_CANDIDATES:
        host_file = os.path.join(home, host_dir, _MODELS_JSON_FILENAME)
        if not os.path.exists(host_file):
            continue
        if container_dir not in _ALWAYS_WRITABLE_DIRS:
            args.extend(_owned_parent_tmpfs(container_dir))
        args.extend(["-v", f"{host_file}:{container_dir}/{_MODELS_JSON_FILENAME}:ro"])
    return args


def _pi_session_mounts(home: str) -> list[str]:
    return [] if local_llm.local_llm_requested() else get_pi_mounts(home)


def _detect_local_gateway(env: Mapping[str, str]) -> bool:
    return env.get("HOLON_LOCAL_LLM", "").strip().lower() in _LOCAL_LLM_TRUTHY_VALUES


def _local_llm_configured() -> bool:
    # why: pi-only, as the artefact is a pi agent directory and a stray opt-in must not mask another runner's key.
    return local_llm.local_llm_requested() and bool(
        os.getenv(local_llm.ENV_PI_AGENT_DIR) or os.getenv(local_llm.ENV_LOCAL_BASE_URL)
    )


def _models_json_paths() -> list[str]:
    paths = list(_MODELS_JSON_PATHS)
    env_pi_dir = os.getenv(local_llm.ENV_PI_AGENT_DIR, "").strip()
    if env_pi_dir:
        paths.insert(0, os.path.join(env_pi_dir, "models.json"))
    return paths


def _check_credentials(spec: RunnerSpec) -> str | None:
    if _local_llm_configured():
        return None
    if any_path_exists(_models_json_paths()) or has_api_key(spec):
        return None
    return generic_missing_message(spec.agent_id)


SPEC = RunnerSpec(
    agent_id="pi",
    image="holon/agent-pi",
    default_model="claude-opus-5-5",
    ticker="PIA",
    api_key_env_vars=("HOLON_AGENT_KEY",),
    credential_check=_check_credentials,
    model_capabilities=(
        ModelCapability(
            runner="pi-agent",
            canonical_model="claude-opus-5-5",
            aliases=["pi-default"],
            effort_levels=[],
            fallback_tier="Secondary (Cloud/CLI)",
        ),
        ModelCapability(
            runner="pi-agent",
            canonical_model="gpt-5-codex",
            aliases=["codex"],
            effort_levels=[],
            fallback_tier="Secondary (Cloud/CLI)",
        ),
    ),
    session_mounts_builder=_pi_session_mounts,
    auth_api_key_env_vars=("PI_API_KEY",),
    session_detection_paths=_MODELS_JSON_PATHS,
    harness_markers=(("PI_CODING_AGENT", "exact"),),
    harness_marker_priority=10,
    local_gateway_detector=_detect_local_gateway,
)
