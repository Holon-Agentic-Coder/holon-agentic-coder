"""Carry an approval across a mechanical target-branch merge when the PR's own change is unchanged.

The conflict gate merges ``origin/<target>`` into a conflicting approved head A (cleanly, or resolving ledger-only
conflicts) and pushes the merge B. When B's PR-own change fingerprint
(:mod:`sandbox_executor.flow.review.approval_fingerprint`) equals the one recorded with the approval, the merge added
nothing to the change the reviewers approved, so the approval is kept instead of re-earned: the carry-over is logged
with both SHAs and the fingerprint and recorded as one appended ``ACT-CARRY`` ledger row (no row is edited).

The carry-over removes a review re-run, never a CI wait. ``target_commit`` stays A, the SHA the reviewers saw, and the
consensus gains ``carried_from`` (A) and ``carried_to`` (B); its ``ci_checks`` become "not measured", so the stamp never
shows A's green CI for B. Stage 5's merged-head gate therefore still sees a moved head, waits for every check on B, and
withdraws the consensus and restarts Stage 4 when one fails. A code conflict, a different fingerprint or an approval
without a fingerprint (a legacy checkpoint) keeps the gate's block.

Stage 5 uses the same helpers: its conflict gate records a carry-over row for a merge it made, and its merged-head
gate (:func:`merged_head_coverage`) annotates the CI wait on a carried head, or withdraws the consensus when the PR-own
change differs from the approved one, so Stage 4 reviews it again.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sandbox_executor.flow.git_refs import _fetch_refspecs_error
from sandbox_executor.flow.github.ci_head_checks import FAILED
from sandbox_executor.flow.github.ci_reverify import CHANGED, SAME_FINGERPRINT, ApprovalCoverage, approval_covers
from sandbox_executor.flow.review.approval_fingerprint import approval_fingerprint_of, fingerprints_match
from sandbox_executor.flow.review.merged_head_gate import gate_record
from sandbox_executor.ledger import generate_action_id
from sandbox_executor.redaction import redact_agent_secrets

if TYPE_CHECKING:
    from sandbox_executor.flow.context import FlowContext

CARRY_OVER_LABELS = ["review", "conflict-gate", "approval-carry-over"]


@dataclass(frozen=True)
class CarryOver:
    """An approval of ``approved_head`` kept for ``merged_head``: both carry the PR-own change ``fingerprint``."""

    approved_head: str
    merged_head: str
    fingerprint: str


def decide_carry_over(
    repo_dir: str, consensus_data: Mapping[str, Any], merged_head: str, target_branch: str
) -> CarryOver | None:
    """The carry-over when ``merged_head`` has the approval's recorded fingerprint, else None."""
    recorded = str(consensus_data.get("approval_fingerprint") or "")
    approved = str(consensus_data.get("target_commit") or "").strip()
    if not (recorded and approved and merged_head):
        return None
    current = approval_fingerprint_of(repo_dir, merged_head, target_branch)
    return CarryOver(approved, merged_head, recorded) if fingerprints_match(recorded, current) else None


def carry_over_message(carry: CarryOver) -> str:
    """The log line naming both SHAs and the fingerprint, and that CI on the merged head is still required."""
    return (
        f"approval carried over from {carry.approved_head} to {carry.merged_head} (fingerprint {carry.fingerprint}); "
        f"the target merge added nothing to the PR's own change; CI on {carry.merged_head} is still required before "
        "Stage 5 calibrates."
    )


def carry_over_record(review_target: str, carry: CarryOver) -> dict[str, Any]:
    """The append-only ``ACT-CARRY`` row naming the approved head, the merged head and the fingerprint."""
    action = (
        f"Approval for {review_target} carried over from {carry.approved_head} to {carry.merged_head}: "
        f"same PR-own change (fingerprint {carry.fingerprint})"
    )
    return {
        "action_id": generate_action_id("ACT-CARRY"),
        "execution_id": f"REV-CARRY-{int(datetime.now(UTC).timestamp())}",
        "action": redact_agent_secrets(action),
        "category": "code",
        "target": review_target,
        "outcome": "success",
        "entry_type": "action",
        "action_origin": "pr_review",
        "labels": list(CARRY_OVER_LABELS),
        "approved_head": carry.approved_head,
        "merged_head": carry.merged_head,
        "approval_fingerprint": carry.fingerprint,
        "created_at": datetime.now(UTC).isoformat(),
    }


