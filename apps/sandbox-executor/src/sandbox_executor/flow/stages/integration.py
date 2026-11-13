"""Stage 3 integration of a decomposed plan: bring the plan branch, which now holds every child's merge commit, into
the parent execution branch and record a superseding executions summary row listing the children.

Stage 4 then raises and reviews one combined PR from the parent execution branch into the intent's ``target_branch``
(halting for the human when that is ``main``), and Stage 5 calibrates it, exactly as for an executed plan. Both
actions are idempotent, so a resumed Stage 3 never merges twice or writes a second row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import TEST_MEASUREMENT_KEYS, _stage_failure
from sandbox_executor.flow.git_refs import (
    _append_superseding_execution_row,
    _latest_execution_summary,
    _read_ledger_rows,
    _track_local_branch,
)
from sandbox_executor.flow.github.merge import merge_into_branch

CHILD_ROW_KEYS = ("slug", "intent_branch", "execution_branch", "pr_number", "pr_url", "merge_commit")


def _superseding_row(summary_row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """The superseding ``decomposed`` summary row: the original row plus the plan branch and each merged child."""
    try:
        prior_revision = int(summary_row.get("ledger_revision") or 1)
    except (TypeError, ValueError):
        prior_revision = 1
    children = [{key: entry.get(key) for key in CHILD_ROW_KEYS} for entry in payload["children_progress"]]
    row = {k: v for k, v in summary_row.items() if k not in TEST_MEASUREMENT_KEYS}
    row.update(
        {
            "status": "decomposed",
            "plan_branch": payload["plan_branch"],
            "children": children,
            "integrated": True,
            "summary": (
                f"Decomposed into {len(children)} sub-intent(s), each merged into '{payload['plan_branch']}' and "
                "integrated into this execution branch; Stage 4 raises the combined PR."
            ),
            "created_at": datetime.now(UTC).isoformat(),
            "ledger_revision": max(2, prior_revision + 1),
        }
    )
    return row


def _record_integration_row(context: FlowContext, payload: dict[str, Any]) -> str | None:
    """Append the superseding summary row unless the execution's latest row already records the integration."""
    exec_branch, exec_id = str(payload["execution_branch"]), str(payload.get("execution_id") or "")
    rows = _read_ledger_rows(context.target_worktree, f"refs/remotes/origin/{exec_branch}", "executions.jsonl")
    latest = _latest_execution_summary(rows, exec_id) or {"entry_type": "summary", "execution_id": exec_id}
    if latest.get("integrated"):
        return None
    return _append_superseding_execution_row(context, exec_branch, _superseding_row(latest, payload))


def integrate_children(context: FlowContext, start_time: str, payload: dict[str, Any]) -> StageResult:
    """Merge the plan branch into the parent execution branch, record the children, and succeed Stage 3.

    The merge is a merge commit made in a temporary worktree and pushed (ledger-only conflicts interleaved, anything
    else fails the stage). The returned result carries ``decomposed`` and ``integrated``, and
    ``context.execution_branch`` is the parent execution branch, so Stage 4 raises the combined PR from it.
    """
    plan_branch, exec_branch = str(payload["plan_branch"]), str(payload.get("execution_branch") or "")
    if not exec_branch:
        error = "Execute stage failed: no parent execution branch to integrate."
        return _stage_failure(context, FlowStage.EXECUTE, start_time, error, **payload)
    slugs = ", ".join(str(e.get("slug")) for e in payload["children_progress"])
    message = f"integrate: merge {plan_branch} ({slugs}) into {exec_branch}"
    merged_sha, error = merge_into_branch(context.target_worktree, plan_branch, exec_branch, message)
    if merged_sha is None:
        reason = f"Execute stage failed: integrating '{plan_branch}' into '{exec_branch}' failed: {error}"
        return _stage_failure(context, FlowStage.EXECUTE, start_time, reason, **payload)
    row_error = _record_integration_row(context, payload)
    if row_error:
        reason = f"Execute stage failed: recording the integrated children on '{exec_branch}' failed: {row_error}"
        return _stage_failure(context, FlowStage.EXECUTE, start_time, reason, **payload)
    context.execution_branch = exec_branch
    _track_local_branch(context, exec_branch)
    payload["integrated"] = True
    context.log(f"Stage 3 completed: '{plan_branch}' integrated into '{exec_branch}'; Stage 4 raises the combined PR.")
    result = StageResult(
        stage=FlowStage.EXECUTE,
        status=StageStatus.SUCCESS,
        start_time=start_time,
        end_time=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        payload=payload,
    )
    context.stage_results[FlowStage.EXECUTE.value] = result
    return result
