"""Stage 5 target-sync helpers: calibration entry and per-retry ``sync_with_target`` wiring."""

from __future__ import annotations

import os
from collections.abc import Callable

from sandbox_executor.flow import target_sync_hooks as _hooks
from sandbox_executor.flow.base_files import base_branch_name
from sandbox_executor.flow.calibrate_sync import CalibrationSyncError
from sandbox_executor.flow.conflict_resolver_role import conflict_code_resolver
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.git_refs import _resolve_branch_ref, _track_local_branch
from sandbox_executor.flow.review.approval_carry_over import record_stage5_carry_over
from sandbox_executor.flow.review.sync_points import (
    compute_rereview_scope,
    format_conflict_block_reason,
    is_mechanical_only,
)
from sandbox_executor.flow.target_probe import TargetProbeError
from sandbox_executor.flow.target_sync import sync_with_target
from sandbox_executor.flow.target_sync_result import SyncBoundary, SyncRequest, SyncResult


def _run_calibrate_sync(context: FlowContext) -> tuple[SyncResult, str]:
    """Run ``sync_with_target`` at ``SyncBoundary.CALIBRATE`` on ``context.execution_branch``."""
    target_branch = base_branch_name(context)
    noop = SyncResult("unchanged", "", "", "")
    worktree, branch = context.target_worktree, context.execution_branch or ""
    if context.dry_run or not branch or not _hooks._can_sync_worktree(worktree, target_branch):
        return noop, target_branch
    if _resolve_branch_ref(worktree, branch, prefer_remote=True) is None:
        return noop, target_branch
    prev, detached, checkout_err = _hooks._checkout_active_branch(context, branch)
    if checkout_err is not None:
        return SyncResult("blocked", "", "", "", error=checkout_err), target_branch
    request = SyncRequest(
        worktree=worktree,
        branch=branch,
        target_branch=target_branch,
        stage=FlowStage.CALIBRATE.value,
        boundary=SyncBoundary.CALIBRATE,
        ledger_path=os.path.join(worktree, _hooks._LEDGER_REL),
        execution_id=_hooks._execution_id_of(branch, FlowStage.CALIBRATE, SyncBoundary.CALIBRATE),
        last_probe=getattr(context, _hooks._PROBE_ATTR, None),
        code_resolver=conflict_code_resolver(context),
    )
    try:
        result = sync_with_target(request)
    except TargetProbeError as exc:
        return SyncResult("blocked", "", "", "", error=str(exc)), target_branch
    finally:
        _hooks._restore_checked_out_branch(worktree, prev, detached, branch)
    if result.probe is not None:
        setattr(context, _hooks._PROBE_ATTR, result.probe)
    if result.status == "merged":
        _track_local_branch(context, branch)
        notice = _hooks.format_target_merge_notice(result.target_sha, result.changed_paths)
        setattr(context, _hooks._NOTICE_ATTR, notice)
        mech = is_mechanical_only(result)
        scope = compute_rereview_scope(result)
        record_stage5_carry_over(context, target_branch, result.head_after, mechanical_only=mech, rereview_scope=scope)
    return result, target_branch


def sync_before_calibration(context: FlowContext, start_time: str) -> StageResult | None:
    """Sync ``context.execution_branch`` before calibration; return a failed ``StageResult`` if blocked."""
    result, target_branch = _run_calibrate_sync(context)
    if result.status != "blocked":
        return None
    reason = f"Calibrate stage failed: {format_conflict_block_reason(result, target_branch)}"
    return _stage_failure(
        context,
        FlowStage.CALIBRATE,
        start_time,
        reason,
        sync_boundary=SyncBoundary.CALIBRATE.value,
        conflicted_paths=list(result.conflicted_paths or result.code_paths),
        sync_error=result.error,
    )


def make_calibrate_retry_sync(context: FlowContext) -> Callable[[int], None]:
    """Build a ``before_retry(attempt)`` callback that syncs ``context.execution_branch`` before each retry."""

    def _before_retry(attempt: int) -> None:
        result, target_branch = _run_calibrate_sync(context)
        if result.status == "blocked":
            detail = format_conflict_block_reason(result, target_branch)
            raise CalibrationSyncError(f"target sync before calibration retry {attempt} blocked: {detail}")

    return _before_retry
