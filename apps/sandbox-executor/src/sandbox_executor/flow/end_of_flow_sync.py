"""Bounded end-of-flow target sync: verify PR mergeability, sync conflicts, and re-enter Stages 4 and 5."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow import target_sync_hooks as _hooks
from sandbox_executor.flow.base_files import base_branch_name
from sandbox_executor.flow.conflict_resolver_role import conflict_code_resolver
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.git_refs import _resolve_branch_ref, _track_local_branch
from sandbox_executor.flow.github.merge import PROTECTED_BRANCHES, _bare_branch
from sandbox_executor.flow.github.pr import check_pr_mergeability
from sandbox_executor.flow.review.proxy_env import review_proxy_env
from sandbox_executor.flow.review.sync_points import compute_rereview_scope, format_conflict_block_reason
from sandbox_executor.flow.review_restart import approval_stands, calibrate_with_review_restart
from sandbox_executor.flow.target_probe import TargetProbeError
from sandbox_executor.flow.target_sync import sync_with_target
from sandbox_executor.flow.target_sync_result import SyncBoundary, SyncRequest, SyncResult
from sandbox_executor.review_accounting import restart_review_passes

MAX_END_OF_FLOW_REENTRIES = 3
MAX_UNKNOWN_MERGEABILITY_PROBES = 3
_RunStage = Callable[[FlowStage], StageResult]
_RecordStage = Callable[[FlowStage, StageResult], None]


@dataclass(frozen=True)
class EndOfFlowSyncResult:
    """Outcome of the bounded end-of-flow mergeability and sync verification loop."""

    clean: bool
    rounds: int = 0
    mergeable: str = ""
    merge_state_status: str = ""
    reason: str = ""
    blocked: bool = False
    skipped: bool = False

    def failure_result(self, context: FlowContext) -> StageResult:
        """Build the failed Stage 5 result with end-of-flow sync diagnostics while preserving existing payload."""
        prev = context.stage_results.get(FlowStage.CALIBRATE.value)
        diag = {"rounds": self.rounds, "mergeable": self.mergeable, "merge_state_status": self.merge_state_status}
        payload = {
            **(prev.payload if prev else {}),
            "end_of_flow_sync": {**diag, "blocked": True, "reason": self.reason},
        }
        return StageResult(FlowStage.CALIBRATE, StageStatus.FAILED, payload=payload, error=self.reason)


@dataclass(frozen=True)
class EndOfFlowSeams:
    """Injectable seams for hermetic unit testing of :func:`finish_flow`."""

    mergeability_probe: Callable[[str, str, str], dict[str, Any]] | None = None
    sync_runner: Callable[[SyncRequest], SyncResult] | None = None
    sleep: Callable[[float], None] = time.sleep


def _verdict_fields(verdict: dict[str, Any]) -> tuple[str, str, bool]:
    mergeable = str(verdict.get("mergeable") or "UNKNOWN").upper()
    state = str(verdict.get("merge_state_status") or "UNKNOWN").upper()
    return mergeable, state, bool(verdict.get("is_dirty")) or mergeable == "CONFLICTING" or state == "DIRTY"


def _blocked_result(rounds: int, mergeable: str, state: str, reason: str) -> EndOfFlowSyncResult:
    return EndOfFlowSyncResult(False, rounds, mergeable, state, reason, blocked=True)


def _is_not_a_repo(verdict: dict[str, Any]) -> bool:
    diag = verdict.get("diagnostics")
    return isinstance(diag, dict) and str(diag.get("error") or "").startswith("not a git repository:")


def _probe_mergeability(context: FlowContext, branch: str, target_branch: str, seams: EndOfFlowSeams) -> dict[str, Any]:
    """Probe PR mergeability, re-probing transient ``UNKNOWN`` states with bounded backoff."""
    probe_fn = seams.mergeability_probe or (
        lambda worktree, br, tgt: check_pr_mergeability(worktree, br, tgt, proxy_env=review_proxy_env(context))
    )
    verdict: dict[str, Any] = {}
    for attempt in range(1, MAX_UNKNOWN_MERGEABILITY_PROBES + 1):
        verdict = probe_fn(context.target_worktree, branch, target_branch)
        mergeable, state, _ = _verdict_fields(verdict)
        if mergeable != "UNKNOWN" or state == "MISSING_REF" or _is_not_a_repo(verdict):
            return verdict
        if attempt < MAX_UNKNOWN_MERGEABILITY_PROBES:
            context.log(f"End-of-flow sync: mergeability UNKNOWN ({attempt}/{MAX_UNKNOWN_MERGEABILITY_PROBES}).")
            seams.sleep(2 * attempt)
    return verdict


def _non_conflicting_outcome(rounds: int, verdict: dict[str, Any]) -> EndOfFlowSyncResult | None:
    """Return a terminal result when the PR is not conflicting; return ``None`` when a sync round is needed."""
    if _is_not_a_repo(verdict):
        return EndOfFlowSyncResult(clean=True, rounds=rounds, skipped=True, reason="not a git repository")
    mergeable, state, is_dirty = _verdict_fields(verdict)
    if mergeable == "UNKNOWN" or state == "MISSING_REF":
        suffix = "" if state == "MISSING_REF" else f" after {MAX_UNKNOWN_MERGEABILITY_PROBES} probes"
        return _blocked_result(rounds, mergeable, state, f"PR mergeability unresolved ({mergeable}/{state}){suffix}")
    if is_dirty:
        return None
    if mergeable == "MERGEABLE":
        return EndOfFlowSyncResult(clean=True, rounds=rounds, mergeable=mergeable, merge_state_status=state)
    return _blocked_result(rounds, mergeable, state, f"PR is not mergeable ({mergeable}/{state})")


def _precheck_sync(worktree: str, branch: str, target_branch: str) -> str | None:
    if _bare_branch(branch) in PROTECTED_BRANCHES:
        return f"refusing to push to protected branch '{branch}'"
    if not _hooks._can_sync_worktree(worktree, target_branch):
        return f"fetching '{target_branch}' and '{branch}' failed"
    if _resolve_branch_ref(worktree, branch, prefer_remote=True) is None:
        return f"branch '{branch}' could not be resolved"
    return None


def _run_flow_end_sync(
    context: FlowContext, branch: str, target_branch: str, round_num: int, seams: EndOfFlowSeams
) -> SyncResult:
    """Execute ``sync_with_target`` at ``SyncBoundary.FLOW_END`` for ``round_num``."""
    worktree, prev, detached = context.target_worktree, "", False
    if seams.sync_runner is None:
        err = _precheck_sync(worktree, branch, target_branch)
        if err is None:
            prev, detached, err = _hooks._checkout_active_branch(context, branch)
        if err is not None:
            return SyncResult("blocked", "", "", "", error=err)
    request = SyncRequest(
        worktree=worktree,
        branch=branch,
        target_branch=target_branch,
        stage=FlowStage.CALIBRATE.value,
        boundary=SyncBoundary.FLOW_END,
        ledger_path=os.path.join(worktree, _hooks._LEDGER_REL),
        execution_id=_hooks._execution_id_of(branch, FlowStage.CALIBRATE, SyncBoundary.FLOW_END),
        last_probe=getattr(context, _hooks._PROBE_ATTR, None),
        code_resolver=conflict_code_resolver(context),
        reentry_round=round_num,
    )
    runner = seams.sync_runner or sync_with_target
    try:
        result = runner(request)
    except TargetProbeError as exc:
        return SyncResult("blocked", "", "", "", error=str(exc))
    finally:
        if seams.sync_runner is None:
            _hooks._restore_checked_out_branch(worktree, prev, detached, branch)
    if result.probe is not None:
        setattr(context, _hooks._PROBE_ATTR, result.probe)
    if result.status == "merged":
        _track_local_branch(context, branch)
        if scope := compute_rereview_scope(result):
            context.review_scope_paths = sorted(set(getattr(context, "review_scope_paths", None) or []) | set(scope))
    return result


def _reenter_review_and_calibrate(context: FlowContext, run_stage: _RunStage, record: _RecordStage) -> str | None:
    """Re-enter Stage 4 and (when enabled) Stage 5 on the merged head; return an error reason if not clean."""
    restart_review_passes(context)
    review = run_stage(FlowStage.REVIEW)
    record(FlowStage.REVIEW, review)
    if review.status != StageStatus.APPROVED:
        return review.error or f"re-entered Stage 4 ended with status '{review.status.value}'"
    if not context.skip_calibrate and context.auto_calibrate:
        calibrate = calibrate_with_review_restart(context, run_stage, record)
        if calibrate.status == StageStatus.FAILED:
            return calibrate.error or "re-entered Stage 5 calibration failed"
        if not approval_stands(context, review):
            return "Stage 4 approval was withdrawn during re-entered Stage 5 calibration"
    return None


def finish_flow(
    context: FlowContext, run_stage: _RunStage, record: _RecordStage, *, seams: EndOfFlowSeams | None = None
) -> EndOfFlowSyncResult:
    """Verify the PR is mergeable and clean at flow end, syncing and re-entering Stages 4-5 up to three times."""
    if context.dry_run or context.skip_push or not context.execution_branch:
        why = "dry run" if context.dry_run else "push skipped" if context.skip_push else "no execution branch"
        return EndOfFlowSyncResult(clean=True, skipped=True, reason=why)
    active_seams = seams or EndOfFlowSeams()
    branch, target_branch, rounds = str(context.execution_branch), base_branch_name(context), 0
    while True:
        verdict = _probe_mergeability(context, branch, target_branch, active_seams)
        if (outcome := _non_conflicting_outcome(rounds, verdict)) is not None:
            return outcome
        mergeable, state, _ = _verdict_fields(verdict)
        if rounds >= MAX_END_OF_FLOW_REENTRIES:
            reason = f"PR still conflicting ({mergeable}/{state}) after {MAX_END_OF_FLOW_REENTRIES} end-of-flow rounds"
            return _blocked_result(rounds, mergeable, state, reason)
        rounds += 1
        context.log(f"End-of-flow sync round {rounds}/{MAX_END_OF_FLOW_REENTRIES}: {mergeable}/{state} on '{branch}'.")
        sync_result = _run_flow_end_sync(context, branch, target_branch, rounds, active_seams)
        if sync_result.status == "blocked":
            return _blocked_result(rounds, mergeable, state, format_conflict_block_reason(sync_result, target_branch))
        if sync_result.status == "merged" and (err := _reenter_review_and_calibrate(context, run_stage, record)):
            return _blocked_result(rounds, mergeable, state, err)
