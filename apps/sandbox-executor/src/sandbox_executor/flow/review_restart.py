"""The merged-head restart: Stage 5 refuses a head CI failed on, and the pipeline restarts Stage 4 for that head.

:func:`stage5_merged_head_gate` runs after the conflict gate and before any calibration commit. A head that is the
approved head, or moved past it only by non-merge bookkeeping commits (the ledger rows Stage 4 commits on exit), keeps
today's path: Stage 5 relies on the Stage 4 CI of the approved code. Any other head (a merge of the target branch, or a
code change) waits for CI on exactly that SHA (:mod:`sandbox_executor.flow.review.merged_head_gate`), also when the
approval carried over by PR-own fingerprint; a PR-own change that differs from the approved one withdraws the consensus
without waiting (:mod:`sandbox_executor.flow.review.approval_carry_over`). A failed check is routed by class
(:mod:`sandbox_executor.flow.review.merged_head_routing`): noise is rerun once, a defect restarts Stage 4 scoped with
the CI finding carried while the diff approval stands, and a changed diff withdraws the consensus; each restart returns
a SKIPPED Stage 5 flagged ``review_restart``. Anything unsettled fails Stage 5 closed.
:func:`calibrate_with_review_restart` turns that flag into a fresh Stage 4 at iteration 1 and, on a new approval, a new
Stage 5, at most ``MAX_MERGED_HEAD_REVIEW_RESTARTS`` times; a scoped restart whose Stage 4 does not approve withdraws.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
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
from sandbox_executor.git import run_git

MAX_MERGED_HEAD_REVIEW_RESTARTS = 3


def _execution_head(context: FlowContext, seams: MergedHeadSeams) -> str | None:
    """The execution head on origin after the conflict gate's push, else the PR head GitHub reports."""
    if seams.head_reader is not None:
        return seams.head_reader()
    branch, repo_dir = str(context.execution_branch), context.target_worktree
    proc = run_git(["rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}"], cwd=repo_dir, check=False)
    sha = str(proc.stdout or "").strip() if proc.returncode == 0 else ""
    return sha or pr_head_sha(repo_dir, branch, seams.proxy_env)


def _git_bookkeeping_move(repo_dir: str) -> Callable[[str, str], bool]:
    """Whether ``approved..head`` holds no merge commit and touches only plan, execution and knowledge-base paths."""

    def moved(approved: str, head: str) -> bool:
        merges = run_git(["rev-list", "--merges", f"{approved}..{head}"], cwd=repo_dir, check=False)
        if merges.returncode != 0 or str(merges.stdout or "").strip():
            return False
        return _bookkeeping_only(_git_changed_paths(repo_dir, {})(approved, head))

    return moved


def _skip_reason(context: FlowContext) -> str | None:
    if context.dry_run or context.skip_push or not context.execution_branch:
        return "dry run" if context.dry_run else "push skipped" if context.skip_push else "no execution branch"
    return None if approved_head_of(context) else "no Stage 4 approval recorded"


def _decide(
    context: FlowContext, start: str, record: dict[str, Any], seams: MergedHeadSeams
) -> tuple[StageResult | None, dict[str, Any]]:
    """None lets Stage 5 calibrate; a failed check is routed by class; anything unsettled fails closed."""
    if record["state"] == PASSED:
        return None, record
    if record["state"] == FAILED:
        return route_merged_head_failure(context, start, record, seams, withdraw_consensus)
    return refuse_calibration(context, start, record), record


def stage5_merged_head_gate(
    context: FlowContext, seams: MergedHeadSeams | None = None
) -> tuple[StageResult | None, dict[str, Any]]:
    """Gate Stage 5 on CI of the head it would calibrate: (the result that stops Stage 5 or None, the CI record)."""
    seams, start = seams or MergedHeadSeams(), datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    resumed = resumed_restart(context, start)
    if resumed is not None:  # A resume after a withdrawal or a scoped restart: Stage 4 runs again first.
        return resumed
    skip = _skip_reason(context)
    if skip:
        context.log(f"Merged-head CI gate skipped: {skip}.")
        return None, skipped_record(None, skip)
    approved, head = str(approved_head_of(context)), _execution_head(context, seams)
    if not head:
        record = gate_record("unknown", "", [], [], "the execution head could not be read")
        return _decide(context, start, record, seams)
    moved = seams.bookkeeping_move or _git_bookkeeping_move(context.target_worktree)
    if _same_commit(approved, head) or moved(approved, head):
        record = stage4_ci_record(context, head)
        context.log(
            f"Stage 5 relies on Stage 4 CI for head {head} (approved {approved}). {format_merged_head_ci(record)}"
        )
        return None, record
    coverage, annotation = merged_head_coverage(context, approved, head)
    if coverage == CHANGED:  # The PR-own change differs from the approved one: Stage 4 reviews it again.
        return _decide(context, start, annotation, seams)
    context.log(f"Merged-head CI gate: head {head} is not the approved head {approved}; waiting for its CI.")
    record = {**gate_merged_head_ci(context, context.target_worktree, head, seams), **annotation}
    context.log(format_merged_head_ci(record))
    return _decide(context, start, record, seams)


def approval_stands(context: FlowContext, result: StageResult) -> bool:
    """Whether ``result`` and Stage 4's latest result are approvals no merged-head CI failure has withdrawn."""
    latest = context.stage_results.get(FlowStage.REVIEW.value) or result
    approved = result.status == StageStatus.APPROVED and latest.status == StageStatus.APPROVED
    return approved and withdrawn_consensus_of(context) is None and pending_scoped_restart(context) is None


def is_review_restart(result: StageResult) -> bool:
    """Whether a Stage 5 result withdrew the consensus and asks for a fresh Stage 4."""
    return result.stage == FlowStage.CALIBRATE and bool(result.payload.get("review_restart"))


def _run_calibrate(
    context: FlowContext,
    run_stage: Callable[[FlowStage], StageResult],
    record: Callable[[FlowStage, StageResult], None],
    restarts: int,
) -> StageResult:
    """Run and record Stage 5; a restart request past the cap becomes a FAILED Stage 5 (nothing was calibrated)."""
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
    """The log line that names a scoped restart for a CI defect, or the withdrawal that restarts Stage 4."""
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
    """Run Stage 5, restarting Stage 4 at iteration 1 while the merged head's CI asks for a restart.

    ``run_stage`` runs one stage with the engine's hooks and budget; ``record`` stores and checkpoints its result. A
    restarted Stage 4 that does not approve is returned as it is, so the pipeline halts on it as on any Stage 4; when
    it was a scoped restart for a CI defect, the consensus is withdrawn first.
    """
    restarts = 0
    result = _run_calibrate(context, run_stage, record, restarts)
    while is_review_restart(result):
        restarts += 1
        gate, scoped = result.payload.get("merged_head_ci") or {}, bool(result.payload.get("scoped_restart"))
        approved = result.payload.get("approved_head")  # Named before the restarted Stage 4 replaces the consensus.
        context.log(f"{_restart_note(result)} (restart {restarts} of {MAX_MERGED_HEAD_REVIEW_RESTARTS}).")
        review = run_stage(FlowStage.REVIEW)
        record(FlowStage.REVIEW, review)
        if review.status != StageStatus.APPROVED:
            if scoped and gate.get("head_sha"):
                withdraw_after_rejected_restart(context, gate, approved)
            return review
        result = _run_calibrate(context, run_stage, record, restarts)
    return result
