"""Posting the Stage 4 consensus review report to the pull request."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Any

from sandbox_executor.review_ensemble import ConsensusVerdict, ReviewerResult, ReviewerStatus
from sandbox_executor.review_report import build_pr_review_consensus_report

logger = logging.getLogger(__name__)


def post_pr_review_consensus(  # noqa: PLR0912, PLR0913, PLR0915
    repo_dir: str,
    target: str,
    consensus_data: dict[str, Any],
    proxy_env: dict[str, str] | None = None,
    *,
    reviewers: list[ReviewerResult] | None = None,
    title: str | None = None,
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

    Returns:
        dict[str, Any]: Posting outcome diagnostics. ``official_review`` describes the primary
        'gh pr review' attempt, ``discussion_comment`` the last-resort 'gh pr comment' fallback
        (normally unattempted), and ``dual_posted`` records that the report was posted exactly
        once.
    """
    clean_target = str(target or "").strip()
    # The ensemble verdict alone decides an approval; no ``approved`` flag or status can promote any other value.
    backed = consensus_data.get("ensemble_verdict") == str(ConsensusVerdict.APPROVED)
    approved = backed and any(r.status is ReviewerStatus.COMPLETED for r in reviewers or [])

    report_text = build_pr_review_consensus_report(
        repo_dir=repo_dir,
        target=clean_target,
        consensus_data=consensus_data,
        reviewers=reviewers or [],
        title=title,
    )

    gh_available = bool(shutil.which("gh"))
    gh_env = {**os.environ, **(proxy_env or {}), "DOCKER_CLI_HINTS": "false"}

    if not gh_available:
        diag_msg = "GitHub CLI ('gh') is not available in PATH; skipping review consensus posting."
        logger.warning(diag_msg)
        return {
            "target": clean_target,
            "gh_available": False,
            "diagnostics": diag_msg,
            "discussion_comment": {
                "attempted": False,
                "success": False,
                "output": diag_msg,
            },
            "official_review": {
                "attempted": False,
                "flag": "--approve" if approved else "--comment",
                "success": False,
                "self_approval_fallback": False,
                "output": diag_msg,
            },
            "dual_posted": False,
        }

    # Primary action: a single official review submission carries the consensus report.
    review_attempted = bool(clean_target)
    review_success = False
    review_output = ""
    self_approval_fallback = False
    initial_flag = "--approve" if approved else "--comment"
    submitted_flag = initial_flag

    if review_attempted:
        try:
            review_proc = subprocess.run(
                ["gh", "pr", "review", clean_target, initial_flag, "--body", report_text],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
                env=gh_env,
            )
            raw_stderr = review_proc.stderr or ""
            raw_stdout = review_proc.stdout or ""

            if review_proc.returncode == 0:
                review_success = True
                review_output = raw_stdout.strip() or "Official review submitted."
            else:
                is_self_approval = (
                    "422" in raw_stderr
                    or "Can not approve your own pull request" in raw_stderr
                    or "PullRequestReviewAuthorError" in raw_stderr
                )
                if is_self_approval and initial_flag == "--approve":
                    logger.info(
                        "Author self-approval restriction encountered (HTTP 422). "
                        "Falling back to official review comment."
                    )
                    self_approval_fallback = True
                    submitted_flag = "--comment"
                    fallback_proc = subprocess.run(
                        ["gh", "pr", "review", clean_target, "--comment", "--body", report_text],
                        cwd=repo_dir,
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=60,
                        env=gh_env,
                    )
                    if fallback_proc.returncode == 0:
                        review_success = True
                        review_output = (fallback_proc.stdout or "").strip() or "Fallback review comment submitted."
                    else:
                        review_success = False
                        fallback_err = fallback_proc.stderr or fallback_proc.stdout or ""
                        review_output = (
                            f"Fallback review comment failed (exit code {fallback_proc.returncode}): {fallback_err}"
                        ).strip()
                else:
                    review_success = False
                    review_output = (
                        f"Official review submission failed (exit code {review_proc.returncode}): "
                        f"{raw_stderr or raw_stdout}"
                    ).strip()
        except Exception as exc:
            review_output = f"gh pr review encountered exception: {exc}"
            logger.warning("gh pr review encountered exception: %s", exc)

    # Last resort: only a failed review ladder posts a plain discussion comment, so the
    # consensus report still lands on the PR without ever being posted twice.
    comment_attempted = review_attempted and not review_success
    comment_success = False
    comment_output = (
        "Discussion comment not attempted: the official review submission posted the report."
        if review_attempted
        else "Discussion comment not attempted: no posting target was resolved."
    )

    if comment_attempted:
        try:
            comment_proc = subprocess.run(
                ["gh", "pr", "comment", clean_target, "--body", report_text],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
                env=gh_env,
            )
            raw_stdout = comment_proc.stdout or ""
            raw_stderr = comment_proc.stderr or ""
            if comment_proc.returncode == 0:
                comment_success = True
                comment_output = raw_stdout.strip() or "Discussion comment posted successfully."
            else:
                comment_output = (
                    f"gh pr comment failed (exit code {comment_proc.returncode}): {raw_stderr or raw_stdout}".strip()
                )
                logger.warning(
                    "gh pr comment failed with returncode %s: %s",
                    comment_proc.returncode,
                    comment_proc.stderr,
                )
        except Exception as exc:
            comment_output = f"gh pr comment encountered exception: {exc}"
            logger.warning("gh pr comment encountered exception: %s", exc)

    posted_once = review_success or comment_success
    return {
        "target": clean_target,
        "gh_available": True,
        "diagnostics": (
            "Review consensus posted once to GitHub."
            if posted_once
            else f"Review posting failed: review={review_success}, comment={comment_success}."
        ),
        "discussion_comment": {
            "attempted": comment_attempted,
            "success": comment_success,
            "output": comment_output.strip(),
        },
        "official_review": {
            "attempted": review_attempted,
            "flag": submitted_flag,
            "success": review_success,
            "self_approval_fallback": self_approval_fallback,
            "output": review_output.strip(),
        },
        "dual_posted": posted_once,
    }
