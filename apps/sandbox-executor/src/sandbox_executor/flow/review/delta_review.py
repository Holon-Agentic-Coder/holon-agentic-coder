"""Scoped delta review: whether a Stage 4 pass reviews only the change since the last reviewed head.

The record lives in ``context.review_state["delta"]`` (checkpointed with the context) and only this module writes it:
``last_reviewed_head`` (the head of the latest pass in which every reviewer completed), ``last_critical`` (whether a
reviewer of that pass reported a critical finding), ``anchor_head`` (the head of the latest such full pass),
``scoped_chain`` (scoped passes since that anchor), ``addressed`` (the findings the resolver was given since) and
``current`` (the decision for the pass in flight) and ``forwarded`` (a decision the host forwarded to the review-loop
container, consumed by the next ``decide_review_mode``). Every unknown, malformed or failed state
decides ``full``: a scoped approval never covers code no completed pass reviewed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.review.delta_review_git import DeltaRepo
from sandbox_executor.flow.review.delta_review_limits import (
    resolve_scoped_review_context_bytes,
    resolve_scoped_review_max_lines,
)
from sandbox_executor.flow.review.delta_review_mode import FULL, SCOPED, ReviewMode, decision_from_dict
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.review_accounting import count_accounting, review_generation
from sandbox_executor.review_ensemble import ReviewerResult, ReviewerStatus

SCOPED_CHAIN_LIMIT = 3
ADDRESSED_LIMIT = 50
SUMMARY_CHARS = 300


def delta_state_of(context: FlowContext) -> dict[str, Any]:
    """The delta-review record, created empty when absent or malformed."""
    state = context.review_state.get("delta")
    if not isinstance(state, dict):
        state = context.review_state["delta"] = {}
    return state


def _text(state: Mapping[str, Any], key: str) -> str:
    value = state.get(key)
    return value if isinstance(value, str) else ""


def _addressed_row(row: Any) -> dict[str, Any] | None:
    """One addressed finding as ``{id, severity, path, line, summary}``, redacted and bounded; None when malformed."""
    get = row.get if isinstance(row, Mapping) else lambda key: getattr(row, key, None)
    line = get("line")
    fields = {key: str(get(key) or "") for key in ("id", "severity", "path")}
    summary = redact_agent_secrets(str(get("summary") or ""))[:SUMMARY_CHARS]
    return {**fields, "line": line if isinstance(line, int) else None, "summary": summary} if fields["id"] else None


def _addressed_rows(rows: Iterable[Any]) -> list[dict[str, Any]]:
    """Up to ``ADDRESSED_LIMIT`` well-formed addressed findings of ``rows``."""
    cleaned = (_addressed_row(row) for row in rows)
    return [row for row in cleaned if row is not None][:ADDRESSED_LIMIT]


def record_addressed_findings(context: FlowContext, rows: Sequence[Any]) -> None:
    """Record the findings handed to the resolver (``FindingRow``s or dicts), for the next scoped pass to verify."""
    delta_state_of(context)["addressed"] = _addressed_rows(rows)


def addressed_findings(context: FlowContext) -> list[dict[str, Any]]:
    """The addressed findings the current pass verifies."""
    current = delta_state_of(context).get("current")
    rows = current.get("addressed") if isinstance(current, Mapping) else None
    return _addressed_rows(rows if isinstance(rows, list) else [])


def current_decision(context: FlowContext) -> ReviewMode:
    """The decision of the pass in flight; ``full`` when none was made."""
    current = delta_state_of(context).get("current")
    return decision_from_dict(current) if isinstance(current, Mapping) else ReviewMode(FULL, "no decision")


def set_current(context: FlowContext, decision: ReviewMode) -> None:
    """Make ``decision`` the decision of the pass in flight; a scoped one carries the addressed findings."""
    state = delta_state_of(context)
    addressed = _addressed_rows(state.get("addressed") or []) if decision.scoped else []
    state["current"] = {**decision.to_dict(), "addressed": addressed}


def set_forwarded(context: FlowContext, decision: ReviewMode) -> None:
    """Hold a decision forwarded from the host for the next ``decide_review_mode`` (review-loop container side)."""
    delta_state_of(context)["forwarded"] = decision.to_dict()


def _precheck(state: Mapping[str, Any], head: str, limit: int) -> str | None:
    """Why the pass must be full without asking git, or None."""
    last = _text(state, "last_reviewed_head")
    chain = state.get("scoped_chain")
    if limit == 0:
        return "disabled"
    if not last or not _text(state, "anchor_head"):
        return "last reviewed head unknown"
    if last == head:
        return "head not moved"
    if state.get("last_critical") is not False:
        return "critical finding on last pass"
    if not isinstance(chain, int) or chain >= SCOPED_CHAIN_LIMIT:
        return "scoped chain limit"
    return None


def _decide_from_git(repo_dir: str, base: str, head: str, scope_paths: Sequence[str], limit: int) -> ReviewMode:
    """The decision once the record allows a scoped pass: ancestry, merges and the size of the reviewable delta."""
    git = DeltaRepo(repo_dir)
    if not git.is_ancestor(base, head):
        return ReviewMode(FULL, "history rewritten", head)
    if git.merge_commits(base, head):
        if not scope_paths:
            return ReviewMode(FULL, "target merge in delta", head)
        candidates = sorted({*git.own_commit_paths(base, head), *scope_paths})
        paths = git.changed_paths(base, head, candidates)
    else:
        paths = git.changed_paths(base, head)
    if not paths:
        return ReviewMode(FULL, "no reviewable change", head)
    lines = git.changed_line_count(base, head, paths)
    if lines is None or lines > limit:
        return ReviewMode(FULL, f"delta {'binary' if lines is None else lines} lines over limit {limit}", head)
    return ReviewMode(SCOPED, f"delta {lines} lines", head, base, tuple(paths), lines)


def decide_review_mode(
    context: FlowContext, repo_dir: str, head: str, *, env: Mapping[str, str] | None = None
) -> ReviewMode:
    """``scoped`` only when a completed pass reviewed an ancestor of ``head`` and the delta is small; else ``full``.

    A decision forwarded from the host (in the review-loop container) is honoured only for the same head.
    """
    state = delta_state_of(context)
    forwarded = state.pop("forwarded", None)
    if forwarded is not None:
        decision = decision_from_dict(forwarded)
        return decision if decision.head == head else ReviewMode(FULL, "forwarded head mismatch", head)
    limit = resolve_scoped_review_max_lines(env)
    reason = _precheck(state, head, limit)
    if reason:
        return ReviewMode(FULL, reason, head)
    try:
        decision = _decide_from_git(repo_dir, state["last_reviewed_head"], head, context.review_scope_paths, limit)
    except (RuntimeError, OSError, TimeoutError, ValueError):
        return ReviewMode(FULL, "delta check failed", head)
    return ReviewMode(**{**asdict(decision), "context_bytes": resolve_scoped_review_context_bytes(env)})


def begin_review_pass(context: FlowContext, repo_dir: str, head: str, iteration: int) -> ReviewMode:
    """Decide and record the mode of pass ``iteration`` on ``head``, and log it."""
    decision = decide_review_mode(context, repo_dir, head)
    set_current(context, decision)
    count_accounting(
        context,
        "scoped_reviews" if decision.scoped else "full_reviews",
        event=f"review:{review_generation(context)}:{head}:{iteration}",
    )
    context.log(f"[Review Loop] Review mode at iteration {iteration}: {decision.mode} ({decision.reason}).")
    return decision


def end_review_pass(context: FlowContext, head: str, reviewers: Sequence[ReviewerResult], ensemble_size: int) -> None:
    """Record what pass on ``head`` reviewed: a failed or missing reviewer leaves the next pass full."""
    state = delta_state_of(context)
    completed = len(reviewers) >= max(1, ensemble_size)
    if not completed or any(r.status is not ReviewerStatus.COMPLETED for r in reviewers):
        state["last_reviewed_head"] = ""
        return
    if current_decision(context).scoped:
        chain = state.get("scoped_chain")
        state["scoped_chain"] = (chain if isinstance(chain, int) else SCOPED_CHAIN_LIMIT) + 1
    else:
        state["anchor_head"], state["scoped_chain"] = head, 0
    state["last_critical"] = any(r.counts.critical > 0 for r in reviewers)
    state["last_reviewed_head"], state["addressed"] = head, []


def adopt_reviewed_head(context: FlowContext, head: str) -> None:
    """Make ``head`` the reviewed anchor, so a fix on top of it can be reviewed scoped; ``last_critical`` stays.

    Called only when the diff approval holds on ``head`` (the same PR-own change), e.g. a merged head whose CI found a
    defect: the merge commit is then behind the anchor and the fix delta is the resolver's change alone.
    """
    state = delta_state_of(context)
    state["last_reviewed_head"], state["anchor_head"], state["scoped_chain"] = head, head, 0
