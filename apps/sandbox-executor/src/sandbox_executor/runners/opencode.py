from __future__ import annotations

from sandbox_executor.runners.spec import RunnerSpec, generic_missing_message, has_api_key


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
)
