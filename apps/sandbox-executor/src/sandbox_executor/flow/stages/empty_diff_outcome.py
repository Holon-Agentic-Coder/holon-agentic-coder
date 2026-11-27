from __future__ import annotations

from typing import Any

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.git_refs import _append_superseding_execution_row
from sandbox_executor.flow.partial_head import FAILED_EXECUTION_STATUSES
from sandbox_executor.flow.stages.touch_set_guard import superseding_failure_row
from sandbox_executor.git import EMPTY_DIFF_FAILURE_REASON, EMPTY_DIFF_STATUS, empty_diff_summary


def empty_diff_failure(
    context: FlowContext,
    start_time: str,
    exec_branch: str,
    summary_row: dict[str, Any] | None,
    base_payload: dict[str, Any],
) -> StageResult:
    guard_summary = empty_diff_summary(context.plan_branch)
    outcome, recorded = _empty_diff_outcome(context, exec_branch, summary_row, base_payload, guard_summary)
    guard_payload = {
        **base_payload,
        "status": EMPTY_DIFF_STATUS,
        "failure_reason": EMPTY_DIFF_FAILURE_REASON,
        "container_status": base_payload["status"],
        "superseding_row_recorded": recorded,
    }
    return _stage_failure(
        context,
        FlowStage.EXECUTE,
        start_time,
        f"Execute stage failed: {guard_summary}; {outcome}; no PR will be created or updated.",
        **guard_payload,
    )


def _empty_diff_outcome(
    context: FlowContext,
    exec_branch: str,
    summary_row: dict[str, Any] | None,
    base_payload: dict[str, Any],
    guard_summary: str,
) -> tuple[str, bool]:
    exec_id, container_status = base_payload["execution_id"], base_payload["status"]
    agent_exit = f"the executor agent exited with code {base_payload['exit_code']}"
    if summary_row is None:
        outcome = (
            f"{agent_exit} but '{exec_branch}' carries no executions.jsonl summary row for {exec_id}, "
            "so no superseding row was recorded"
        )
        return outcome, False
    if container_status in FAILED_EXECUTION_STATUSES:
        detail = summary_row.get("summary") or "no summary"
        outcome = (
            f"{agent_exit} and the container already recorded status '{container_status}': {detail}; "
            "no superseding row was written"
        )
        return outcome, False
    superseding = superseding_failure_row(summary_row, EMPTY_DIFF_STATUS, EMPTY_DIFF_FAILURE_REASON, guard_summary)
    write_error = _append_superseding_execution_row(context, exec_branch, superseding)
    if write_error is not None:
        context.log(f"Warning: could not record the empty-diff failure row on '{exec_branch}': {write_error}")
        outcome = (
            f"{agent_exit} and the container recorded status '{container_status}', but recording the "
            f"superseding '{EMPTY_DIFF_STATUS}' row (failure_reason '{EMPTY_DIFF_FAILURE_REASON}') failed, "
            f"so the ledger still shows '{container_status}': {write_error}"
        )
        return outcome, False
    outcome = (
        f"{agent_exit} and the container recorded status '{container_status}'; recorded a superseding "
        f"status '{EMPTY_DIFF_STATUS}' row (failure_reason '{EMPTY_DIFF_FAILURE_REASON}')"
    )
    return outcome, True
