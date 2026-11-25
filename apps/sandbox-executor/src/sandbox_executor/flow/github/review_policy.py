from __future__ import annotations

from dataclasses import dataclass
from typing import Any

GH_UNAVAILABLE_MESSAGE = "GitHub CLI ('gh') is not available in PATH; skipping review consensus posting."

_APPROVE_FLAG = "--approve"
_COMMENT_FLAG = "--comment"
_SELF_APPROVAL_MARKERS = ("422", "Can not approve your own pull request", "PullRequestReviewAuthorError")
_OFFICIAL_REVIEW_SUBMITTED_MESSAGE = "Official review submitted."
_FALLBACK_REVIEW_SUBMITTED_MESSAGE = "Fallback review comment submitted."
_DISCUSSION_COMMENT_POSTED_MESSAGE = "Discussion comment posted successfully."
_COMMENT_SKIPPED_REVIEW_POSTED_MESSAGE = (
    "Discussion comment not attempted: the official review submission posted the report."
)
_COMMENT_SKIPPED_NO_TARGET_MESSAGE = "Discussion comment not attempted: no posting target was resolved."
_CONSENSUS_POSTED_ONCE_MESSAGE = "Review consensus posted once to GitHub."


@dataclass(frozen=True)
class ReviewAttempt:
    attempted: bool
    flag: str
    success: bool
    self_approval_fallback: bool
    output: str


@dataclass(frozen=True)
class CommentAttempt:
    attempted: bool
    success: bool
    output: str


def review_flag(approved: bool) -> str:
    return _APPROVE_FLAG if approved else _COMMENT_FLAG


def is_self_approval_rejection(stderr: str) -> bool:
    return any(marker in stderr for marker in _SELF_APPROVAL_MARKERS)


def review_submitted_output(stdout: str | None, *, fallback: bool = False) -> str:
    default = _FALLBACK_REVIEW_SUBMITTED_MESSAGE if fallback else _OFFICIAL_REVIEW_SUBMITTED_MESSAGE
    return (stdout or "").strip() or default


def review_failed_output(returncode: int, stderr: str | None, stdout: str | None, *, fallback: bool = False) -> str:
    label = "Fallback review comment failed" if fallback else "Official review submission failed"
    return f"{label} (exit code {returncode}): {stderr or stdout or ''}".strip()


def comment_not_attempted_output(review_attempted: bool) -> str:
    if review_attempted:
        return _COMMENT_SKIPPED_REVIEW_POSTED_MESSAGE
    return _COMMENT_SKIPPED_NO_TARGET_MESSAGE


def comment_posted_output(stdout: str | None) -> str:
    return (stdout or "").strip() or _DISCUSSION_COMMENT_POSTED_MESSAGE


def comment_failed_output(returncode: int, stderr: str | None, stdout: str | None) -> str:
    return f"gh pr comment failed (exit code {returncode}): {stderr or stdout or ''}".strip()


def gh_unavailable_outcome(target: str, approved: bool) -> dict[str, Any]:
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
    posted_once = review.success or comment.success
    return {
        "target": target,
        "gh_available": True,
        "diagnostics": (
            _CONSENSUS_POSTED_ONCE_MESSAGE
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
