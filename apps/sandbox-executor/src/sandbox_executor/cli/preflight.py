"""CLI runner/model resolution and pre-flight readiness gate."""

import logging
import sys

from sandbox_executor.agent_defaults import (
    Resolution,
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
    """Resolve the agent runner and model for a command.

    The resolvers log each decision at INFO, which :func:`configure_cli_logging` routes to stderr.

    Args:
        agent: Raw ``--agent`` value (None when omitted).
        model: Raw ``--model`` value (None when omitted).

    Returns:
        ``(agent resolution, model resolution)``.

    Raises:
        MissingAgentRunnerError: If no agent runner was provided, configured, or detected.
        MissingModelError: If no model was provided or configured.
        UnmappedAgentRunnerError: If the selected runner has no container image.
    """
    agent_res = resolve_agent_runner(agent)
    model_res = resolve_model(agent_res.value, model)
    return agent_res, model_res


# Resumption points from which a flow still reaches a stage that needs the agent runner: plan and execute, and review,
# whose loop needs the runner for its reviewer turns (Stage 4 dispatches reviewer turns through _dispatch_role into
# the review-loop role container). A flow resumed at review must preflight the runner too; only calibration (git and
# local checks) has nothing for the probe to protect.
_AGENT_FLOW_STAGES = tuple(s.value for s in FlowStage if s not in (FlowStage.CALIBRATE, FlowStage.COMPLETED))


def flow_dispatches_agent(from_stage: str | None) -> bool:
    """Return True when a flow starting at ``from_stage`` still reaches a stage that dispatches the agent runner.

    Args:
        from_stage: Resumption stage name, or None for a run from the start.

    Returns:
        False only when the flow resumes at a stage after the last agent-dispatching stage.
    """
    if not from_stage:
        return True
    return str(from_stage).strip().lower() in _AGENT_FLOW_STAGES


def preflight_gate(agent: str, model: str, skip: bool = False) -> PreflightResult | None:
    """Run the readiness probe unless skipped and print the diagnosis when it fails.

    Must be called before any branch, checkpoint or ledger write.

    Args:
        agent: Resolved runner name.
        model: Resolved model name.
        skip: True when ``--skip-preflight`` was given.

    Returns:
        The probe result, or None when the probe was skipped by flag or ``HOLON_SKIP_PREFLIGHT``.
    """
    if skip or preflight_skipped_by_env():
        logger.info("Pre-flight probe skipped (--skip-preflight or HOLON_SKIP_PREFLIGHT).")
        return None
    result = run_preflight_probe(agent, model, timeout=resolve_timeout())
    if not result.ok:
        print(result.format_report(), file=sys.stderr)
        print(
            "Aborting before any branch, checkpoint or ledger write. Use --skip-preflight to bypass this check.",
            file=sys.stderr,
        )
    return result
