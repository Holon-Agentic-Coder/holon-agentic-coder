from __future__ import annotations

from sandbox_executor.runners.spec import ModelCapability, RunnerSpec, generic_missing_message, has_api_key


def _check_credentials(spec: RunnerSpec) -> str | None:
    if has_api_key(spec):
        return None
    return generic_missing_message(spec.agent_id)


SPEC = RunnerSpec(
    agent_id="opencode",
    image="holon/agent-opencode",
    default_model="anthropic/claude-opus-5-5",
    ticker=None,
    api_key_env_vars=("HOLON_AGENT_KEY",),
    credential_check=_check_credentials,
    host_api_key_env_vars=("OPENCODE_API_KEY",),
    harness_markers=(("OPENCODE", "exact"), ("OPENCODE_", "prefix")),
    harness_marker_priority=40,
    non_marker_vars=("OPENCODE_API_KEY", "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR"),
    model_capabilities=(
        ModelCapability(
            runner="opencode-agent",
            canonical_model="anthropic/claude-opus-5-5",
            aliases=["opencode"],
            effort_levels=[],
            fallback_tier="Secondary (Cloud/CLI)",
        ),
    ),
)
