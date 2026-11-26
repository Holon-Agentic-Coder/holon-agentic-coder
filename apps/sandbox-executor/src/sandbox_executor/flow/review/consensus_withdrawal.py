"""Withdraw a Stage 4 consensus whose approval CI failed on the merged head, so Stage 4 restarts for the new head.

The withdrawal appends one ACT row to the ledger (earlier rows, the approval included, are never edited: the new row
supersedes the approval by time), posts one PR comment per merged head (a hidden marker keyed on the full SHA is checked
against the existing comments first) and resets the engine-owned review state: the iteration counter goes back to 0,
the carried findings are cleared and the recorded consensus is marked not approved, so nothing can calibrate it later.
A withdrawal after a halted Stage 4 (a scoped review of a CI fix that did not approve) keeps the counter and the
carried findings, so a resume continues that review. It never approves, merges or pushes.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.approval_invalidation import (
    CommentLister,
    CommentPoster,
    approval_pr_number,
    gh_comment_lister,
    gh_comment_poster,
)
from sandbox_executor.flow.github.ci_failure_detail import short_sha
from sandbox_executor.flow.github.ci_head_checks import (
    CANCELLED_CONCLUSIONS,
    PASSING_CONCLUSIONS,
    PENDING_STATUSES,
)
from sandbox_executor.flow.github.ci_reverify import approved_head_of
from sandbox_executor.flow.review.approval_coverage import approved_fingerprint_of
from sandbox_executor.knowledge import CredentialRedactor
from sandbox_executor.ledger import generate_action_id
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.review_accounting import restart_review_passes

logger = logging.getLogger(__name__)

MARKER_PREFIX = "<!-- holon-consensus-withdrawn:"
MAX_COMMENT_CHARS = 4000
WITHDRAWAL_LABELS = ["review", "consensus-withdrawn", "merged-head-ci"]
CI_FAILED = "CI failed on the merged head"
REJECTED_SCOPED_FIX = "CI failed and the scoped review of its fix did not approve"
RESTART_STEP = "The review restarts at iteration 1 for the new head"
CAP_STEP = "The review restart cap was reached, so the review was not restarted and the flow stopped"
HALTED_STEP = "The flow stopped at Stage 4 with that review's open findings kept for a resume"
_Runs = list[Mapping[str, Any]]


@dataclass(frozen=True)
class WithdrawalSeams:
    """Optional comment collaborators; tests inject fakes, the flow uses the ``gh`` adapters."""

    lister: CommentLister | None = None
    poster: CommentPoster | None = None


def withdrawal_marker(merged_head: str) -> str:
    """The hidden marker that keys the withdrawal comment on the full merged SHA."""
    return f"{MARKER_PREFIX}{merged_head} -->"


def failed_runs_of(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The runs of a gate record that failed, as ``{run_id, name, conclusion}``; pending and cancelled runs are not."""
    not_failed = PASSING_CONCLUSIONS | CANCELLED_CONCLUSIONS | PENDING_STATUSES
    runs = record.get("runs") or []
    failed = [run for run in runs if str(run.get("conclusion")) not in not_failed]
    return [
        {"run_id": run.get("run_id"), "name": run.get("name"), "conclusion": run.get("conclusion")} for run in failed
    ]


def _why(merged_head: str, reason: str | None) -> str:
    return f"{reason} on head {merged_head}" if reason else f"CI failed on merged head {merged_head}"


def render_withdrawal(
    approved_head: str | None,
    merged_head: str,
    failed_runs: _Runs,
    next_step: str = RESTART_STEP,
    reason: str | None = None,
) -> str:
    """The comment body: the withdrawn approval, the merged head, each failing check (or the reason), what follows."""
    covered = f"`{short_sha(approved_head)}`" if approved_head else "an earlier commit"
    marker = withdrawal_marker(merged_head)
    lines = [
        "## ⚠️ Ensemble Consensus Approval withdrawn",
        "",
        f"The consensus approval of {covered} was withdrawn: {_why(f'`{short_sha(merged_head)}`', reason)}:",
        "",
        *(f"- {run.get('name')} (run {run.get('run_id') or 'n/a'}): {run.get('conclusion')}" for run in failed_runs),
        "",
        f"No calibration commit was pushed for this head. {next_step}; merging stays with a human maintainer.",
    ]
    text = CredentialRedactor.redact_text("\n".join(lines))[: MAX_COMMENT_CHARS - len(marker) - 2]
    return f"{text}\n\n{marker}"


def _withdrawal_row(
    context: FlowContext, approved: str | None, record: Mapping[str, Any], restart: bool, next_step: str
) -> dict[str, Any]:
    """The ACT row recording the withdrawn approval and whether the review restarts; free text is redacted."""
    merged, failed, reason = str(record.get("head_sha") or ""), failed_runs_of(record), record.get("withdrawal_reason")
    named = ", ".join(f"{run.get('name')}: {run.get('conclusion')}" for run in failed) or "no check named"
    detail = f"{_why(merged, reason)} after the approval of {approved or 'an earlier head'}"
    detail += f". {next_step}." if reason else f": {named}. {next_step}."
    return {
        "action_id": generate_action_id("ACT-WITHDRAW"),
        "execution_id": f"REV-WITHDRAW-{int(datetime.now(UTC).timestamp())}",
        "action": f"Consensus approval withdrawn for {context.execution_branch}: {reason or CI_FAILED}",
        "category": "code",
        "target": str(context.execution_branch or ""),
        "outcome": "failure",
        "entry_type": "action",
        "action_origin": "pr_review",
        "labels": list(WITHDRAWAL_LABELS),
        "error_context": redact_agent_secrets(detail)[:2000],
        "approved_head": approved,
        "merged_head": merged,
        "failed_runs": failed,
        "review_restarted": restart,
        "approval_fingerprint": approved_fingerprint_of(context),
        "created_at": datetime.now(UTC).isoformat(),
    }


