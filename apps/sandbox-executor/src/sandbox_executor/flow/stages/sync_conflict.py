"""Stage 3 outcome for an execution branch that kept its commits but could not merge the target branch."""

from __future__ import annotations

from typing import Any

from sandbox_executor.entrypoint.sync_conflict import SYNC_CONFLICT_STATUS
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.target_sync_hooks import at_stage_boundary
from sandbox_executor.flow.target_sync_result import SyncBoundary


def sync_conflict_result(
    context: FlowContext, start_time: str, summary_row: dict[str, Any] | None, base_payload: dict[str, Any]
) -> StageResult | None:
    """Resolve a ``sync_conflict`` summary row via host-side ``sync_with_target`` at Stage 3 end, or fail closed.

    When ``summary_row`` has ``status == SYNC_CONFLICT_STATUS``, runs ``at_stage_boundary(context, FlowStage.EXECUTE,
    SyncBoundary.STAGE_END)`` on ``context.execution_branch``. If the host sync resolves the conflict (or is a no-op),
    promotes ``summary_row["status"]`` and ``base_payload["status"]`` to ``"success"`` and returns ``None`` so Stage 3
    proceeds through the empty-diff and touch-set checks. If the host sync is blocked, returns the failed
    :class:`StageResult` so Stage 3 never reports false success with unresolved conflicts.
    """
    if summary_row is None or summary_row.get("status") != SYNC_CONFLICT_STATUS:
        return None
    paths = [str(path) for path in summary_row.get("conflicted_paths") or []]
    exec_branch = str(base_payload.get("execution_branch") or context.execution_branch or "")
    if exec_branch and not context.execution_branch:
        context.execution_branch = exec_branch
    failure = at_stage_boundary(context, FlowStage.EXECUTE, SyncBoundary.STAGE_END)
    if failure is not None:
        failure.start_time = start_time
        failure.payload = {**base_payload, **failure.payload, "sync_conflict": True, "conflicted_paths": paths}
        context.stage_results["execute"] = failure
        return failure
    summary_row["status"] = "success"
    base_payload["status"] = "success"
    base_payload["sync_conflict_resolved"] = True
    base_payload["conflicted_paths"] = paths
    context.log(f"Stage 3 resolved sync conflict on '{exec_branch}' with target branch in {paths}.")
    return None
