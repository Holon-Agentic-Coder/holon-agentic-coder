from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.github.ci_final_head import _bookkeeping_only, _git_changed_paths
from sandbox_executor.flow.github.ci_head_checks import FAILED, PASSED
from sandbox_executor.flow.github.ci_reverify import _same_commit, approved_head_of
from sandbox_executor.flow.github.pr_head import pr_head_sha
from sandbox_executor.flow.review.approval_carry_over import merged_head_coverage
from sandbox_executor.flow.review.approval_coverage import CHANGED
from sandbox_executor.flow.review.consensus_withdrawal import withdraw_consensus, withdrawn_consensus_of
from sandbox_executor.flow.review.merged_head_gate import (
    MergedHeadSeams,
    format_merged_head_ci,
    gate_merged_head_ci,
    gate_record,
    skipped_record,
    stage4_ci_record,
)
from sandbox_executor.flow.review.merged_head_resume import (
    pending_scoped_restart,
    resumed_restart,
    withdraw_after_rejected_restart,
)
from sandbox_executor.flow.review.merged_head_routing import refuse_calibration, route_merged_head_failure
from sandbox_executor.flow.stage5 import stage5_prerequisite_error
from sandbox_executor.git import run_git

MAX_MERGED_HEAD_REVIEW_RESTARTS = 3


def _execution_head(context: FlowContext, seams: MergedHeadSeams) -> str | None:
    if seams.head_reader is not None:
        return seams.head_reader()
    branch, repo_dir = str(context.execution_branch), context.target_worktree
    proc = run_git(["rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}"], cwd=repo_dir, check=False)
    sha = str(proc.stdout or "").strip() if proc.returncode == 0 else ""
    return sha or pr_head_sha(repo_dir, branch, seams.proxy_env)


def _git_bookkeeping_move(repo_dir: str) -> Callable[[str, str], bool]:

    def moved(approved: str, head: str) -> bool:
        merges = run_git(["rev-list", "--merges", f"{approved}..{head}"], cwd=repo_dir, check=False)
        if merges.returncode != 0 or str(merges.stdout or "").strip():
            return False
        return _bookkeeping_only(_git_changed_paths(repo_dir, {})(approved, head))

    return moved


def _skip_reason(context: FlowContext) -> str | None:
    if context.dry_run or context.skip_push or not context.execution_branch:
        return "dry run" if context.dry_run else "push skipped" if context.skip_push else "no execution branch"
    return None


def _refuse_prerequisites(context: FlowContext, start: str, reason: str) -> tuple[StageResult, dict[str, Any]]:
    record = gate_record("refused", "", [], [], reason)
    failure = _stage_failure(
        context,
        FlowStage.CALIBRATE,
        start,
        f"Calibrate stage refused: {reason}; nothing was calibrated or pushed.",
        merged_head_ci=record,
    )
    return failure, record


def _decide(
    context: FlowContext, start: str, record: dict[str, Any], seams: MergedHeadSeams
) -> tuple[StageResult | None, dict[str, Any]]:
    if record["state"] == PASSED:
        return None, record
    if record["state"] == FAILED:
        return route_merged_head_failure(context, start, record, seams, withdraw_consensus)
    return refuse_calibration(context, start, record), record


def _evaluate_merged_head(
    context: FlowContext, start: str, approved: str, head: str, seams: MergedHeadSeams
) -> tuple[StageResult | None, dict[str, Any]]:
    moved = seams.bookkeeping_move or _git_bookkeeping_move(context.target_worktree)
    if _same_commit(approved, head) or moved(approved, head):
        record = stage4_ci_record(context, head)
        context.log(
            f"Stage 5 relies on Stage 4 CI for head {head} (approved {approved}). {format_merged_head_ci(record)}"
        )
        return None, record
    coverage, annotation = merged_head_coverage(context, approved, head)
    # why: a PR-own change that differs from the approved one sends Stage 4 to review it again.
    if coverage == CHANGED:
        return _decide(context, start, annotation, seams)
    context.log(f"Merged-head CI gate: head {head} is not the approved head {approved}; waiting for its CI.")
    record = {**gate_merged_head_ci(context, context.target_worktree, head, seams), **annotation}
    context.log(format_merged_head_ci(record))
    return _decide(context, start, record, seams)


