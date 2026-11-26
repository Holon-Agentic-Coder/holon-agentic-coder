"""Runner spec for the Antigravity agent."""

from __future__ import annotations

import os

from sandbox_executor.runners.spec import RunnerSpec, has_api_key

_SESSION_PATHS = ("/home/holon/.gemini/antigravity-cli", "/home/holon/.gemini", "/home/holon/.config/gcloud")
_HOME_SESSION_PATHS = ("~/.gemini/antigravity-cli", "~/.gemini", "~/.config/gcloud", "~/.holon/sessions/antigravity")


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


SPEC = RunnerSpec(
    agent_id="antigravity",
    image="holon/agent-antigravity",
    default_model="gemini-3.8-flash-medium",
    ticker="ANA",
    api_key_env_vars=("HOLON_AGENT_KEY",),
    credential_check=_check_credentials,
)
