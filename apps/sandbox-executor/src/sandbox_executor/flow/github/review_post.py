"""Posting the Stage 4 consensus review report to the pull request."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.github.adapter import GitHubAdapter
from sandbox_executor.flow.github.adapter_runner import GhResult
from sandbox_executor.flow.github.review_policy import (
    GH_UNAVAILABLE_MESSAGE,
    CommentAttempt,
    ReviewAttempt,
    comment_failed_output,
    comment_not_attempted_output,
    comment_posted_output,
    gh_unavailable_outcome,
    is_self_approval_rejection,
    posting_outcome,
    review_failed_output,
    review_flag,
    review_submitted_output,
)
from sandbox_executor.review_ensemble import ReviewerResult
from sandbox_executor.review_report import build_pr_review_consensus_report, report_approves

logger = logging.getLogger(__name__)


def post_pr_review_consensus(  # noqa: PLR0913
    repo_dir: str,
    target: str,
    consensus_data: dict[str, Any],
    proxy_env: dict[str, str] | None = None,
    *,
    reviewers: list[ReviewerResult] | None = None,
    title: str | None = None,
    github: GitHubAdapter | None = None,
) -> dict[str, Any]:
    """Post the review consensus to GitHub exactly once and handle the HTTP 422 fallback.

    Submits the consensus report a single time via 'gh pr review --approve' (when the
    ensemble approved) or 'gh pr review --comment' (when it did not). When GitHub rejects
    '--approve' with the HTTP 422 author self-approval restriction ('Can not approve your
    own pull request' / 'PullRequestReviewAuthorError'), it falls back once to
    'gh pr review --comment'. Only when the review ladder ends without a successful
    submission does it post a last-resort top-level discussion comment via 'gh pr comment',
    so the report is visible on the PR exactly once per exit. Strictly enforces the
    human-only PR merge rule.

    Args:
        repo_dir: Path to git repository.
        target: Target PR number, URL, or branch name.
        consensus_data: Review consensus diagnostics dictionary.
        proxy_env: Optional proxy environment variables.
        reviewers: Reviewer results of the deciding iteration; the report renders only these.
        title: Resolved report title (intent title, else PR title, else PR number or branch).
        github: The GitHub adapter to post through; defaults to one for repo_dir and proxy_env.

    Returns:
        dict[str, Any]: Posting outcome diagnostics. ``official_review`` describes the primary
        'gh pr review' attempt, ``discussion_comment`` the last-resort 'gh pr comment' fallback
        (normally unattempted), and ``dual_posted`` records that the report was posted exactly
        once.
    """
    clean_target = str(target or "").strip()
    # The report's own guard decides an approval: an APPROVED ensemble verdict, every reviewer clean and no role at
    # CHANGES. No ``approved`` flag or status can promote any other value.
    approved = report_approves(consensus_data, reviewers or [])

    report_text = build_pr_review_consensus_report(
        repo_dir=repo_dir,
        target=clean_target,
        consensus_data=consensus_data,
        reviewers=reviewers or [],
        title=title,
    )

    github = github or GitHubAdapter(repo_dir, proxy_env)
    if not github.available:
        logger.warning(GH_UNAVAILABLE_MESSAGE)
        return gh_unavailable_outcome(clean_target, approved)

    call = _GhCall(github, clean_target, report_text)
    # Primary action: a single official review submission carries the consensus report.
    if clean_target:
        review = _submit_review(call, review_flag(approved))
    else:
        review = ReviewAttempt(False, review_flag(approved), False, False, "")
    # Last resort: only a failed review ladder posts a plain discussion comment, so the
    # consensus report still lands on the PR without ever being posted twice.
    return posting_outcome(clean_target, review, _post_discussion_comment(call, review))


@dataclass(frozen=True)
class _GhCall:
    """The shared arguments of every ``gh pr`` posting command for one consensus report."""

    github: GitHubAdapter
    target: str
    report_text: str

    def review(self, flag: str) -> GhResult:
        """``gh pr review <target> <flag> --body <report>``; raises when ``gh`` could not be run."""
        return _ran(self.github.post_review(self.target, flag, self.report_text))

    def comment(self) -> GhResult:
        """``gh pr comment <target> --body <report>``; raises when ``gh`` could not be run."""
        return _ran(self.github.post_comment(self.target, self.report_text))


class _GhSpawnError(RuntimeError):
    """``gh`` could not be run to completion (missing binary, ``OSError`` or a timeout); the message is redacted."""


def _ran(result: GhResult) -> GhResult:
    """Return ``result``, or raise :class:`_GhSpawnError` so the posting ladder reports it as an exception."""
    if result.error:
        raise _GhSpawnError(result.error)
    return result


def _review_exception(flag: str, self_approval_fallback: bool, exc: Exception) -> ReviewAttempt:
    logger.warning("gh pr review encountered exception: %s", exc)
    return ReviewAttempt(True, flag, False, self_approval_fallback, f"gh pr review encountered exception: {exc}")


def _submit_review(call: _GhCall, flag: str) -> ReviewAttempt:
    """Submit the official review once; on the HTTP 422 self-approval rejection, fall back once to ``--comment``."""
    try:
        proc = call.review(flag)
        if proc.returncode == 0:
            return ReviewAttempt(True, flag, True, False, review_submitted_output(proc.stdout))
        if flag != "--approve" or not is_self_approval_rejection(proc.stderr or ""):
            return ReviewAttempt(
                True, flag, False, False, review_failed_output(proc.returncode, proc.stderr, proc.stdout)
            )
        logger.info("Author self-approval restriction encountered (HTTP 422). Falling back to official review comment.")
    except Exception as exc:
        return _review_exception(flag, False, exc)
    try:
        fallback = call.review("--comment")
    except Exception as exc:
        return _review_exception("--comment", True, exc)
    if fallback.returncode == 0:
        return ReviewAttempt(True, "--comment", True, True, review_submitted_output(fallback.stdout, fallback=True))
    output = review_failed_output(fallback.returncode, fallback.stderr, fallback.stdout, fallback=True)
    return ReviewAttempt(True, "--comment", False, True, output)


def _post_discussion_comment(call: _GhCall, review: ReviewAttempt) -> CommentAttempt:
    """Post the report as a discussion comment only when an attempted review did not succeed."""
    if not review.attempted or review.success:
        return CommentAttempt(False, False, comment_not_attempted_output(review.attempted))
    try:
        proc = call.comment()
        if proc.returncode == 0:
            return CommentAttempt(True, True, comment_posted_output(proc.stdout))
        logger.warning("gh pr comment failed with returncode %s: %s", proc.returncode, proc.stderr)
        return CommentAttempt(True, False, comment_failed_output(proc.returncode, proc.stderr, proc.stdout))
    except Exception as exc:
        logger.warning("gh pr comment encountered exception: %s", exc)
        return CommentAttempt(True, False, f"gh pr comment encountered exception: {exc}")
