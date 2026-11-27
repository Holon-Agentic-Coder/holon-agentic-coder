import argparse
import json

from sandbox_executor.preflight import PreflightResult, PreflightStatus

# why: the matrix runs thousands of cases per slice, so its shared inputs are built once and never mutated
DOCTOR_PROBE = PreflightResult(
    status=PreflightStatus.OK, agent="pi-agent", model="m", image="img", exit_code=0, output_excerpt="", hint=""
)

# why: tight limits make one response drive every #94 doctor line, including both envelope warnings
HEALTH_TIGHT = {
    "prefill_cache": {"enabled": True, "entries": 1, "memory_bytes": 5},
    "memory": {"used_bytes": 1},
    "effective_defaults": {"max_output_tokens": 1, "max_prompt_tokens": 1},
}
HEALTH_TIGHT_BYTES = json.dumps(HEALTH_TIGHT).encode()

_DOCTOR_ARGS = {"agent": "antigravity-agent", "model": "gemini-3.8-flash-medium", "timeout": None}


def doctor_args(as_json: bool) -> argparse.Namespace:
    # why: a doctor run that sets an attribute on its arguments must not reach the next matrix case
    return argparse.Namespace(**_DOCTOR_ARGS, json=as_json)


def probe_stand_in(*_args, **_kwargs) -> PreflightResult:
    return DOCTOR_PROBE