def stage5_merged_head_gate(
    context: FlowContext, seams: MergedHeadSeams | None = None
) -> tuple[StageResult | None, dict[str, Any]]:
    seams, start = seams or MergedHeadSeams(), datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    resumed = resumed_restart(context, start)
    # why: a resume after a withdrawal or a scoped restart runs Stage 4 again first.
    if resumed is not None:
        return resumed
    prereq_error = stage5_prerequisite_error(context)
    if prereq_error is not None:
        return _refuse_prerequisites(context, start, prereq_error)
    skip = _skip_reason(context)
    if skip:
        context.log(f"Merged-head CI gate skipped: {skip}.")
        return None, skipped_record(None, skip)
    approved, head = str(approved_head_of(context)), _execution_head(context, seams)
    if not head:
        record = gate_record("unknown", "", [], [], "the execution head could not be read")
        return _decide(context, start, record, seams)
    return _evaluate_merged_head(context, start, approved, head, seams)


def approval_stands(context: FlowContext, result: StageResult) -> bool:
    latest = context.stage_results.get(FlowStage.REVIEW.value) or result
    approved = result.status == StageStatus.APPROVED and latest.status == StageStatus.APPROVED
    return approved and withdrawn_consensus_of(context) is None and pending_scoped_restart(context) is None


def is_review_restart(result: StageResult) -> bool:
    return result.stage == FlowStage.CALIBRATE and bool(result.payload.get("review_restart"))


def _run_calibrate(
    context: FlowContext,
    run_stage: Callable[[FlowStage], StageResult],
    record: Callable[[FlowStage, StageResult], None],
    restarts: int,
) -> StageResult:
    context.merged_head_restart_allowed = restarts < MAX_MERGED_HEAD_REVIEW_RESTARTS
    result = run_stage(FlowStage.CALIBRATE)
    if is_review_restart(result) and restarts >= MAX_MERGED_HEAD_REVIEW_RESTARTS:
        head = (result.payload.get("merged_head_ci") or {}).get("head_sha")
        error = f"merged head CI failed after {restarts} review restarts (last head {head}); nothing calibrated."
        context.log(error)
        payload = {**result.payload, "review_restart": False, "review_restarts": restarts}
        result = StageResult(
            FlowStage.CALIBRATE, StageStatus.FAILED, result.start_time, result.end_time, payload, error
        )
    record(FlowStage.CALIBRATE, result)
    return result


def _restart_note(result: StageResult) -> str:
    gate = result.payload.get("merged_head_ci") or {}
    if result.payload.get("scoped_restart"):
        cls = (result.payload.get("ci_routing") or {}).get("classification")
        return (
            f"CI defect ({cls}) on merged head {gate.get('head_sha')}: restarting Stage 4 with the resolver on the CI "
            "finding; the diff approval stands"
        )
    why = gate.get("withdrawal_reason") or f"CI failed on merged head {gate.get('head_sha')}"
    return f"Consensus withdrawn: {why}; restarting Stage 4 at iteration 1"


def calibrate_with_review_restart(
    context: FlowContext,
    run_stage: Callable[[FlowStage], StageResult],
    record: Callable[[FlowStage, StageResult], None],
) -> StageResult:
    restarts = 0
    result = _run_calibrate(context, run_stage, record, restarts)
    while is_review_restart(result):
        restarts += 1
        gate, scoped = result.payload.get("merged_head_ci") or {}, bool(result.payload.get("scoped_restart"))
        # why: read before the restarted Stage 4 replaces the consensus.
        approved = result.payload.get("approved_head")
        context.log(f"{_restart_note(result)} (restart {restarts} of {MAX_MERGED_HEAD_REVIEW_RESTARTS}).")
        review = run_stage(FlowStage.REVIEW)
        record(FlowStage.REVIEW, review)
        if review.status != StageStatus.APPROVED:
            if scoped and gate.get("head_sha"):
                withdraw_after_rejected_restart(context, gate, approved)
            return review
        result = _run_calibrate(context, run_stage, record, restarts)
    return result