def _post_once(
    context: FlowContext, pr_target: str | None, body: str, merged: str, seams: WithdrawalSeams
) -> dict[str, Any]:
    """Post the withdrawal comment unless one for ``merged`` exists; a failure is logged and never raised."""
    if context.dry_run or context.skip_push:
        return {"posted": False, "reason": "dry run or push skipped"}
    if not pr_target:
        return {"posted": False, "reason": "no pull request to comment on"}
    repo_dir = context.target_worktree
    try:
        existing = (seams.lister or gh_comment_lister(repo_dir))(pr_target)
        if existing is None:
            return {"posted": False, "reason": "comments could not be listed"}
        if any(withdrawal_marker(merged) in comment for comment in existing):
            return {"posted": False, "reason": "already posted for this head"}
        posted = (seams.poster or gh_comment_poster(repo_dir))(pr_target, body)
    except Exception as exc:
        logger.warning("Consensus withdrawal comment failed: %s", redact_agent_secrets(str(exc)))
        posted = False
    if not posted:
        context.log(f"Warning: the consensus withdrawal comment on {pr_target} could not be posted.")
    return {"posted": posted, "reason": "posted" if posted else "the comment could not be posted"}


def _reset_review(context: FlowContext, withdrawn: dict[str, Any], halted: bool) -> None:
    """Mark the recorded consensus withdrawn and not approved; reset the Stage 4 counter and carried findings unless
    ``halted`` (the halted Stage 4's open findings and labels must survive for a resume)."""
    review = context.stage_results.get("review")
    if review is not None:
        consensus = {**(review.payload.get("consensus") or {}), "approved": False, "withdrawn": withdrawn}
        review.payload = {**review.payload, "consensus": consensus}
    if not halted:
        restart_review_passes(context)


def withdraw_consensus(
    context: FlowContext,
    record: Mapping[str, Any],
    seams: WithdrawalSeams | None = None,
    restart: bool = True,
    halted: bool = False,
) -> dict[str, Any]:
    """Record, announce and reset a consensus whose merged head failed CI; returns the withdrawal record.

    ``restart`` is False at the review restart cap, so the comment and the row say the flow stopped instead.
    ``halted`` withdraws after a Stage 4 that did not approve: nothing restarts and the review state is kept. The
    record's ``approved_head`` names the approval when the recorded consensus no longer does.
    """
    from sandbox_executor.flow.review.approval_gates import mark_pipeline_red  # Lazy: gates import verdict, records.
    from sandbox_executor.flow.review.records import record_review_action  # Lazy: records imports review_report.

    approved = approved_head_of(context) or record.get("approved_head")
    merged = str(record.get("head_sha") or "")
    restart = restart and not halted
    next_step = HALTED_STEP if halted else RESTART_STEP if restart else CAP_STEP
    reason = record.get("withdrawal_reason")  # Set when the PR-own change differs from the approved one.
    pr_target = approval_pr_number(context) or context.execution_branch
    failed = failed_runs_of(record)
    ledger_path = os.path.join(context.target_worktree, "holon-knowledge", "ledger", "executions.jsonl")
    row = record_review_action(context, ledger_path, _withdrawal_row(context, approved, record, restart, next_step))
    action_id = (row or {}).get("action_id")
    body = render_withdrawal(approved, merged, failed, next_step, reason)
    comment = _post_once(context, pr_target, body, merged, seams or WithdrawalSeams())
    withdrawn = {"merged_head": merged, "approved_head": approved, "action_id": action_id, "review_restarted": restart}
    if fingerprint := approved_fingerprint_of(context):  # Named only when the approval recorded one.
        withdrawn["approval_fingerprint"] = fingerprint
    _reset_review(context, withdrawn, halted)
    if not reason:  # A CI failure turns only the pipeline gate red; the diff gate survives it.
        mark_pipeline_red(context, merged, failed)
    context.log(
        f"Consensus withdrawn: the approval of {short_sha(approved)} no longer stands because {_why(merged, reason)}; "
        f"ledger row {action_id or 'not written'}, comment {comment['reason']}."
    )
    return {**withdrawn, "comment_posted": comment["posted"], "comment": comment, "failed_runs": failed}


def withdrawn_consensus_of(context: FlowContext) -> dict[str, Any] | None:
    """The withdrawal recorded on the Stage 4 consensus, or None when the consensus still stands."""
    review = context.stage_results.get("review")
    withdrawn = ((review.payload.get("consensus") if review else None) or {}).get("withdrawn")
    return withdrawn if isinstance(withdrawn, dict) else None
