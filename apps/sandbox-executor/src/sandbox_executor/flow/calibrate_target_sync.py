from __future__ import annotations

import dataclasses
import os
from collections.abc import Callable

from sandbox_executor.flow import target_sync_hooks as _hooks
from sandbox_executor.flow.base_files import base_branch_name
from sandbox_executor.flow.calibrate_sync import CalibrationSyncError, prepare_calibrated_branch
from sandbox_executor.flow.conflict_resolver_role import conflict_code_resolver
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.git_refs import _resolve_branch_ref, _track_local_branch
from sandbox_executor.flow.review.approval_carry_over import record_stage5_carry_over
from sandbox_executor.flow.review.ledger_commit import _commit_pending_ledger_rows
from sandbox_executor.flow.review.main_baseline_guard import MAIN_BASELINE_FAILING, guard_target_sync
from sandbox_executor.flow.review.sync_points import (
    compute_rereview_scope,
    format_conflict_block_reason,
    is_mechanical_only,
)
from sandbox_executor.flow.target_probe import TargetProbeError
from sandbox_executor.flow.target_sync import sync_with_target
from sandbox_executor.flow.target_sync_result import SyncBoundary, SyncRequest, SyncResult
from sandbox_executor.git import run_git
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.ref_names import calibration_branch


class _CalibrateRestartRequired(Exception):
    def __init__(self, result: StageResult) -> None:
        self.result = result
        super().__init__(result.error or "Stage 4 review restart required after agent-resolved target sync")


def _leave_calibrated_branch(context: FlowContext) -> None:
    branch = context.execution_branch
    if not branch or context.dry_run:
        return
    repo_dir = context.target_worktree
    calibrated = calibration_branch(branch)
    head = run_git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=repo_dir, check=False)
    if head.stdout.strip() == calibrated:
        res = run_git(["checkout", branch], cwd=repo_dir, check=False)
        if res.returncode != 0:
            context.log(f"Warning: could not leave '{calibrated}': {redact_agent_secrets(res.stderr.strip())}")


def _retry_safe(context: FlowContext, report_rel: str, expected_tip: list[str]) -> Callable[[], bool]:
    repo_dir = context.target_worktree
    calibrated_ref = f"refs/heads/{calibration_branch(str(context.execution_branch))}"

    def calibrated_tip() -> str:
        res = run_git(["rev-parse", "--verify", "--quiet", calibrated_ref], cwd=repo_dir, check=False)
        return res.stdout.strip() if res.returncode == 0 else ""

    expected_tip[0] = "" if context.dry_run else calibrated_tip()

    def safe() -> bool:
        if context.dry_run:
            return True
        status = run_git(["status", "--porcelain", "--", report_rel], cwd=repo_dir, check=False)
        return status.returncode == 0 and not status.stdout.strip() and calibrated_tip() == expected_tip[0]

    return safe


def _finalize_calibrate_merge(context: FlowContext, branch: str, target_branch: str, result: SyncResult) -> SyncResult:
    _track_local_branch(context, branch)
    notice = _hooks.format_target_merge_notice(result.target_sha, result.changed_paths)
    setattr(context, _hooks._NOTICE_ATTR, notice)
    mech = is_mechanical_only(result)
    scope = compute_rereview_scope(result)
    carry = record_stage5_carry_over(
        context, target_branch, result.head_after, mechanical_only=mech, rereview_scope=scope
    )
    if carry is not None:
        commit = _commit_pending_ledger_rows(context, context.review_iterations_done, "stage 5 carry-over")
        if commit is not None:
            return dataclasses.replace(result, head_after=commit.sha)
    return result


def _run_calibrate_sync(context: FlowContext) -> tuple[SyncResult, str]:
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
    try:
        if baseline := guard_target_sync(context, target_branch, branch):
            err = f"{MAIN_BASELINE_FAILING}: {baseline.reason}"
            return SyncResult("blocked", "", "", "", error=err), target_branch
        last_probe = getattr(context, _hooks._PROBE_ATTR, None)
        if _commit_pending_ledger_rows(context, context.review_iterations_done, "before calibration target sync"):
            last_probe = None
        request = SyncRequest(
            worktree=worktree,
            branch=branch,
            target_branch=target_branch,
            stage=FlowStage.CALIBRATE.value,
            boundary=SyncBoundary.CALIBRATE,
            ledger_path=os.path.join(worktree, _hooks._LEDGER_REL),
            execution_id=_hooks._execution_id_of(branch, FlowStage.CALIBRATE, SyncBoundary.CALIBRATE),
            last_probe=last_probe,
            code_resolver=conflict_code_resolver(context),
        )
        result = sync_with_target(request)
        if result.status == "merged":
            result = _finalize_calibrate_merge(context, branch, target_branch, result)
    except TargetProbeError as exc:
        return SyncResult("blocked", "", "", "", error=str(exc)), target_branch
    finally:
        _hooks._restore_checked_out_branch(worktree, prev, detached, branch)
    if result.probe is not None:
        setattr(context, _hooks._PROBE_ATTR, result.probe)
    return result, target_branch


def sync_before_calibration(context: FlowContext, start_time: str) -> StageResult | None:
    result, target_branch = _run_calibrate_sync(context)
    if result.status == "blocked":
        if str(result.error or "").startswith(MAIN_BASELINE_FAILING):
            return _stage_failure(
                context,
                FlowStage.CALIBRATE,
                start_time,
                f"Calibrate stage blocked: {result.error}",
                blocked_on=MAIN_BASELINE_FAILING,
                sync_boundary=SyncBoundary.CALIBRATE.value,
                sync_error=result.error,
            )
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
    if result.status == "merged":
        from sandbox_executor.flow.review_restart import stage5_merged_head_gate

        stopped, _ = stage5_merged_head_gate(context)
        if stopped is not None:
            return stopped
    return None


def make_calibrate_retry_sync(
    context: FlowContext, on_head_updated: Callable[[str], None] | None = None
) -> Callable[[int], None]:

    def _before_retry(attempt: int) -> None:
        _leave_calibrated_branch(context)
        result, target_branch = _run_calibrate_sync(context)
        if result.status == "blocked":
            if str(result.error or "").startswith(MAIN_BASELINE_FAILING):
                raise CalibrationSyncError(f"target sync before calibration retry {attempt} blocked: {result.error}")
            detail = format_conflict_block_reason(result, target_branch)
            raise CalibrationSyncError(f"target sync before calibration retry {attempt} blocked: {detail}")
        if result.status != "merged":
            return
        from sandbox_executor.flow.review_restart import stage5_merged_head_gate

        stopped, _ = stage5_merged_head_gate(context)
        if stopped is not None:
            raise _CalibrateRestartRequired(stopped)
        if result.head_after:
            prepare_calibrated_branch(context, result.head_after)
            if on_head_updated is not None:
                on_head_updated(result.head_after)

    return _before_retry
