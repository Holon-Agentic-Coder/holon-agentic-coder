from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sandbox_executor.flow import stage_conflicts
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.github.pr import format_review_git_trailers
from sandbox_executor.flow.host_remote import push_commit, remote_error
from sandbox_executor.flow.review import sync_points as _sp
from sandbox_executor.flow.review.approval_carry_over import carry_approval_over, record_stage5_carry_over
from sandbox_executor.flow.review.conflict_merge import _is_ledger_path, _MergeOutcome
from sandbox_executor.flow.review.ledger_commit import (
    _commit_pending_ledger_rows,
    _LedgerCommit,
    _only_ledger_commits_ahead,
)
from sandbox_executor.flow.target_sync_result import SyncBoundary
from sandbox_executor.git import run_git
from sandbox_executor.ledger import format_commit_trailers, generate_action_id
from sandbox_executor.redaction import redact_agent_secrets

if TYPE_CHECKING:
    from sandbox_executor.flow.review.posting import PostRequest

MergeabilityProbe = Callable[..., dict[str, Any]]
MERGED_REASON = "target branch merged; approval must be re-earned on the new head unless its PR-own fingerprint matches"
UNPUSHED_ROWS_REASON = "pending ledger rows could not be pushed"


@dataclass(frozen=True)
class _ConflictGateResult:
    conflicting: bool
    merged: bool = False
    ledger_only: bool = False
    blocked_reason: str | None = None
    merged_head: str = ""
    mechanical_only: bool = False
    rereview_scope: tuple[str, ...] = ()


def _is_conflicting(mergeability: Mapping[str, Any] | None) -> bool:
    probe = mergeability or {}
    state = str(probe.get("merge_state_status") or "").upper()
    if state == "MISSING_REF":
        return False
    return str(probe.get("mergeable") or "").upper() == "CONFLICTING" or state == "DIRTY" or bool(probe.get("is_dirty"))


def _blocked_reason(outcome: _MergeOutcome, target_branch: str) -> str:
    if outcome.merged:
        return MERGED_REASON
    if outcome.conflicting_paths:
        kind = "merge" if all(_is_ledger_path(p) for p in outcome.conflicting_paths) else "code"
        return f"{kind} conflicts with {target_branch} in: {', '.join(outcome.conflicting_paths)}"
    return f"merging {target_branch} failed: {outcome.error or 'unknown error'}"


@dataclass(frozen=True)
class _GateMerge:
    trailers: str
    iteration: int = 0
    committed: _LedgerCommit | None = None
    boundary: SyncBoundary = SyncBoundary.PUSH


def _local_commits_unpushed(context: FlowContext, branch: str) -> bool:
    repo_dir, local = context.target_worktree, f"refs/heads/{branch}"
    if run_git(["rev-parse", "--verify", "-q", local], cwd=repo_dir, check=False).returncode != 0:
        return False
    ancestor = ["merge-base", "--is-ancestor", local, f"refs/remotes/origin/{branch}"]
    if run_git(ancestor, cwd=repo_dir, check=False).returncode == 0:
        return False
    if context.skip_push:
        error: str | None = "pushing is skipped"
    elif not _only_ledger_commits_ahead(repo_dir, local, branch):
        error = "it holds commits beyond the ledger rows, which the gate never pushes"
    else:
        error = remote_error(push_commit, repo_dir, local, branch)
    if error:
        context.log(f"[Review Loop] Conflict gate: local '{branch}' is ahead of origin and unpushed: {error}")
    return error is not None


