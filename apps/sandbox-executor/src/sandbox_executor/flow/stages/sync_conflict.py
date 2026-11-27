from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sandbox_executor.entrypoint.sync_conflict import SYNC_CONFLICT_STATUS
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.git_refs import _append_superseding_execution_row, _origin_has_branch
from sandbox_executor.flow.target_sync_hooks import at_stage_boundary
from sandbox_executor.flow.target_sync_result import SyncBoundary


def _superseding_success_row(summary_row: dict[str, Any], exec_id: str, paths: list[str]) -> dict[str, Any]:
    try:
        prior_revision = int(summary_row.get("ledger_revision") or 1)
    except (TypeError, ValueError):
        prior_revision = 1
    row = {key: value for key, value in summary_row.items() if key != "conflicted_paths"}
    if exec_id and not row.get("execution_id"):
        row["execution_id"] = exec_id
    row.update(
        {
            "entry_type": "summary",
            "status": "success",
            "summary": f"Pre-push sync conflict in {paths} resolved with target branch on host.",
            "created_at": datetime.now(UTC).isoformat(),
            "ledger_revision": max(2, prior_revision + 1),
        }
    )
    return row


def sync_conflict_result(
    context: FlowContext, start_time: str, summary_row: dict[str, Any] | None, base_payload: dict[str, Any]
) -> StageResult | None:
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
    exec_id = str(summary_row.get("execution_id") or base_payload.get("execution_id") or "")
    superseding = _superseding_success_row(summary_row, exec_id, paths)
    if not context.dry_run and exec_branch and _origin_has_branch(context.target_worktree, exec_branch):
        write_error = _append_superseding_execution_row(context, exec_branch, superseding)
        if write_error is not None:
            return _stage_failure(
                context,
                FlowStage.EXECUTE,
                start_time,
                f"Execute stage failed: resolved sync conflict on '{exec_branch}' but could not record the "
                f"superseding summary row: {write_error}",
                **base_payload,
                sync_conflict=True,
                conflicted_paths=paths,
            )
    summary_row.clear()
    summary_row.update(superseding)
    base_payload["status"] = "success"
    base_payload["sync_conflict_resolved"] = True
    base_payload["conflicted_paths"] = paths
    context.log(f"Stage 3 resolved sync conflict on '{exec_branch}' with target branch in {paths}.")
    return None
