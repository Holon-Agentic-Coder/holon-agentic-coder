from __future__ import annotations

from collections.abc import Mapping

from sandbox_executor import claude_keychain
from sandbox_executor.claude_credential import CLAUDE_CREDENTIAL_ENV_NAMES, resolve_claude_credential
from sandbox_executor.runners.spec import ModelCapability, RunnerSpec, SessionMountSpec, any_path_exists, has_api_key

_SESSION_PATHS = ("/home/holon/.config/claude", "~/.config/claude")


def _has_claude_api_key(env: Mapping[str, str]) -> bool:
    return resolve_claude_credential(env) is not None


def _check_credentials(spec: RunnerSpec) -> str | None:
    if has_api_key(spec) or any_path_exists(_SESSION_PATHS):
        return None
    return (
        "Error: Missing required credentials for agent 'claude'.\n"
        "Please store a token or API key from 'claude setup-token' in the host's OS keychain "
        f"({claude_keychain.store_commands_text()}), set 'CLAUDE_CODE_OAUTH_TOKEN' (sk-ant-oat...) or "
        "'ANTHROPIC_API_KEY' (sk-ant-api...), or mount a session. 'HOLON_AGENT_KEY' is not used for claude."
    )


SPEC = RunnerSpec(
    agent_id="claude",
    image="holon/agent-claude",
    default_model="claude-opus-5-5",
    ticker="CLA",
    api_key_env_vars=tuple(CLAUDE_CREDENTIAL_ENV_NAMES),
    credential_check=_check_credentials,
    model_capabilities=(
        ModelCapability(
            runner="claude-agent",
            canonical_model="claude-fable-5-2",
            aliases=["fable", "claude-fable"],
            effort_levels=[],
            fallback_tier="Primary (Cloud/CLI)",
        ),
        ModelCapability(
            runner="claude-agent",
            canonical_model="claude-opus-5-5",
            aliases=["opus", "claude-opus"],
            effort_levels=[],
            fallback_tier="Primary (Cloud/CLI)",
        ),
        ModelCapability(
            runner="claude-agent",
            canonical_model="claude-sonnet-4-6",
            aliases=["sonnet", "claude-sonnet"],
            effort_levels=[],
            fallback_tier="Primary (Cloud/CLI)",
        ),
        ModelCapability(
            runner="claude-agent",
            canonical_model="claude-haiku-4-5",
            aliases=["haiku", "claude-haiku"],
            effort_levels=[],
            fallback_tier="Primary (Cloud/CLI)",
        ),
    ),
    accepts_generic_agent_key=False,
    api_key_detector=_has_claude_api_key,
    session_detection_paths=("~/.config/claude", "~/.claude", "/home/holon/.config/claude"),
    harness_markers=(("CLAUDECODE", "exact"), ("CLAUDE_CODE_ENTRYPOINT", "exact")),
    harness_marker_priority=20,
    session_mount_specs=(SessionMountSpec(".config/claude", "/home/holon/.config/claude"),),
)
