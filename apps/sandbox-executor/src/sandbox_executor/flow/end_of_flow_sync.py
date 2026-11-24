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
from sandbox_executor.flow.git_refs import _track_local_branch
from sandbox_executor.flow.github.pr import check_pr_mergeability
from sandbox_executor.flow.review.sync_points import compute_rereview_scope, format_conflict_block_reason
from sandbox_executor.flow.review_restart import approval_stands, calibrate_with_review_restart
from sandbox_executor.flow.target_probe import TargetProbeError
from sandbox_executor.flow.target_sync import sync_with_target
from sandbox_executor.flow.target_sync_result import SyncBoundary, SyncRequest, SyncResult

MAX_END_OF_FLOW_REENTRIES = 3
MAX_UNKNOWN_MERGEABILITY_PROBES = 3


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

    def failure_result(self, prev: StageResult | None) -> StageResult:
        """Build the failed Stage 5 result recording end-of-flow sync diagnostics while preserving ``prev``."""
        diag = {
            "rounds": self.rounds,
            "mergeable": self.mergeable,
            "merge_state_status": self.merge_state_status,
            "blocked": True,
            "reason": self.reason,
        }
        payload = {**(prev.payload if prev else {}), "end_of_flow_sync": diag}
        return StageResult(FlowStage.CALIBRATE, StageStatus.FAILED, payload=payload, error=self.reason)


@dataclass(frozen=True)
class EndOfFlowSeams:
    """Injectable seams for hermetic unit testing of :func:`finish_flow`."""

    mergeability_probe: Callable[[str, str, str], dict[str, Any]] | None = None
    sync_runner: Callable[[SyncRequest], SyncResult] | None = None
    sleep: Callable[[float], None] = time.sleep


def _skip_reason(context: FlowContext) -> str | None:
    if context.dry_run:
        return "dry run"
    if context.skip_push:
        return "push skipped"
    return "no execution branch" if not context.execution_branch else None


def _verdict_fields(verdict: dict[str, Any]) -> tuple[str, str, bool]:
    mergeable = str(verdict.get("mergeable") or "UNKNOWN").upper()
    state = str(verdict.get("merge_state_status") or "UNKNOWN").upper()
    return mergeable, state, bool(verdict.get("is_dirty")) or mergeable == "CONFLICTING" or state == "DIRTY"


def _blocked_result(rounds: int, mergeable: str, state: str, reason: str) -> EndOfFlowSyncResult:
    return EndOfFlowSyncResult(False, rounds, mergeable, state, reason, blocked=True)


def _probe_mergeability(context: FlowContext, branch: str, target_branch: str, seams: EndOfFlowSeams) -> dict[str, Any]:
    """Probe PR mergeability, re-probing transient ``UNKNOWN`` states with bounded backoff."""
    probe_fn = seams.mergeability_probe or (lambda worktree, br, tgt: check_pr_mergeability(worktree, br, tgt))
    verdict: dict[str, Any] = {}
    for attempt in range(1, MAX_UNKNOWN_MERGEABILITY_PROBES + 1):
        verdict = probe_fn(context.target_worktree, branch, target_branch)
        mergeable, state, _ = _verdict_fields(verdict)
        if mergeable != "UNKNOWN" or state == "MISSING_REF":
            return verdict
        if attempt < MAX_UNKNOWN_MERGEABILITY_PROBES:
            context.log(
                f"End-of-flow sync: PR mergeability is UNKNOWN "
                f"(probe {attempt} of {MAX_UNKNOWN_MERGEABILITY_PROBES}); re-probing."
            )
            seams.sleep(2 * attempt)
    return verdict


def _non_conflicting_outcome(rounds: int, mergeable: str, state: str, is_dirty: bool) -> EndOfFlowSyncResult | None:
    """Return a terminal result when the PR is not conflicting; return ``None`` when a sync round is needed."""
    if mergeable == "UNKNOWN" or state == "MISSING_REF":
        reason = f"PR mergeability unresolved ({mergeable}/{state}) after {MAX_UNKNOWN_MERGEABILITY_PROBES} probes"
        return _blocked_result(rounds, mergeable, state, reason)
    if is_dirty:
        return None
    if mergeable == "MERGEABLE":
        return EndOfFlowSyncResult(clean=True, rounds=rounds, mergeable=mergeable, merge_state_status=state)
    return _blocked_result(rounds, mergeable, state, f"PR is not mergeable ({mergeable}/{state})")


def _run_flow_end_sync(
    context: FlowContext, branch: str, target_branch: str, round_num: int, seams: EndOfFlowSeams
) -> SyncResult:
    """Execute ``sync_with_target`` at ``SyncBoundary.FLOW_END`` for ``round_num``."""
    worktree = context.target_worktree
    prev, detached = "", False
    if seams.sync_runner is None:
        prev, detached, checkout_err = _hooks._checkout_active_branch(context, branch)
        if checkout_err is not None:
            return SyncResult("blocked", "", "", "", error=checkout_err)
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
            existing = getattr(context, "review_scope_paths", None) or []
            context.review_scope_paths = sorted(set(existing) | set(scope))
    return result


def _reenter_review_and_calibrate(
    context: FlowContext,
    run_stage: Callable[[FlowStage], StageResult],
    record: Callable[[FlowStage, StageResult], None],
) -> str | None:
    """Re-enter Stage 4 and (when enabled) Stage 5 on the merged head; return an error reason if not clean."""
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
    context: FlowContext,
    run_stage: Callable[[FlowStage], StageResult],
    record: Callable[[FlowStage, StageResult], None],
    *,
    seams: EndOfFlowSeams | None = None,
) -> EndOfFlowSyncResult:
    """Verify the PR is mergeable and clean at flow end, syncing and re-entering Stages 4-5 up to three times."""
    if skip := _skip_reason(context):
        return EndOfFlowSyncResult(clean=True, skipped=True, reason=skip)
    active_seams = seams or EndOfFlowSeams()
    branch, target_branch = str(context.execution_branch), base_branch_name(context)
    rounds = 0
    while True:
        verdict = _probe_mergeability(context, branch, target_branch, active_seams)
        mergeable, state, is_dirty = _verdict_fields(verdict)
        if (outcome := _non_conflicting_outcome(rounds, mergeable, state, is_dirty)) is not None:
            return outcome
        if rounds >= MAX_END_OF_FLOW_REENTRIES:
            reason = f"PR still conflicting ({mergeable}/{state}) after {MAX_END_OF_FLOW_REENTRIES} end-of-flow rounds"
            return _blocked_result(rounds, mergeable, state, reason)
        rounds += 1
        context.log(
            f"End-of-flow sync (round {rounds} of {MAX_END_OF_FLOW_REENTRIES}): "
            f"PR is {mergeable}/{state}; syncing '{branch}' with '{target_branch}'."
        )
        sync_result = _run_flow_end_sync(context, branch, target_branch, rounds, active_seams)
        if sync_result.status == "blocked":
            return _blocked_result(rounds, mergeable, state, format_conflict_block_reason(sync_result, target_branch))
        if sync_result.status == "merged" and (
            reentry_err := _reenter_review_and_calibrate(context, run_stage, record)
        ):
            return _blocked_result(rounds, mergeable, state, reentry_err)
