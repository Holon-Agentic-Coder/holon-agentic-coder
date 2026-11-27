from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.pr import check_pr_mergeability
from sandbox_executor.flow.review.conflict_gate import MergeabilityProbe, _approved_post_gate
from sandbox_executor.flow.review.records import _posting_failure_context, record_review_action
from sandbox_executor.ledger import generate_action_id
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.review_ensemble import ConsensusVerdict, ReviewerResult, ReviewerStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PostFacts:
    dry_run: bool
    skip_post: bool
    # why: infrastructure errors share this flag so they fail closed like a refused run and never post.
    failed: bool
    reviewer_completed: bool
    consensus_reached: bool
    # why: every early stop shares one flag so a halted loop keeps its report local unless consensus was reached.
    halted_early: bool
    iteration: int
    last_label: int
    verdict_terminal: bool


@dataclass(frozen=True)
class PostDecision:
    post: bool
    reason: str = ""
    # why: a dry run or --skip-post still counts a suppressed ledger record.
    count_suppressed: bool = False


_HALTED = "the loop halted before a terminal state; the report is kept local"
_FAILED_VERDICT = (
    "the iteration halted on a failed verdict or a crashed, timed-out or stalled reviewer turn; "
    "the report is kept local"
)


def is_terminal_verdict(verdict: ConsensusVerdict | None, reviewers: list[ReviewerResult]) -> bool:
    terminal = verdict in (ConsensusVerdict.CHANGES_REQUESTED, ConsensusVerdict.APPROVED)
    return terminal and all(result.status is ReviewerStatus.COMPLETED for result in reviewers)


# why: only a terminal state posts: a consensus approval, or the label budget exhausted with findings open.
# why: an iteration halting on a failed verdict stays local even on the last label.
def decide_post(facts: PostFacts) -> PostDecision:
    if facts.dry_run or facts.skip_post:
        return PostDecision(post=False, count_suppressed=True)
    if facts.failed or not facts.reviewer_completed:
        return PostDecision(post=False, reason="the review failed" if facts.failed else "no reviewer completed")
    if facts.consensus_reached:
        return PostDecision(post=True)
    if facts.halted_early or not facts.verdict_terminal:
        return PostDecision(post=False, reason=_HALTED if facts.halted_early else _FAILED_VERDICT)
    if facts.iteration < facts.last_label:
        return PostDecision(post=False, reason="the loop stopped before the iteration cap; the report is kept local")
    return PostDecision(post=True)


@dataclass(frozen=True)
class PostRequest:
    repo_dir: str | Path
    review_target: str
    consensus_data: dict[str, Any]
    proxy_env: dict[str, str] | None
    reviewers: Any
    title: str
    iteration: int
    mergeability_probe: MergeabilityProbe | None = None


def _attempt_post(post_fn: Callable[..., dict[str, Any]], context: FlowContext, request: PostRequest) -> dict[str, Any]:
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
        safe_error = redact_agent_secrets(str(exc))
        logger.warning("Posting review consensus for %s failed: %s", request.review_target, safe_error)
        context.log(f"Warning: Posting review consensus for '{request.review_target}' failed: {safe_error}")
        return {"error": safe_error}


def _posting_action_record(request: PostRequest, posting_res: dict[str, Any]) -> tuple[dict[str, Any], bool]:
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
        # why: successful rows keep their historical shape; only failures carry the posting error.
        record["error_context"] = _posting_failure_context(posting_res)
    record["created_at"] = datetime.now(UTC).isoformat()
    return record, posted


def post_review_consensus(
    post_fn: Callable[..., dict[str, Any]], context: FlowContext, exec_file: str | Path, request: PostRequest
) -> tuple[str | None, dict[str, Any]]:
    # why: a CONFLICTING or DIRTY head is never approved; the gate merges the target and the approval is re-earned.
    if request.consensus_data.get("approved"):
        probe = functools.partial(
            request.mergeability_probe or check_pr_mergeability,
            str(request.repo_dir),
            request.review_target,
            proxy_env=request.proxy_env,
        )
        blocked = _approved_post_gate(context, str(exec_file), request, probe)
        if blocked:
            context.log(f"[Review Loop] Not posting the approved consensus: {blocked[0]}")
            return blocked
    posting_res = _attempt_post(post_fn, context, request)
    record, posted = _posting_action_record(request, posting_res)
    record_review_action(context, exec_file, record)
    if posted:
        return None, posting_res
    # why: fail closed; an approval nobody can see on the PR is not an approval.
    post_error = f"Posting the review consensus for '{request.review_target}' failed: {record['error_context']}"
    context.log(f"[Review Loop] {post_error}")
    return post_error, posting_res


def log_skipped_post(context: FlowContext, review_target: str, decision: PostDecision) -> None:
    if decision.reason:
        context.log(f"[Review Loop] Not posting a review consensus for '{review_target}': {decision.reason}.")
