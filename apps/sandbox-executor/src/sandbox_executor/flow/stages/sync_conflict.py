"""Stage 3 outcome for an execution branch that kept its commits but could not merge the target branch."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sandbox_executor.entrypoint.sync_conflict import SYNC_CONFLICT_STATUS
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus


def sync_conflict_result(
    context: FlowContext, start_time: str, summary_row: dict[str, Any] | None, base_payload: dict[str, Any]
) -> StageResult | None:
    """Return the resumable Stage 3 result when ``summary_row`` is a ``sync_conflict`` row, else None.

    The result is a success so the flow continues from review with ``execution_branch`` in the context and the
    checkpoint; the payload names the conflicted paths for a resolver and the pull request text.
    """
    if summary_row is None or summary_row.get("status") != SYNC_CONFLICT_STATUS:
        return None
    paths = [str(path) for path in summary_row.get("conflicted_paths") or []]
    context.log(
        f"Stage 3 completed with a sync conflict: '{base_payload['execution_branch']}' keeps the executor's commits "
        f"but conflicts with the target branch in {paths}; a resolver or the maintainer must resolve it."
    )
    result = StageResult(
        stage=FlowStage.EXECUTE,
        status=StageStatus.SUCCESS,
        start_time=start_time,
        end_time=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        payload={**base_payload, "sync_conflict": True, "conflicted_paths": paths},
    )
    context.stage_results["execute"] = result
    return result
