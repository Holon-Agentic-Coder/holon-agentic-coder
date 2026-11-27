from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime

from sandbox_executor.entrypoint.execution_workspace import workspace_mounted
from sandbox_executor.flow.base_files import base_branch_name
from sandbox_executor.flow.conflict_resolver_role import conflict_code_resolver
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.git_refs import _track_local_branch
from sandbox_executor.flow.target_probe import TargetProbeError
from sandbox_executor.flow.target_sync import _preflight_sync, sync_with_target
from sandbox_executor.flow.target_sync_branch import (
    _best_branch_ref,
    _can_sync_worktree,
    _checkout_active_branch,
    _restore_checked_out_branch,
)
from sandbox_executor.flow.target_sync_result import SyncBoundary, SyncRequest, SyncResult

_PROBE_ATTR = "_last_target_probe"
_NOTICE_ATTR = "_pending_target_merge_notice"
_LEDGER_REL = "holon-knowledge/ledger/executions.jsonl"


class TurnBoundarySyncError(RuntimeError):
    def __init__(self, failure: StageResult) -> None:
        self.failure = failure
        super().__init__(failure.error or "turn-boundary target sync blocked")


def format_target_merge_notice(target_sha: str, changed_paths: tuple[str, ...] | list[str]) -> str:
    paths = [str(p) for p in changed_paths if p]
    bullets = "\n".join(f"- `{p}`" for p in paths) if paths else "- (no file changes)"
    header = f"## Target branch merged\n\nThe target branch was merged into this flow branch at commit `{target_sha}`."
    return f"{header}\nChanged paths:\n{bullets}"


def consume_target_merge_notice(context: FlowContext | None = None) -> str:
    notice = str(getattr(context, _NOTICE_ATTR, "") or "").strip() if context is not None else ""
    if context is not None:
        setattr(context, _NOTICE_ATTR, "")
    return notice


def active_flow_branch(context: FlowContext, stage: FlowStage) -> str:
    candidates: dict[FlowStage, tuple[str | None, ...]] = {
        FlowStage.INTENT: (context.intent_branch,),
        FlowStage.PLAN: (context.plan_branch, context.intent_branch),
        FlowStage.EXECUTE: (context.execution_branch, context.plan_branch, context.intent_branch),
        FlowStage.REVIEW: (context.execution_branch, context.plan_branch),
        FlowStage.CALIBRATE: (context.execution_branch,),
    }
    return next((branch for branch in candidates.get(stage, ()) if branch), "")


def _execution_id_of(branch: str, stage: FlowStage, boundary: SyncBoundary) -> str:
    segments = [part for part in branch.split("/") if part and part != "_"]
    for prefix in ("E-", "P-", "I-"):
        if found := next((part for part in reversed(segments) if part.startswith(prefix)), ""):
            return found
    return segments[-1] if segments else f"SYNC-{stage.value}-{boundary.value}"


def _blocked_stage_failure(
    context: FlowContext, stage: FlowStage, boundary: SyncBoundary, branch: str, result: SyncResult
) -> StageResult:
    paths = ", ".join(result.conflicted_paths or result.code_paths)
    where = f" in {paths}" if paths else ""
    base = result.error or "target sync blocked"
    detail = f"{base} ({result.test_result})" if result.test_result else base
    msg = (
        f"{stage.value.capitalize()} stage failed at {boundary.value}: target sync of '{branch}' with "
        f"'origin/{base_branch_name(context)}' was blocked{where}: {detail}"
    )
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _stage_failure(
        context,
        stage,
        ts,
        msg,
        sync_boundary=boundary.value,
        conflicted_paths=list(result.conflicted_paths or result.code_paths),
        sync_error=result.error,
    )


def _after_boundary_merge(context: FlowContext, stage: FlowStage, branch: str, result: SyncResult) -> None:
    _track_local_branch(context, branch)
    setattr(context, _NOTICE_ATTR, format_target_merge_notice(result.target_sha, result.changed_paths))
    if stage is FlowStage.REVIEW:
        from sandbox_executor.flow.review.sync_scope import compute_rereview_scope, record_rereview_scope

        record_rereview_scope(context, compute_rereview_scope(result))


def _run_boundary_sync(
    context: FlowContext, stage: FlowStage, boundary: SyncBoundary
) -> tuple[SyncResult, StageResult | None]:
    worktree, target_branch = context.target_worktree, base_branch_name(context)
    branch = active_flow_branch(context, stage)
    if context.dry_run or not branch or not _can_sync_worktree(worktree, target_branch):
        return SyncResult("unchanged", "", "", ""), None
    if (branch_ref := _best_branch_ref(worktree, branch)) is None:
        return SyncResult("unchanged", "", "", ""), None
    last = getattr(context, _PROBE_ATTR, None)
    try:
        probe, early = _preflight_sync(worktree, target_branch, boundary, last, head_ref=branch_ref)
    except TargetProbeError as exc:
        blocked = SyncResult("blocked", "", "", "", error=str(exc))
        return blocked, _blocked_stage_failure(context, stage, boundary, branch, blocked)
    setattr(context, _PROBE_ATTR, probe)
    if early is not None:
        return early, None
    prev, detached, checkout_err = _checkout_active_branch(context, branch, branch_ref)
    if checkout_err is not None:
        blocked = SyncResult("blocked", "", "", "", error=checkout_err)
        return blocked, _blocked_stage_failure(context, stage, boundary, branch, blocked)
    ledger, exec_id = os.path.join(worktree, _LEDGER_REL), _execution_id_of(branch, stage, boundary)
    req = SyncRequest(
        worktree, branch, target_branch, stage.value, boundary, ledger, exec_id, probe, conflict_code_resolver(context)
    )
    try:
        result = sync_with_target(req, probe=probe)
    finally:
        _restore_checked_out_branch(worktree, prev, detached, branch)
    if result.status == "merged":
        _after_boundary_merge(context, stage, branch, result)
    failure = _blocked_stage_failure(context, stage, boundary, branch, result) if result.status == "blocked" else None
    return result, failure


def at_flow_start(context: FlowContext, first_stage: FlowStage = FlowStage.INTENT) -> StageResult | None:
    return _run_boundary_sync(context, first_stage, SyncBoundary.FLOW_START)[1]


def at_stage_boundary(context: FlowContext, stage: FlowStage, boundary: SyncBoundary) -> StageResult | None:
    return _run_boundary_sync(context, stage, boundary)[1]


def at_turn_boundary(context: FlowContext, stage: FlowStage, turn_index: int = 0) -> StageResult | None:
    del turn_index
    return _run_boundary_sync(context, stage, SyncBoundary.TURN)[1]


def make_turn_boundary_hook(
    worktree: str, branch: str, target_branch: str, stage: FlowStage, *, context: FlowContext | None = None
) -> Callable[[int], str | None]:
    ctx = context or FlowContext(
        target_worktree=worktree,
        intent_data={"target_branch": target_branch},
        plan_branch=branch if stage == FlowStage.PLAN else None,
        execution_branch=branch if stage == FlowStage.EXECUTE else None,
        token_reduce=False,
    )

    def _on_turn(turn_index: int) -> str | None:
        # why: inside a mounted container the host owns the target sync, so the in-container hook does nothing
        if workspace_mounted():
            return None
        if failure := at_turn_boundary(ctx, stage, turn_index):
            raise TurnBoundarySyncError(failure)
        return consume_target_merge_notice(ctx) or None

    return _on_turn
