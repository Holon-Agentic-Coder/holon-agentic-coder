import logging
import sys
import time
from collections.abc import Callable

from sandbox_executor import local_llm, preflight_cooldown, preflight_outage
from sandbox_executor.agent_defaults import (
    AGENT_IMAGE_MAPPING,
    Resolution,
    normalise_agent_id,
    resolve_agent_runner,
    resolve_model,
)
from sandbox_executor.flow import FlowStage
from sandbox_executor.preflight import (
    PreflightResult,
    preflight_skipped_by_env,
    resolve_timeout,
    run_preflight_probe,
)

logger = logging.getLogger(__name__)


def resolve_runner_and_model(agent: str | None, model: str | None) -> tuple[Resolution, Resolution]:
    agent_res = resolve_agent_runner(agent)
    model_res = resolve_model(agent_res.value, model)
    return agent_res, model_res


# why: review resumptions still dispatch reviewer turns through the runner, so only calibration skips the probe.
_AGENT_FLOW_STAGES = tuple(s.value for s in FlowStage if s not in (FlowStage.CALIBRATE, FlowStage.COMPLETED))


def flow_dispatches_agent(from_stage: str | None) -> bool:
    if not from_stage:
        return True
    return str(from_stage).strip().lower() in _AGENT_FLOW_STAGES


def _print_preflight_failure(result: PreflightResult, *, gave_up: bool, outage_seconds: float) -> None:
    print(result.format_report(), file=sys.stderr)
    if gave_up:
        print(preflight_outage.format_outage_duration(outage_seconds), file=sys.stderr)
    print(
        "Aborting before any branch, checkpoint or ledger write. Use --skip-preflight to bypass this check.",
        file=sys.stderr,
    )


def preflight_gate(
    agent: str,
    model: str,
    skip: bool = False,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> PreflightResult | None:
    # why: must run before any branch, checkpoint or ledger write.
    if skip or preflight_skipped_by_env():
        logger.info("Pre-flight probe skipped (--skip-preflight or HOLON_SKIP_PREFLIGHT).")
        return None
    agent_id = normalise_agent_id(agent)
    image = AGENT_IMAGE_MAPPING.get(agent_id, f"holon/agent-{agent_id}")
    if not (agent_id == "pi" and local_llm.local_llm_requested()) and (
        short_circuit := preflight_cooldown.short_circuit_result(agent, model, image)
    ):
        _print_preflight_failure(short_circuit, gave_up=False, outage_seconds=0.0)
        return short_circuit
    started_at = clock()
    timeout = resolve_timeout()

    def probe() -> PreflightResult:
        return run_preflight_probe(agent, model, timeout=timeout)

    outcome = preflight_outage.wait_out_outage(probe, probe(), started_at, sleep=sleep, clock=clock)
    result = outcome.result
    if not (isinstance(result, PreflightResult) and result.short_circuited):
        preflight_cooldown.sync_probe_result(result)
    if not result.ok:
        _print_preflight_failure(result, gave_up=outcome.gave_up, outage_seconds=outcome.outage_seconds)
    return result
