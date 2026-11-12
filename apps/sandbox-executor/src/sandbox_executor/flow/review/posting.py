"""Stage 4 review posting: decide whether the consensus review reaches the pull request, and post it.

A review is posted only when the loop ends in a terminal state: a consensus approval, or the label budget
exhausted with findings still open. Every other exit keeps the report local.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.review.records import _posting_failure_context, record_review_action
from sandbox_executor.knowledge import CredentialRedactor
from sandbox_executor.ledger import generate_action_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PostFacts:
    """What the loop knows about its exit when it decides whether to post."""

    dry_run: bool
    skip_post: bool
    failed: bool  # A refused diff or host run, or an infrastructure error.
    reviewer_completed: bool
    consensus_reached: bool
    halted_early: bool  # A resolver error or the no-convergence halt, even at the cap.
    iteration: int
    last_label: int


@dataclass(frozen=True)
class PostDecision:
    """Whether to post, and why not when skipping."""

    post: bool
    reason: str = ""
    count_suppressed: bool = False  # Dry run and --skip-post count a suppressed ledger record.


def decide_post(facts: PostFacts) -> PostDecision:
    """Decide whether the consensus review is posted to the pull request."""
    if facts.dry_run or facts.skip_post:
        return PostDecision(post=False, count_suppressed=True)
    if facts.failed or not facts.reviewer_completed:
        return PostDecision(post=False, reason="the review failed" if facts.failed else "no reviewer completed")
    if facts.consensus_reached:
        return PostDecision(post=True)
    if facts.halted_early:
        return PostDecision(post=False, reason="the loop halted before a terminal state; the report is kept local")
    if facts.iteration < facts.last_label:
        return PostDecision(post=False, reason="the loop stopped before the iteration cap; the report is kept local")
    return PostDecision(post=True)


@dataclass(frozen=True)
class PostRequest:
    """The inputs of one post attempt."""

    repo_dir: str | Path
    review_target: str
    consensus_data: dict[str, Any]
    proxy_env: dict[str, str] | None
    reviewers: Any
    title: str
    iteration: int


def _attempt_post(post_fn: Callable[..., dict[str, Any]], context: FlowContext, request: PostRequest) -> dict[str, Any]:
    """Call the poster; a raised error is redacted and returned as ``{"error": ...}``."""
    try:
        return post_fn(
            repo_dir=request.repo_dir,
            target=request.review_target,
            consensus_data=request.consensus_data,
            proxy_env=request.proxy_env,
            reviewers=request.reviewers,
            title=request.title,
        )
    except Exception as exc:
        safe_error = CredentialRedactor.redact_text(str(exc))
        logger.warning("Posting review consensus for %s failed: %s", request.review_target, safe_error)
        context.log(f"Warning: Posting review consensus for '{request.review_target}' failed: {safe_error}")
        return {"error": safe_error}


def _posting_action_record(request: PostRequest, posting_res: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Build the ACT-POST row and report whether the post succeeded."""
    target = request.review_target
    posted = bool(posting_res.get("dual_posted") or (posting_res.get("official_review") or {}).get("success"))
    record: dict[str, Any] = {
        "action_id": generate_action_id("ACT-POST"),
        "execution_id": f"REV-POST-{int(time.time())}",
        "action": (
            f"Review consensus dual-posted to GitHub for {target}"
            if posted
            else f"Review consensus posting to GitHub failed for {target}"
        ),
        "category": "code",
        "target": target,
        "outcome": "success" if posted else "failure",
        "entry_type": "action",
        "action_origin": "pr_review",
        "review_iteration": request.iteration,
        "labels": ["review", "github", "dual-post"],
    }
    if not posted:
        # Successful rows keep their historical shape; failures carry the posting error.
        record["error_context"] = _posting_failure_context(posting_res)
    record["created_at"] = datetime.now(UTC).isoformat()
    return record, posted


def post_review_consensus(
    post_fn: Callable[..., dict[str, Any]], context: FlowContext, exec_file: str | Path, request: PostRequest
) -> tuple[str | None, dict[str, Any]]:
    """Post the consensus review and record ACT-POST; return the fail-closed error (None on success) and the result."""
    posting_res = _attempt_post(post_fn, context, request)
    record, posted = _posting_action_record(request, posting_res)
    record_review_action(context, exec_file, record)
    if posted:
        return None, posting_res
    # Fail closed: an approval nobody can see on the PR is not an approval.
    post_error = f"Posting the review consensus for '{request.review_target}' failed: {record['error_context']}"
    context.log(f"[Review Loop] {post_error}")
    return post_error, posting_res


def log_skipped_post(context: FlowContext, review_target: str, decision: PostDecision) -> None:
    """Log that nothing was posted and why (silent for a dry run or --skip-post)."""
    if decision.reason:
        context.log(f"[Review Loop] Not posting a review consensus for '{review_target}': {decision.reason}.")
