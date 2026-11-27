from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.github.adapter import GitHubAdapter
from sandbox_executor.flow.github.adapter_runner import GhResult
from sandbox_executor.flow.github.forge import GitForge
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

_APPROVE_FLAG = "--approve"
_COMMENT_FLAG = "--comment"


def post_pr_review_consensus(  # noqa: PLR0913
    repo_dir: str,
    target: str,
    consensus_data: dict[str, Any],
    proxy_env: dict[str, str] | None = None,
    *,
    reviewers: list[ReviewerResult] | None = None,
    title: str | None = None,
    github: GitForge | None = None,
) -> dict[str, Any]:
    clean_target = str(target or "").strip()
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
    if clean_target:
        review = _submit_review(call, review_flag(approved))
    else:
        review = ReviewAttempt(False, review_flag(approved), False, False, "")
    return posting_outcome(clean_target, review, _post_discussion_comment(call, review))


@dataclass(frozen=True)
class _GhCall:
    github: GitForge
    target: str
    report_text: str

    def review(self, flag: str) -> GhResult:
        return _ran(self.github.post_review(self.target, flag, self.report_text))

    def comment(self) -> GhResult:
        return _ran(self.github.post_comment(self.target, self.report_text))


class _GhSpawnError(RuntimeError):
    pass


def _ran(result: GhResult) -> GhResult:
    if result.error:
        raise _GhSpawnError(result.error)
    return result


def _review_exception(flag: str, self_approval_fallback: bool, exc: Exception) -> ReviewAttempt:
    logger.warning("gh pr review encountered exception: %s", exc)
    return ReviewAttempt(True, flag, False, self_approval_fallback, f"gh pr review encountered exception: {exc}")


def _submit_self_approval_fallback(call: _GhCall) -> ReviewAttempt:
    try:
        fallback = call.review(_COMMENT_FLAG)
    except Exception as exc:
        return _review_exception(_COMMENT_FLAG, True, exc)
    if fallback.returncode == 0:
        return ReviewAttempt(True, _COMMENT_FLAG, True, True, review_submitted_output(fallback.stdout, fallback=True))
    output = review_failed_output(fallback.returncode, fallback.stderr, fallback.stdout, fallback=True)
    return ReviewAttempt(True, _COMMENT_FLAG, False, True, output)


def _submit_review(call: _GhCall, flag: str) -> ReviewAttempt:
    try:
        proc = call.review(flag)
        if proc.returncode == 0:
            return ReviewAttempt(True, flag, True, False, review_submitted_output(proc.stdout))
        if flag != _APPROVE_FLAG or not is_self_approval_rejection(proc.stderr or ""):
            return ReviewAttempt(
                True, flag, False, False, review_failed_output(proc.returncode, proc.stderr, proc.stdout)
            )
        logger.info("Author self-approval restriction encountered (HTTP 422). Falling back to official review comment.")
    except Exception as exc:
        return _review_exception(flag, False, exc)
    return _submit_self_approval_fallback(call)


def _post_discussion_comment(call: _GhCall, review: ReviewAttempt) -> CommentAttempt:
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
