"""Stage 5's verdict on the head it published: green, red or unverified, and which head the approval covered.

Only ``green`` (or nothing pushed) lets Stage 5 end as a success; ``red`` blocks it and ``unverified`` is reported as
not verified, never as ready to merge. Merging stays with the human maintainer. :func:`approval_covers` says whether
the approval still covers a head: the same head, or the same PR-own change fingerprint. It only annotates reports and
records; it never stands in for CI on the head.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.approval_invalidation import invalidate_approval
from sandbox_executor.flow.github.ci_failure_detail import bare_detail, describe_failure, short_sha
from sandbox_executor.flow.review.approval_fingerprint import approval_fingerprint_of, fingerprints_match
from sandbox_executor.git import run_git

GREEN = "green"
RED = "red"
UNVERIFIED = "unverified"
SKIPPED = "skipped"
_VERDICT_BY_STATE = {"success": GREEN, "failed": RED, "skipped": SKIPPED}
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


def _same_commit(first: str | None, second: str | None) -> bool:
    """Whether two SHAs (either may be abbreviated) name the same commit."""
    a, b = str(first or ""), str(second or "")
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def _consensus_of(context: FlowContext) -> Mapping[str, Any]:
    review = context.stage_results.get("review")
    return (review.payload.get("consensus") if review else None) or {}


def approved_head_of(context: FlowContext) -> str | None:
    """The commit the Stage 4 approval covered, or None when no approval recorded one."""
    consensus = _consensus_of(context)
    commit = str(consensus.get("target_commit") or "").strip()
    return commit if consensus.get("approved") and commit and commit != "HEAD" else None


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


def published_head_of(repo_dir: str) -> str | None:
    """The full SHA Stage 5 left checked out, or None when it cannot be read."""
    try:
        proc = run_git(["rev-parse", "HEAD"], cwd=repo_dir, check=False)
    except Exception:
        return None
    sha = str(getattr(proc, "stdout", "") or "").strip()
    return sha if proc.returncode == 0 and sha else None


def _failed_details(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(record.get("failed_details") or [bare_detail(n) for n in record.get("failed_checks") or []])


def _timing_rerun_clause(record: Mapping[str, Any]) -> str:
    """The clause naming the one automatic timing-gate rerun, empty when none was attempted."""
    note = record.get("timing_rerun") or {}
    return " after one automatic rerun of the timing gate" if note.get("attempted") else ""


def _moved_clause(moved: bool | None, carried: bool) -> str:
    """How a green report ends: whether the head moved after approval, and whether the approval carried over."""
    if not moved:
        return "."
    carry = ", which carried over by PR-own fingerprint" if carried else ""
    return f"; the head moved after approval{carry}."


def _report(verdict: str, record: Mapping[str, Any], final_head: str | None, moved: bool | None, carried: bool) -> str:
    head = short_sha(final_head)
    rerun = _timing_rerun_clause(record)
    if verdict == RED:
        named = ", ".join(describe_failure(d) for d in _failed_details(record))
        return f"CI is red on final head {head}{rerun}: {named}. Not ready to merge."
    if verdict == UNVERIFIED:
        return f"CI on final head {head} is not verified ({record.get('reason') or 'no result'}). Not merge-ready."
    if verdict == GREEN:
        return f"CI is green on final head {head}{rerun}" + _moved_clause(moved, carried)
    return "CI was not re-verified: nothing was pushed."


def reverify_final_head(
    record: Mapping[str, Any], approved_head: str | None, final_head: str | None, carried: bool = False
) -> dict[str, Any]:
    """Map a final-head CI record to the Stage 5 verdict, naming the approved and final heads and whether it moved.

    ``carried`` says the approval was carried over a target-branch merge by PR-own fingerprint; only a green report
    on a moved head names it, and it never changes the verdict.
    """
    verdict = _VERDICT_BY_STATE.get(str(record.get("state")), UNVERIFIED)
    moved = None if not (approved_head and final_head) else not _same_commit(approved_head, final_head)
    return {
        "verdict": verdict,
        "approved_head": approved_head,
        "final_head": final_head,
        "head_moved": moved,
        "failed_details": _failed_details(record) if verdict == RED else [],
        "timing_rerun": record.get("timing_rerun"),
        "approval_carried_over": bool(carried and moved),
        "report": _report(verdict, record, final_head, moved, carried),
    }


def rerun_note(reverified: Mapping[str, Any], refreshed_head: str | None) -> dict[str, Any]:
    """The re-verification record after a head move made Stage 5 rerun: the report names the rerun and the new head."""
    note = f"The head moved after calibration; a Stage 5 rerun refreshed the report to {short_sha(refreshed_head)}."
    return {**reverified, "report": f"{note} {reverified.get('report') or ''}".strip()}


def blocked_error(reverified: Mapping[str, Any]) -> str:
    """The Stage 5 error of a red final head."""
    covered = reverified.get("approved_head")
    note = f" The approval covered {short_sha(covered)} and no longer stands." if covered else ""
    return f"Stage 5 blocked: {reverified['report']}{note}"


def reverify_and_report(context: FlowContext, repo_dir: str, record: Mapping[str, Any], pushed: bool) -> dict[str, Any]:
    """Re-verify the published head, log the verdict and, on red, post the once-per-head approval invalidation."""
    carried = bool(_consensus_of(context).get("carried_to"))
    final_head = published_head_of(repo_dir) if pushed else None
    reverified = reverify_final_head(record, approved_head_of(context), final_head, carried)
    context.log(f"Stage 5 re-verification: {reverified['report']}")
    if reverified["verdict"] == RED:
        reverified["invalidation"] = invalidate_approval(context, reverified)
    return reverified
