from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sandbox_executor.budgets import MAX_REPLAN_ATTEMPTS
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import TEST_MEASUREMENT_KEYS, _stage_failure
from sandbox_executor.flow.git_refs import _append_superseding_execution_row
from sandbox_executor.flow.in_flight import read_plan_touch_set
from sandbox_executor.module_budget_guard import (
    MODULE_BUDGET_CHANGE_REFUSED,
    budget_config_changes,
    module_budget_change_authorised,
    refusal_message,
)
from sandbox_executor.touch_set import out_of_set_files

TOUCH_SET_VIOLATION = "touch_set_violation"


def superseding_failure_row(
    summary_row: dict[str, Any], status: str, failure_reason: str, summary: str
) -> dict[str, Any]:
    superseding = {key: value for key, value in summary_row.items() if key not in TEST_MEASUREMENT_KEYS}
    try:
        prior_revision = int(summary_row.get("ledger_revision") or 1)
    except (TypeError, ValueError):
        prior_revision = 1
    superseding.update(
        {
            "status": status,
            "failure_reason": failure_reason,
            "summary": summary,
            "created_at": datetime.now(UTC).isoformat(),
            "ledger_revision": max(2, prior_revision + 1),
        }
    )
    return superseding


def touch_set_violation(context: FlowContext, changed: list[str]) -> list[str]:
    declared = read_plan_touch_set(context.target_worktree, str(context.plan_branch)) if context.plan_branch else None
    return out_of_set_files(declared, changed) if declared is not None else []


def _record_refusal(
    context: FlowContext, summary_row: dict[str, Any], base_payload: dict[str, Any], reason: str, summary: str
) -> dict[str, Any]:
    exec_branch = str(base_payload["execution_branch"])
    row = superseding_failure_row(summary_row, "failed", reason, summary)
    write_error = _append_superseding_execution_row(context, exec_branch, row)
    if write_error:
        what = reason.replace("_", " ").replace("touch set", "touch-set")
        context.log(f"Warning: could not record the {what} row on '{exec_branch}': {write_error}")
    return {
        **base_payload,
        "status": "failed",
        "container_status": base_payload.get("status"),
        "failure_reason": reason,
        "superseding_row_recorded": write_error is None,
    }


def _budget_refusal_result(
    context: FlowContext, start_time: str, summary_row: dict[str, Any], changed: list[str], base_payload: dict[str, Any]
) -> StageResult | None:
    paths = [] if module_budget_change_authorised(context.intent_data) else budget_config_changes(changed)
    if not paths:
        return None
    summary = refusal_message(paths)
    payload = _record_refusal(context, summary_row, base_payload, MODULE_BUDGET_CHANGE_REFUSED, summary)
    payload[MODULE_BUDGET_CHANGE_REFUSED] = paths
    return _stage_failure(context, FlowStage.EXECUTE, start_time, f"Execute stage failed: {summary}", **payload)


def touch_set_violation_result(
    context: FlowContext, start_time: str, summary_row: dict[str, Any], changed: list[str], base_payload: dict[str, Any]
) -> StageResult | None:
    # why: a budget configuration change is refused without a re-plan; the refusal tells the agent to split the module.
    refused = _budget_refusal_result(context, start_time, summary_row, changed, base_payload)
    if refused is not None:
        return refused
    files = touch_set_violation(context, changed)
    if not files:
        return None
    summary = f"the execution changed {len(files)} file(s) outside the plan's touch set: {', '.join(files)}"
    payload = _record_refusal(context, summary_row, base_payload, TOUCH_SET_VIOLATION, summary)
    payload.update({TOUCH_SET_VIOLATION: files, "replan_requested": True})
    error = f"Execute stage failed: {summary}; the plan returns to Stage 2 to widen its touch set."
    return _stage_failure(context, FlowStage.EXECUTE, start_time, error, **payload)


def touch_set_replan_feedback(plan_branch: str, files: list[str], attempt: int) -> str:
    return (
        f"The execution of the previous plan '{plan_branch}' changed files outside its touch set: {', '.join(files)}. "
        f"Re-plan attempt {attempt} of {MAX_REPLAN_ATTEMPTS}: widen the '## Touch Set' section to include these "
        "files, or decompose the plan."
    )


def touch_set_replan_exhausted(files: list[str], attempts: int) -> str:
    return (
        f"Execute stage failed: the execution changed files outside the plan's touch set ({', '.join(files)}) after "
        f"{attempts} re-plan attempts."
    )
