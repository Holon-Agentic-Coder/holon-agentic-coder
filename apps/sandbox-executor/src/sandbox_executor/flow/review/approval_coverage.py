"""Whether the Stage 4 approval still covers a head: the same head, or the same PR-own change fingerprint.

:func:`approval_covers` only annotates reports and records; it never stands in for CI on the head.
"""

from __future__ import annotations

from typing import NamedTuple

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.ci_reverify import _consensus_of, _same_commit, approved_head_of
from sandbox_executor.flow.review.approval_fingerprint import approval_fingerprint_of, fingerprints_match

SAME_HEAD = "same_head"
SAME_FINGERPRINT = "same_fingerprint"
CHANGED = "changed"
UNKNOWN_CHANGE = "unknown"


class ApprovalCoverage(NamedTuple):
    """Whether the approval covers a head, how (``SAME_HEAD``, ``SAME_FINGERPRINT``, ``CHANGED`` or
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

    Both fingerprints must be known to call a change ``CHANGED``; otherwise (a legacy approval, unreadable git) it is
    ``UNKNOWN_CHANGE``, which keeps today's behaviour.
    """
    if _same_commit(approved_head_of(context), head):
        return ApprovalCoverage(True, SAME_HEAD)
    recorded = approved_fingerprint_of(context)
    consensus = _consensus_of(context)
    base = str(consensus.get("base_branch") or context.intent_data.get("target_branch") or "main")
    current = approval_fingerprint_of(repo_dir, head, base) if recorded else None
    if not current:
        return ApprovalCoverage(False, UNKNOWN_CHANGE, recorded)
    how = SAME_FINGERPRINT if fingerprints_match(recorded, current) else CHANGED
    return ApprovalCoverage(how == SAME_FINGERPRINT, how, recorded, current)
