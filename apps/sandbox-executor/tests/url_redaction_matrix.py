"""Inputs of the URL credential redaction matrix that do not depend on the credential, built once per module.

``tests/test_url_credential_redaction.py`` runs thousands of cases per slice. Each case used to rebuild the same
pre-flight probe result, the same doctor argument sets and the same encoded health payload. Those are built here once,
and every case reads them instead:

- the probe result is a frozen dataclass, so one object serves every case;
- the doctor argument sets are handed out as new ``argparse.Namespace`` objects, so a doctor run that set an attribute
  on its arguments could not reach the next case;
- the health payload is ``bytes``, which nothing can change.

Nothing here holds a credential, a temp path or any other per-case value.
"""

import argparse
import json

from sandbox_executor.preflight import PreflightResult, PreflightStatus

DOCTOR_PROBE = PreflightResult(
    status=PreflightStatus.OK, agent="pi-agent", model="m", image="img", exit_code=0, output_excerpt="", hint=""
)

# A health response that drives every #94 doctor line: the cache state, memory figures, and both envelope warnings.
HEALTH_TIGHT = {
    "prefill_cache": {"enabled": True, "entries": 1, "memory_bytes": 5},
    "memory": {"used_bytes": 1},
    "effective_defaults": {"max_output_tokens": 1, "max_prompt_tokens": 1},
}
HEALTH_TIGHT_BYTES = json.dumps(HEALTH_TIGHT).encode()

_DOCTOR_ARGS = {"agent": "antigravity-agent", "model": "gemini-3.8-flash-medium", "timeout": None}


def doctor_args(as_json: bool) -> argparse.Namespace:
    """The ``holon doctor`` arguments of every matrix case, as a new object per call: text or ``--json``."""
    return argparse.Namespace(**_DOCTOR_ARGS, json=as_json)


def probe_stand_in(*_args, **_kwargs) -> PreflightResult:
    """``cli_doctor.run_preflight_probe`` stand-in: the shared OK result, for any runner and model."""
    return DOCTOR_PROBE
