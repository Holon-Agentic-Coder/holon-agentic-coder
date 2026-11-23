"""I/O-free consensus review posting decisions.

Which ``gh pr review`` flag to submit, how a failed submission is classified (the HTTP 422 author self-approval
restriction or anything else), and every outcome text and dict returned by
:func:`sandbox_executor.flow.github.review_post.post_pr_review_consensus`. This module spawns nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

GH_UNAVAILABLE_MESSAGE = "GitHub CLI ('gh') is not available in PATH; skipping review consensus posting."

_SELF_APPROVAL_MARKERS = ("422", "Can not approve your own pull request", "PullRequestReviewAuthorError")


@dataclass(frozen=True)
class ReviewAttempt:
    """Outcome of the official ``gh pr review`` submission, including the 422 self-approval fallback."""

    attempted: bool
    flag: str
    success: bool
    self_approval_fallback: bool
    output: str


@dataclass(frozen=True)
class CommentAttempt:
    """Outcome of the last-resort ``gh pr comment`` discussion comment."""

    attempted: bool
    success: bool
    output: str


def review_flag(approved: bool) -> str:
    """Return the ``gh pr review`` flag: ``--approve`` only for an approving report, otherwise ``--comment``."""
    return "--approve" if approved else "--comment"


def is_self_approval_rejection(stderr: str) -> bool:
    """True when ``gh pr review --approve`` stderr shows GitHub's HTTP 422 author self-approval restriction."""
    return any(marker in stderr for marker in _SELF_APPROVAL_MARKERS)


def review_submitted_output(stdout: str | None, *, fallback: bool = False) -> str:
    """Output text of a successful official review (or of the 422 fallback review comment)."""
    default = "Fallback review comment submitted." if fallback else "Official review submitted."
    return (stdout or "").strip() or default


def review_failed_output(returncode: int, stderr: str | None, stdout: str | None, *, fallback: bool = False) -> str:
    """Output text of a failed official review (or of a failed 422 fallback review comment)."""
    label = "Fallback review comment failed" if fallback else "Official review submission failed"
    return f"{label} (exit code {returncode}): {stderr or stdout or ''}".strip()


def comment_not_attempted_output(review_attempted: bool) -> str:
    """Output text when no discussion comment is posted: the review posted the report, or there was no target."""
    if review_attempted:
        return "Discussion comment not attempted: the official review submission posted the report."
    return "Discussion comment not attempted: no posting target was resolved."


def comment_posted_output(stdout: str | None) -> str:
    """Output text of a successful ``gh pr comment``."""
    return (stdout or "").strip() or "Discussion comment posted successfully."


def comment_failed_output(returncode: int, stderr: str | None, stdout: str | None) -> str:
    """Output text of a failed ``gh pr comment``."""
    return f"gh pr comment failed (exit code {returncode}): {stderr or stdout or ''}".strip()


def gh_unavailable_outcome(target: str, approved: bool) -> dict[str, Any]:
    """Return the posting outcome when ``gh`` is not on PATH: nothing attempted, nothing posted."""
    return {
        "target": target,
        "gh_available": False,
        "diagnostics": GH_UNAVAILABLE_MESSAGE,
        "discussion_comment": {"attempted": False, "success": False, "output": GH_UNAVAILABLE_MESSAGE},
        "official_review": {
            "attempted": False,
            "flag": review_flag(approved),
            "success": False,
            "self_approval_fallback": False,
            "output": GH_UNAVAILABLE_MESSAGE,
        },
        "dual_posted": False,
    }


def posting_outcome(target: str, review: ReviewAttempt, comment: CommentAttempt) -> dict[str, Any]:
    """Return the posting outcome once ``gh`` ran; ``dual_posted`` records that the report was posted exactly once."""
    posted_once = review.success or comment.success
    return {
        "target": target,
        "gh_available": True,
        "diagnostics": (
            "Review consensus posted once to GitHub."
            if posted_once
            else f"Review posting failed: review={review.success}, comment={comment.success}."
        ),
        "discussion_comment": {
            "attempted": comment.attempted,
            "success": comment.success,
            "output": comment.output.strip(),
        },
        "official_review": {
            "attempted": review.attempted,
            "flag": review.flag,
            "success": review.success,
            "self_approval_fallback": review.self_approval_fallback,
            "output": review.output.strip(),
        },
        "dual_posted": posted_once,
    }
