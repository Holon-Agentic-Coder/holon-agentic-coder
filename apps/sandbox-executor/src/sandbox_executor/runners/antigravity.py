from __future__ import annotations

import os
import sys

from sandbox_executor.runners.spec import (
    _VALID_EFFORT_LEVELS,
    RunnerSpec,
    _ExtendedRunnerSpec,
    _ModelCapability,
    has_api_key,
)

_SESSION_PATHS = ("/home/holon/.gemini/antigravity-cli", "/home/holon/.gemini", "/home/holon/.config/gcloud")
_HOME_SESSION_PATHS = ("~/.gemini/antigravity-cli", "~/.gemini", "~/.config/gcloud", "~/.holon/sessions/antigravity")
_ANTIGRAVITY_CLI_CONTAINER_DIR = "/home/holon/.gemini/antigravity-cli"
_WRITABLE_TMPFS: tuple[str, ...] = (
    "/home/holon/.gemini/config:uid=1000,gid=1000",
    f"{_ANTIGRAVITY_CLI_CONTAINER_DIR}/log:uid=1000,gid=1000",
    f"{_ANTIGRAVITY_CLI_CONTAINER_DIR}/cache:uid=1000,gid=1000",
    f"{_ANTIGRAVITY_CLI_CONTAINER_DIR}/brain:uid=1000,gid=1000",
    f"{_ANTIGRAVITY_CLI_CONTAINER_DIR}/conversations:uid=1000,gid=1000",
)


def _check_credentials(spec: RunnerSpec) -> str | None:
    has_session = any(os.path.exists(p) for p in _SESSION_PATHS) or any(
        os.path.exists(os.path.expanduser(p)) for p in _HOME_SESSION_PATHS
    )
    if has_api_key(spec) or has_session:
        return None
    return (
        "Error: Missing required API credentials for agent 'antigravity'.\n"
        "Please set 'HOLON_AGENT_KEY' or mount active session to '/home/holon/.gemini'."
    )


def _coordinate_gemini_effort(canonical: str, effort: str | None) -> tuple[str, str | None]:
    eff = effort.strip().lower() if effort and effort.strip() else None

    inferred_effort = None
    base = canonical
    for level in _VALID_EFFORT_LEVELS:
        suffix = f"-{level}"
        if canonical.endswith(suffix):
            inferred_effort = level
            base = canonical[: -len(suffix)]
            break

    if eff in _VALID_EFFORT_LEVELS:
        return f"{base}-{eff}", eff
    if inferred_effort:
        return canonical, inferred_effort
    return canonical, None


def _antigravity_session_mounts(source: str) -> list[str]:
    mounts = ["-v", f"{source}:{_ANTIGRAVITY_CLI_CONTAINER_DIR}:ro"]
    for spec in _WRITABLE_TMPFS:
        mounts.extend(["--tmpfs", spec])
    return mounts


def _abort_missing_macos_antigravity_session(dedicated_session: str) -> None:
    print(
        "Error: Missing Antigravity sandbox session directory on macOS.\n"
        f"The session directory '{dedicated_session}' does not exist.\n\n"
        "Please initialise the Antigravity session in an interactive TTY by running:\n\n"
        f"  mkdir -p {dedicated_session}\n"
        "  docker run -it "
        f"-v {dedicated_session}:/home/holon/.gemini/antigravity-cli:rw holon/agent-antigravity agy\n\n"
        "After completing interactive authentication, rerun this command.",
        file=sys.stderr,
    )
    sys.exit(1)


def _build_session_mounts(home: str) -> list[str]:
    dedicated_session = os.getenv(
        "HOLON_ANTIGRAVITY_SESSION_DIR",
        os.path.join(home, ".holon", "sessions", "antigravity"),
    )
    has_key = bool(os.getenv("HOLON_AGENT_KEY"))

    if os.path.exists(dedicated_session):
        return _antigravity_session_mounts(dedicated_session)

    if sys.platform == "darwin" and not has_key:
        _abort_missing_macos_antigravity_session(dedicated_session)

    host_agy_cli = os.path.join(home, ".gemini", "antigravity-cli")
    if os.path.exists(host_agy_cli):
        return _antigravity_session_mounts(host_agy_cli)

    return []


SPEC: RunnerSpec = _ExtendedRunnerSpec(
    agent_id="antigravity",
    image="holon/agent-antigravity",
    default_model="gemini-3.8-flash-medium",
    ticker="ANA",
    api_key_env_vars=("HOLON_AGENT_KEY",),
    credential_check=_check_credentials,
    host_api_key_env_vars=("AGY_USER_TOKEN", "GOOGLE_API_KEY", "GEMINI_API_KEY"),
    session_detect_paths=(
        "~/.gemini/antigravity-cli",
        "~/.gemini",
        "~/.holon/sessions/antigravity",
        "/home/holon/.gemini/antigravity-cli",
        "/home/holon/.gemini",
    ),
    harness_markers=(("AGY_", "prefix"), ("ANTIGRAVITY_", "prefix")),
    harness_marker_priority=50,
    non_marker_vars=("AGY_USER_TOKEN", "GOOGLE_API_KEY"),
    model_capabilities=(
        _ModelCapability(
            runner="antigravity-agent",
            canonical_model="gemini-3.8-flash",
            aliases=["flash"],
            effort_levels=list(_VALID_EFFORT_LEVELS),
            fallback_tier="Primary (Cloud/CLI)",
        ),
        _ModelCapability(
            runner="antigravity-agent",
            canonical_model="gemini-3.8-flash-medium",
            aliases=["flash-medium"],
            effort_levels=list(_VALID_EFFORT_LEVELS),
            fallback_tier="Primary (Cloud/CLI)",
        ),
        _ModelCapability(
            runner="antigravity-agent",
            canonical_model="gemini-3.8-flash-high",
            aliases=["flash-high"],
            effort_levels=list(_VALID_EFFORT_LEVELS),
            fallback_tier="Primary (Cloud/CLI)",
        ),
    ),
    supports_effort_suffix=True,
    normalise_model_effort=_coordinate_gemini_effort,
    session_mounts_builder=_build_session_mounts,
)
