"""Role-agent dispatch shared with 'holon intent', 'holon plan' and 'holon execute'.

Stages 1-3 dispatch the same containerised role agents as the CLI commands, so a flow run is
equivalent to invoking the stages by hand.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus


def recorded_test_pass_rate(value: Any) -> float | None:
    """Return a recorded test pass rate in [0, 1], or None when no usable measurement was recorded.

    ``None`` means "not measured": callers must never read it as a pass.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    rate = float(value)
    if math.isnan(rate) or rate < 0.0 or rate > 1.0:
        return None
    return rate


def _test_evidence(exec_result: StageResult | None) -> tuple[float | None, int]:
    """Return the Stage 3 ``(test_pass_rate, exit_code)``; the rate is None when Stage 3 measured no tests."""
    if not exec_result or not exec_result.payload:
        return None, 0
    return recorded_test_pass_rate(exec_result.payload.get("test_pass_rate")), int(
        exec_result.payload.get("exit_code", 0) or 0
    )


def _get_dispatcher(context: FlowContext) -> Callable[..., int]:
    """Return the role-agent dispatcher, defaulting lazily to cli.dispatch_role_agent.

    The import is deferred because cli imports this module at load time.
    """
    if context.dispatcher is not None:
        return context.dispatcher
    from sandbox_executor.cli import dispatch_role_agent

    return dispatch_role_agent


def _dispatch_role(
    context: FlowContext,
    role: str,
    positional_args: list[str],
    intent_file: str | None = None,
    result_file: str | None = None,
    **extra: Any,
) -> int:
    """Dispatch one role agent through the same function the per-stage commands use.

    Non-None ``extra`` keywords (e.g. ``workspace_mount`` for a host-provisioned worktree) are forwarded
    to the dispatcher; None values are dropped so injected dispatchers without those parameters keep
    working unchanged.
    """
    dispatcher = _get_dispatcher(context)
    context.log(f"Dispatching {role} agent (runner={context.agent}, model={context.model}).")
    kwargs: dict[str, Any] = {
        "target_worktree": context.target_worktree,
        "intent_file": intent_file,
        "token_reduce": bool(context.token_reduce),
        "mitm_web": bool(context.mitm_web),
    }
    if result_file is not None:
        kwargs["result_file"] = result_file
    kwargs.update({key: value for key, value in extra.items() if value is not None})
    return int(
        dispatcher(
            role,
            context.agent,
            context.model,
            positional_args,
            **kwargs,
        )
    )


def _stage_failure(context: FlowContext, stage: FlowStage, start_time: str, error: str, **payload: Any) -> StageResult:
    """Log and record a FAILED StageResult."""
    context.log(error)
    result = StageResult(
        stage=stage,
        status=StageStatus.FAILED,
        start_time=start_time,
        end_time=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        payload=dict(payload),
        error=error,
    )
    context.stage_results[stage.value] = result
    return result
