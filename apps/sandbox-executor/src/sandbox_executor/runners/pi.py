from __future__ import annotations

import os

from sandbox_executor import local_llm
from sandbox_executor.runners.spec import RunnerSpec, any_path_exists, generic_missing_message, has_api_key

# why: the launcher mounts only models.json, not its directory holding auth.json and transcripts, so it is the session.
_MODELS_JSON_PATHS = (
    "/home/holon/.pi/agent/models.json",
    "~/.pi/agent/models.json",
    "/home/holon/.config/pi/models.json",
    "~/.config/pi/models.json",
)


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
)
