from __future__ import annotations

from sandbox_executor import claude_keychain
from sandbox_executor.claude_credential import CLAUDE_CREDENTIAL_ENV_NAMES
from sandbox_executor.runners.spec import RunnerSpec, any_path_exists, has_api_key

_SESSION_PATHS = ("/home/holon/.config/claude", "~/.config/claude")


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
)
