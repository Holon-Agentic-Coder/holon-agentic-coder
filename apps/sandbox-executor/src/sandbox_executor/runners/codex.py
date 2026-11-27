from __future__ import annotations

import os

from sandbox_executor.runners.spec import ModelCapability, RunnerSpec, SessionMountRule, has_api_key


def _oss_mode_enabled() -> bool:
    return os.getenv("HOLON_AGENT_OSS_MODE") in ("true", "1")


def _check_credentials(spec: RunnerSpec) -> str | None:
    if _oss_mode_enabled():
        return None
    has_session = os.path.exists("/home/holon/.codex") or os.path.exists(os.path.expanduser("~/.codex"))
    if has_api_key(spec) or has_session:
        return None
    return (
        "Error: Missing required credentials for agent 'codex'.\n"
        "Please set 'HOLON_AGENT_KEY', set 'HOLON_AGENT_OSS_MODE=true', "
        "or mount active credentials to '/home/holon/.codex'."
    )


SPEC = RunnerSpec(
    agent_id="codex",
    image="holon/agent-codex",
    default_model="gpt-5-codex",
    ticker=None,
    api_key_env_vars=("HOLON_AGENT_KEY",),
    credential_check=_check_credentials,
    host_api_key_env_vars=("OPENAI_API_KEY", "CODEX_API_KEY"),
    session_detect_paths=("~/.codex", "/home/holon/.codex"),
    harness_markers=(("CODEX_", "prefix"),),
    harness_marker_priority=30,
    non_marker_vars=("CODEX_API_KEY", "CODEX_HOME"),
    model_capabilities=(
        ModelCapability(
            runner="codex-agent",
            canonical_model="gpt-5-codex",
            aliases=["codex"],
            effort_levels=[],
            fallback_tier="Secondary (Cloud/CLI)",
        ),
    ),
    session_mount_rules=(SessionMountRule(".codex", "/home/holon/.codex", tmpfs_subdirs=("log", "cache")),),
)
