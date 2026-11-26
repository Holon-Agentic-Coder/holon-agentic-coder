"""Whether the Stage 4 approval still covers a head: the same head, a bookkeeping-only move, or the same fingerprint.

:func:`approval_covers` only annotates reports and records; it never stands in for CI on the head. Its one side
effect is accounting: a bookkeeping-only move counts one ``non_defect_churn`` (``bookkeeping``) per head, deduplicated
by event, so repeated queries such as ``diff_gate_holds`` never count the same head twice.
"""

from __future__ import annotations

from typing import NamedTuple

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.ci_reverify import _consensus_of, _same_commit, approved_head_of
from sandbox_executor.flow.review.approval_fingerprint import (
    approval_fingerprint_of,
    fingerprints_match,
    is_bookkeeping_only_move,
    is_current_version,
)
from sandbox_executor.review_accounting import count_accounting

SAME_HEAD = "same_head"
SAME_FINGERPRINT = "same_fingerprint"
BOOKKEEPING = "bookkeeping_only"
CHANGED = "changed"
UNKNOWN_CHANGE = "unknown"


class ApprovalCoverage(NamedTuple):
    """Whether the approval covers a head, how (``SAME_HEAD``, ``BOOKKEEPING``, ``SAME_FINGERPRINT``, ``CHANGED`` or
    ``UNKNOWN_CHANGE``), and the recorded and current fingerprints when they were compared."""

    covers: bool
    how: str
    recorded: str | None = None
    current: str | None = None


def approved_fingerprint_of(context: FlowContext) -> str | None:
    """The PR-own change fingerprint recorded with the Stage 4 approval, or None (no approval, a legacy one)."""
    consensus = _consensus_of(context)
    fingerprint = str(consensus.get("approval_fingerprint") or "").strip()
    return fingerprint if consensus.get("approved") and fingerprint else None


def approval_covers(context: FlowContext, repo_dir: str, head: str | None) -> ApprovalCoverage:
    """Whether the Stage 4 approval covers ``head``: the approved head itself, or the same PR-own change.

    A head that moved from the approved head only by bookkeeping commits is ``BOOKKEEPING``, even without a recorded
    fingerprint (a legacy approval) or with a bookkeeping-only PR change (no fingerprint at all).
    Both fingerprints must be known and of the current version to call a change ``CHANGED``; otherwise (a legacy
    approval, a ``pf1`` fingerprint recorded before a version bump, unreadable git) it is ``UNKNOWN_CHANGE``, which
    keeps today's behaviour. A ``BOOKKEEPING`` result counts the head's bookkeeping churn once (see the module doc).
    """
    approved = approved_head_of(context)
    if _same_commit(approved, head):
        return ApprovalCoverage(True, SAME_HEAD)
    if approved and head and is_bookkeeping_only_move(repo_dir, approved, head):
        count_accounting(context, "non_defect_churn", "bookkeeping", event=f"bookkeeping:{head}")
        return ApprovalCoverage(True, BOOKKEEPING)
    recorded = approved_fingerprint_of(context)
    consensus = _consensus_of(context)
    base = str(consensus.get("base_branch") or context.intent_data.get("target_branch") or "main")
    current = approval_fingerprint_of(repo_dir, head, base) if is_current_version(recorded) else None
    if not current:
        return ApprovalCoverage(False, UNKNOWN_CHANGE, recorded)
    how = SAME_FINGERPRINT if fingerprints_match(recorded, current) else CHANGED
    return ApprovalCoverage(how == SAME_FINGERPRINT, how, recorded, current)
