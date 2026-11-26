"""The ``ReviewRequest`` of a scoped delta review: the delta diff, the full modified files, the addressed findings.

The delta diff is bounded by ``bound_review_diff``, as the full review's is. Everything agent-visible is redacted; each
file is capped at the decision's ``context_bytes`` with a visible marker and the files together at
``CONTEXT_FILES_FACTOR`` times that, later files listed by name only.
"""

from __future__ import annotations

from collections.abc import Sequence

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.review.delta_review import addressed_findings, current_decision, set_current
from sandbox_executor.flow.review.delta_review_git import DeltaRepo
from sandbox_executor.flow.review.delta_review_mode import FULL, ReviewMode
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.review_request import ReviewRequest, ScopedDelta, bound_review_diff, format_delta_body
from sandbox_executor.review_target import ReviewTarget

CONTEXT_FILES_FACTOR = 4


def _bounded_text(text: str, cap: int) -> str:
    """``text`` cut to ``cap`` UTF-8 bytes, with a marker naming how much was left out."""
    data = text.encode("utf-8")
    if len(data) <= cap:
        return text
    kept = data[:cap].decode("utf-8", "ignore")
    return f"{kept}\n[... truncated: {len(data) - cap} more bytes not shown]"


def _context_files(
    git: DeltaRepo, head: str, paths: Sequence[str], cap: int
) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """``((path, redacted bounded text), ...)`` for the paths that exist as text at ``head``, and those left out."""
    shown: list[tuple[str, str]] = []
    omitted: list[str] = []
    total = 0
    for path in paths:
        text = git.file_at(head, path)
        if text is None:
            continue
        if total >= cap * CONTEXT_FILES_FACTOR:
            omitted.append(path)
            continue
        bounded = _bounded_text(redact_agent_secrets(text), cap)
        shown.append((path, bounded))
        total += len(bounded.encode("utf-8"))
    return tuple(shown), tuple(omitted)


def build_delta_request(
    context: FlowContext, target: ReviewTarget, decision: ReviewMode, iteration: int, pr_text: tuple[str, str]
) -> ReviewRequest:
    """The scoped request for ``target``'s head; raises ``RuntimeError`` when the decision is not for that head."""
    head, repo_dir = target.head_sha, context.target_worktree
    git = DeltaRepo(repo_dir)
    if not decision.scoped or not head or head != decision.head:
        raise RuntimeError("the scoped decision is not for the reviewed head")
    diff_text = redact_agent_secrets(bound_review_diff(git.diff(decision.base, head, decision.paths)))
    if not diff_text.strip():
        raise RuntimeError("the scoped delta diff is empty")
    files, omitted = _context_files(git, head, decision.paths, decision.context_bytes)
    addressed = tuple(addressed_findings(context))
    body = format_delta_body(target.body or pr_text[1], ScopedDelta(decision.base, head, files, addressed, omitted))
    return ReviewRequest(repo_dir, head, decision.base, target.title or pr_text[0], body, diff_text, iteration)


def scoped_review_request(
    context: FlowContext, target: ReviewTarget, iteration: int, pr_text: tuple[str, str]
) -> ReviewRequest | None:
    """The scoped request when this pass is scoped, else None; a failed build falls back to a full pass."""
    decision = current_decision(context)
    if not decision.scoped:
        return None
    try:
        return build_delta_request(context, target, decision, iteration, pr_text)
    except Exception as exc:
        set_current(context, ReviewMode(FULL, "scoped request failed", decision.head))
        context.log(
            f"[Review Loop] Iteration {iteration}: scoped request failed; falling back to full: "
            f"{redact_agent_secrets(str(exc))}"
        )
        return None
