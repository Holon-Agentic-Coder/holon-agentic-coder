from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.test_evidence import TEST_MEASUREMENT_KEYS as TEST_MEASUREMENT_KEYS
from sandbox_executor.flow.test_evidence import _effective_exit_code, recorded_test_pass_rate
from sandbox_executor.flow.test_evidence import _recorded_test_fields as _recorded_test_fields
from sandbox_executor.module_budget_guard import module_budget_flag


def _test_evidence(exec_result: StageResult | None) -> tuple[float | None, int]:
    if not exec_result or not exec_result.payload:
        return None, 0
    return recorded_test_pass_rate(exec_result.payload.get("test_pass_rate")), _effective_exit_code(exec_result.payload)


def _get_dispatcher(context: FlowContext) -> Callable[..., int]:
    if context.dispatcher is not None:
        return context.dispatcher
    # why: deferred only to keep importing the flow engine light; the launcher never imports the flow engine.
    from sandbox_executor.launcher import role_dispatch

    return role_dispatch.dispatch_role_agent


def _dispatch_role(
    context: FlowContext,
    role: str,
    positional_args: list[str],
    intent_file: str | None = None,
    result_file: str | None = None,
    **extra: Any,
) -> int:
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
    # why: forwarded only when set, so dispatchers see the same keywords as before when it is off.
    if getattr(context, "proxy_attach", False):
        kwargs["proxy_attach"] = True
    # why: None extras are dropped so injected dispatchers without those parameters keep working.
    kwargs.update({key: value for key, value in extra.items() if value is not None})
    with module_budget_flag(context.intent_data):
        return int(dispatcher(role, context.agent, context.model, positional_args, **kwargs))


def _stage_failure(context: FlowContext, stage: FlowStage, start_time: str, error: str, **payload: Any) -> StageResult:
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