def _gate_conflicting_head(
    context: FlowContext,
    branch: str | None,
    target_branch: str,
    mergeability: Mapping[str, Any],
    merge: _GateMerge | None,
) -> _ConflictGateResult:
    if not _is_conflicting(mergeability):
        return _ConflictGateResult(conflicting=False)
    if merge is None or not branch:
        why = "dry run, nothing merged" if branch else "the head branch is unknown, nothing merged"
        reason = f"the head conflicts with {target_branch} ({why})"
        context.log(f"[Review Loop] Conflict gate: {reason}.")
        return _ConflictGateResult(conflicting=True, blocked_reason=reason)
    pending = _commit_pending_ledger_rows(context, merge.iteration, "before conflict merge") or merge.committed
    if (pending is not None and not pending.pushed) or _local_commits_unpushed(context, branch):
        context.log(f"[Review Loop] Conflict gate on '{branch}': {UNPUSHED_ROWS_REASON}; nothing merged.")
        return _ConflictGateResult(conflicting=True, blocked_reason=UNPUSHED_ROWS_REASON)
    stage = FlowStage.CALIBRATE if merge.boundary is SyncBoundary.CALIBRATE else FlowStage.REVIEW
    result = _sp.sync_review_head(context, branch, target_branch, merge.boundary, stage=stage)
    if result.status == "unchanged" and result.probe is not None and not result.probe.moved:
        return _ConflictGateResult(conflicting=False)
    if result.status == "merged":
        mech, scope = _sp.is_mechanical_only(result), _sp.compute_rereview_scope(result)
        ledger = _sp.is_ledger_only_sync(result)
        context.log(f"[Review Loop] Conflict gate on '{branch}': {MERGED_REASON}.")
        return _ConflictGateResult(True, True, ledger, MERGED_REASON, result.head_after, mech, scope)
    if result.status in ("unchanged", "deferred"):
        reason = f"target sync {result.status} without resolving conflicts with {target_branch}"
    else:
        reason = _sp.format_conflict_block_reason(result, target_branch)
    context.log(f"[Review Loop] Conflict gate on '{branch}': {reason}.")
    return _ConflictGateResult(True, False, False, reason, "")


def _gate_record(review_target: str, gate: _ConflictGateResult) -> dict[str, Any]:
    return {
        "action_id": generate_action_id("ACT-GATE"),
        "execution_id": f"REV-GATE-{int(datetime.now(UTC).timestamp())}",
        "action": f"Approved consensus not posted for {review_target}: the head conflicts with the target branch",
        "category": "code",
        "target": review_target,
        "outcome": "failure",
        "entry_type": "action",
        "action_origin": "pr_review",
        "labels": ["review", "conflict-gate"],
        "error_context": redact_agent_secrets(str(gate.blocked_reason)),
        "created_at": datetime.now(UTC).isoformat(),
    }


def _approved_post_gate(
    context: FlowContext, exec_file: str, request: PostRequest, probe: MergeabilityProbe
) -> tuple[str, dict[str, Any]] | None:
    # why: imported lazily because records imports review_report, which would form an import cycle.
    from sandbox_executor.flow.review.records import record_review_action

    review_target, target_branch = request.review_target, str(request.consensus_data.get("base_branch") or "main")
    mergeability = probe(target_branch=target_branch)
    branch = context.execution_branch or (review_target if not review_target.isdigit() else None)
    trailers = format_review_git_trailers("pr_review", request.iteration)
    merge = None if context.dry_run else _GateMerge(trailers, request.iteration)
    gate = _gate_conflicting_head(context, branch, target_branch, mergeability, merge)
    if gate.blocked_reason is None:
        return None
    if gate.merged and not gate.mechanical_only:
        _sp.record_rereview_scope(context, gate.rereview_scope, approved=True)
    if (
        gate.merged
        and gate.mechanical_only
        and carry_approval_over(context, exec_file, request.consensus_data, review_target, gate.merged_head)
    ):
        # why: a mechanical-only merge carries the approval to the merged head; Stage 5 still waits for its CI.
        return None
    record_review_action(context, exec_file, _gate_record(review_target, gate))
    return f"conflict gate: {gate.blocked_reason}", {"posted": False, "conflict_gate": asdict(gate)}


def _stage5_conflict_gate(context: FlowContext) -> StageResult | None:
    if not context.execution_branch:
        return None
    target_branch = str(context.intent_data.get("target_branch") or "main")
    start_time, iteration = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), context.review_iterations_done
    committed = _commit_pending_ledger_rows(context, iteration, "before Stage 5")
    mergeability = stage_conflicts.check_pr_mergeability(
        context.target_worktree, branch=context.execution_branch, target_branch=target_branch
    )
    trailers = format_commit_trailers("calibration")
    merge = None if context.dry_run else _GateMerge(trailers, iteration, committed, SyncBoundary.CALIBRATE)
    gate = _gate_conflicting_head(context, context.execution_branch, target_branch, mergeability, merge)
    if gate.merged:
        record_stage5_carry_over(
            context,
            target_branch,
            gate.merged_head,
            mechanical_only=gate.mechanical_only,
            rereview_scope=gate.rereview_scope,
        )
    if gate.blocked_reason is None or gate.merged:
        return None
    reason = f"Calibrate stage refused: {gate.blocked_reason}"
    return _stage_failure(context, FlowStage.CALIBRATE, start_time, reason, conflict_gate=asdict(gate))