def apply_carry_over(consensus_data: dict[str, Any], carry: CarryOver) -> None:
    """Record the carry-over on the consensus; ``target_commit`` stays the head the reviewers saw."""
    consensus_data["carried_from"] = carry.approved_head
    consensus_data["carried_to"] = carry.merged_head
    reason = f"CI on merged head {carry.merged_head} not yet measured; Stage 5 waits for it"
    consensus_data["ci_checks"] = {"measured": False, "reason": reason}


def carry_approval_over(
    context: FlowContext, exec_file: str, consensus_data: dict[str, Any], review_target: str, merged_head: str
) -> bool:
    """Keep an approval the conflict gate's merge did not change: log, append one row, record it; True when kept."""
    from sandbox_executor.flow.review.records import record_review_action  # Lazy: records imports review_report.

    target_branch = str(consensus_data.get("base_branch") or "main")
    carry = decide_carry_over(context.target_worktree, consensus_data, merged_head, target_branch)
    if carry is None:
        return False
    context.log(f"[Review Loop] Conflict gate: {carry_over_message(carry)}")
    record_review_action(context, exec_file, carry_over_record(review_target, carry))
    apply_carry_over(consensus_data, carry)
    return True


def _review_consensus(context: FlowContext) -> dict[str, Any]:
    review = context.stage_results.get("review")
    return dict((review.payload.get("consensus") if review else None) or {})


def record_stage5_carry_over(context: FlowContext, target_branch: str, merged_head: str) -> CarryOver | None:
    """After Stage 5's conflict gate merged the target: log and record the carry-over when the change is unchanged."""
    from sandbox_executor.flow.review.records import record_review_action  # Lazy: records imports review_report.

    consensus = _review_consensus(context)
    if not consensus.get("approved"):
        return None
    carry = decide_carry_over(context.target_worktree, consensus, merged_head, target_branch)
    if carry is None:
        return None
    context.log(f"Calibrate stage conflict gate: {carry_over_message(carry)}")
    ledger_path = os.path.join(context.target_worktree, "holon-knowledge", "ledger", "executions.jsonl")
    record_review_action(context, ledger_path, carry_over_record(str(context.execution_branch or ""), carry))
    review = context.stage_results["review"]
    carried = {**consensus, "carried_from": carry.approved_head, "carried_to": carry.merged_head}
    review.payload = {**review.payload, "consensus": carried}
    return carry


def _coverage(context: FlowContext, head: str) -> ApprovalCoverage:
    """The approval's coverage of ``head``; a mismatch is re-checked once after fetching the base branch."""
    coverage = approval_covers(context, context.target_worktree, head)
    if coverage.how != CHANGED:
        return coverage
    base = str(_review_consensus(context).get("base_branch") or context.intent_data.get("target_branch") or "main")
    refspec = f"+refs/heads/{base}:refs/remotes/origin/{base}"
    if _fetch_refspecs_error(context.target_worktree, [refspec]):
        return coverage  # A stale base can only cost a re-review, never skip one.
    return approval_covers(context, context.target_worktree, head)


def merged_head_coverage(context: FlowContext, approved: str, head: str) -> tuple[str, dict[str, Any]]:
    """How the approval covers a head that moved by more than bookkeeping, and what the merged-head gate records.

    ``SAME_FINGERPRINT`` gives the ``approval_carry_over`` annotation for the CI record (the gate still waits for CI
    on ``head``); ``CHANGED`` gives a failed record that withdraws the consensus so Stage 4 reviews the change again;
    anything else gives an empty annotation (today's CI wait).
    """
    coverage = _coverage(context, head)
    if coverage.how == SAME_FINGERPRINT:
        context.log(
            f"Merged-head CI gate: approval carried over from {approved} to {head} (fingerprint {coverage.recorded}); "
            f"waiting for CI on {head}."
        )
        carry = {"approved_head": approved, "merged_head": head, "approval_fingerprint": coverage.recorded}
        return coverage.how, {"approval_carry_over": carry}
    if coverage.how == CHANGED:
        reason = f"PR-own change changed since approval (fingerprint {coverage.recorded} -> {coverage.current})"
        context.log(f"Merged-head CI gate: head {head} is not covered by the approval of {approved}: {reason}.")
        return coverage.how, {**gate_record(FAILED, head, [], [], reason), "withdrawal_reason": reason}
    return coverage.how, {}
