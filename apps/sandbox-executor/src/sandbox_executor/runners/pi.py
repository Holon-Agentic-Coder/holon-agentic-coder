"""Runner spec for the Pi agent, including its host-local LLM exemption."""

from __future__ import annotations

import os

from sandbox_executor import local_llm
from sandbox_executor.runners.spec import RunnerSpec, any_path_exists, generic_missing_message, has_api_key

# pi resolves providers from models.json; that single file is what the launcher mounts, so that is what counts as an
# available session here -- not the surrounding directory, which holds auth.json and transcripts.
_MODELS_JSON_PATHS = (
    "/home/holon/.pi/agent/models.json",
    "~/.pi/agent/models.json",
    "/home/holon/.config/pi/models.json",
    "~/.config/pi/models.json",
)


def _local_llm_configured() -> bool:
    # A host-local inference server addressed through the Docker gateway needs no provider credential. Only accepted
    # for pi -- the generated artifact is a pi agent directory, so a pi-only gate keeps a stray opt-in from masking a
    # missing cloud key for another runner -- and only when the local endpoint is actually configured.
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
