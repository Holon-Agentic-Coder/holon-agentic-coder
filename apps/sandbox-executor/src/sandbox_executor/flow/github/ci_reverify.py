"""Stage 5's verdict on the head it published: green, red or unverified, and which head the approval covered.

Only ``green`` (or nothing pushed) lets Stage 5 end as a success; ``red`` blocks it and ``unverified`` is reported as
not verified, never as ready to merge. Merging stays with the human maintainer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.approval_invalidation import invalidate_approval
from sandbox_executor.flow.github.ci_failure_detail import bare_detail, describe_failure, short_sha
from sandbox_executor.git import run_git

GREEN = "green"
RED = "red"
UNVERIFIED = "unverified"
SKIPPED = "skipped"
_VERDICT_BY_STATE = {"success": GREEN, "failed": RED, "skipped": SKIPPED}


def _same_commit(first: str | None, second: str | None) -> bool:
    """Whether two SHAs (either may be abbreviated) name the same commit."""
    a, b = str(first or ""), str(second or "")
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def approved_head_of(context: FlowContext) -> str | None:
    """The commit the Stage 4 approval covered, or None when no approval recorded one."""
    review = context.stage_results.get("review")
    consensus = (review.payload.get("consensus") if review else None) or {}
    commit = str(consensus.get("target_commit") or "").strip()
    return commit if consensus.get("approved") and commit and commit != "HEAD" else None


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


def _report(verdict: str, record: Mapping[str, Any], final_head: str | None, moved: bool | None) -> str:
    head = short_sha(final_head)
    if verdict == RED:
        named = ", ".join(describe_failure(d) for d in _failed_details(record))
        return f"CI is red on final head {head}: {named}. Not ready to merge."
    if verdict == UNVERIFIED:
        return f"CI on final head {head} is not verified ({record.get('reason') or 'no result'}). Not merge-ready."
    if verdict == GREEN:
        return f"CI is green on final head {head}" + ("; the head moved after approval." if moved else ".")
    return "CI was not re-verified: nothing was pushed."


def reverify_final_head(record: Mapping[str, Any], approved_head: str | None, final_head: str | None) -> dict[str, Any]:
    """Map a final-head CI record to the Stage 5 verdict, naming the approved and final heads and whether it moved."""
    verdict = _VERDICT_BY_STATE.get(str(record.get("state")), UNVERIFIED)
    moved = None if not (approved_head and final_head) else not _same_commit(approved_head, final_head)
    return {
        "verdict": verdict,
        "approved_head": approved_head,
        "final_head": final_head,
        "head_moved": moved,
        "failed_details": _failed_details(record) if verdict == RED else [],
        "report": _report(verdict, record, final_head, moved),
    }


def blocked_error(reverified: Mapping[str, Any]) -> str:
    """The Stage 5 error of a red final head."""
    covered = reverified.get("approved_head")
    note = f" The approval covered {short_sha(covered)} and no longer stands." if covered else ""
    return f"Stage 5 blocked: {reverified['report']}{note}"


def reverify_and_report(context: FlowContext, repo_dir: str, record: Mapping[str, Any], pushed: bool) -> dict[str, Any]:
    """Re-verify the published head, log the verdict and, on red, post the once-per-head approval invalidation."""
    reverified = reverify_final_head(record, approved_head_of(context), published_head_of(repo_dir) if pushed else None)
    context.log(f"Stage 5 re-verification: {reverified['report']}")
    if reverified["verdict"] == RED:
        reverified["invalidation"] = invalidate_approval(context, reverified)
    return reverified
