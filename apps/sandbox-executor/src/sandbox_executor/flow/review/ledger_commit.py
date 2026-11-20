"""Commit and push the ledger rows Stage 4 appended, so the flow worktree never holds uncommitted provenance.

The review loop appends rows to ``holon-knowledge/ledger/*.jsonl`` in the flow worktree. Left uncommitted, they are
lost when the worktree is reset or removed, and they block a clean merge of the target branch: twice a flow reached a
conflict with dozens of uncommitted rows that had to be set aside and appended back by hand. The loop calls this module
at every iteration boundary and once after its final rows, the conflict gates call it before merging, and Stage 4's
exit calls it as a backstop.

Guarantees: only the three append-only ledgers enter the commit (an explicit pathspec, whatever else is staged), the
commit is made on the execution branch with the review-loop identity and carries no co-author trailer, the push is a
fast-forward through :func:`sandbox_executor.flow.host_remote.push_commit`, and nothing here ever raises.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

from sandbox_executor.calibration import LEDGER_EXECUTIONS_REL
from sandbox_executor.entrypoint.execution_workspace import workspace_mounted
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.merge import PROTECTED_BRANCHES, _bare_branch
from sandbox_executor.flow.host_remote import HostRemoteError, push_commit
from sandbox_executor.git import run_git
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.review_target import ReviewTarget

logger = logging.getLogger(__name__)

# The only ledgers: every row Stage 4 writes lands in one of these append-only files.
LEDGER_RELS: tuple[str, ...] = (
    "holon-knowledge/ledger/intents.jsonl",
    "holon-knowledge/ledger/plans.jsonl",
    LEDGER_EXECUTIONS_REL,
)


@dataclass(frozen=True)
class _LedgerCommit:
    """A ledger-only commit on the execution branch: ``parent`` is the head it was made on."""

    sha: str
    parent: str
    pushed: bool = False


def _pending_ledger_paths(repo_dir: str) -> list[str]:
    """Return the ledger files modified or untracked in ``repo_dir``; empty when it is not a git worktree."""
    res = run_git(["status", "--porcelain", "--untracked-files=all", "--", *LEDGER_RELS], cwd=repo_dir, check=False)
    if res.returncode != 0:
        return []
    paths = (line[3:].strip() for line in (res.stdout or "").splitlines())
    return [path for path in paths if path in LEDGER_RELS]


def _checked_out_branch(repo_dir: str) -> str | None:
    """The branch checked out when ``repo_dir`` is the root of its worktree; None when detached or not a root."""
    res = run_git(["rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD"], cwd=repo_dir, check=False)
    lines = (res.stdout or "").splitlines() if res.returncode == 0 else []
    if len(lines) != 2 or os.path.realpath(lines[0]) != os.path.realpath(repo_dir) or lines[1] == "HEAD":
        return None
    return lines[1]


def _ledger_commit_message(iteration: int, reason: str = "") -> str:
    """The ledger commit message: a subject naming the iteration or the reason, then the review trailers only."""
    subject = f"chore(review): record review ledger rows ({reason or f'iteration {iteration}'})"
    trailers = ["Action-Origin: pr_review"] + ([f"Review-Iteration: {iteration}"] if iteration >= 1 else [])
    return subject + "\n\n" + "\n".join(trailers)


def _commit_ledger_rows(
    repo_dir: str, branch: str, message: str, log: Callable[[str], None] | None = None
) -> _LedgerCommit | None:
    """Commit the pending ledger files of ``repo_dir`` on ``branch``; None when nothing is pending or off the branch.

    The commit names the ledger paths explicitly, so other staged or modified files stay exactly as they were.

    Raises:
        RuntimeError: If ``git add`` or ``git commit`` fails.
    """
    from sandbox_executor.flow.stages.review import REVIEW_GIT_ENV  # Lazy: stages.review imports the review loop.

    pending = _pending_ledger_paths(repo_dir)
    if not pending:
        return None
    checked_out = _checked_out_branch(repo_dir)
    if checked_out != branch:
        if log:
            log(f"[Review Loop] Ledger rows left uncommitted: HEAD is {checked_out or 'detached'}, not '{branch}'.")
        return None
    parent = run_git(["rev-parse", "HEAD"], cwd=repo_dir).stdout.strip()
    run_git(["add", "--", *pending], cwd=repo_dir)
    run_git(["commit", "-q", "-m", message, "--", *pending], cwd=repo_dir, env={**os.environ, **REVIEW_GIT_ENV})
    return _LedgerCommit(sha=run_git(["rev-parse", "HEAD"], cwd=repo_dir).stdout.strip(), parent=parent)


def _push_ledger_commit(context: FlowContext, commit: _LedgerCommit, branch: str) -> _LedgerCommit:
    """Fast-forward push ``commit``; a rejected push keeps the local commit and is logged, never raised."""
    try:
        push_commit(context.target_worktree, commit.sha, branch)
    except HostRemoteError as exc:
        _warn(context, f"pushing ledger commit {commit.sha[:8]} failed: {exc}")
        return commit
    return dataclasses.replace(commit, pushed=True)


def _warn(context: FlowContext, detail: str) -> None:
    msg = f"[Review Loop] Warning: {redact_agent_secrets(detail)}"
    logger.warning(msg)
    context.log(msg)


def _commit_pending_ledger_rows(context: FlowContext, iteration: int, reason: str = "") -> _LedgerCommit | None:
    """Commit the flow worktree's pending ledger rows on the execution branch and push them; never raises.

    Nothing happens on a dry run, inside a role container (the host commits the mounted worktree), without an execution
    branch, on a protected branch, or when no row is pending. ``skip_push`` commits without pushing.

    Returns:
        The commit (``pushed`` tells whether it reached the remote), or None when nothing was committed.
    """
    branch = context.execution_branch
    if context.dry_run or workspace_mounted() or not branch or _bare_branch(branch) in PROTECTED_BRANCHES:
        return None
    try:
        message = _ledger_commit_message(iteration, reason)
        commit = _commit_ledger_rows(context.target_worktree, branch, message, context.log)
        if commit is None:
            return None
        context.log(f"[Review Loop] Committed ledger rows at {commit.sha[:8]} ({reason or f'iteration {iteration}'}).")
        return commit if context.skip_push else _push_ledger_commit(context, commit, branch)
    except Exception as exc:
        _warn(context, f"committing ledger rows failed: {exc}")
        return None


def _advance_past_ledger_commit(
    target: ReviewTarget | None, prev_head_sha: str, commit: _LedgerCommit | None
) -> tuple[ReviewTarget | None, str]:
    """Move the reviewed head onto a ledger commit made on it; a ledger-only move is no diff change for convergence."""
    if target is None or commit is None or commit.parent != target.head_sha:
        return target, prev_head_sha
    carried = commit.sha if prev_head_sha == target.head_sha else prev_head_sha
    return dataclasses.replace(target, head_sha=commit.sha), carried


def _close_review_stage(context: FlowContext, on_branch: list[str], restore: Callable[..., object]) -> None:
    """Stage 4's exit: commit rows still pending on the execution branch, then hand the worktree back (``restore``)."""
    if on_branch:
        _commit_pending_ledger_rows(context, context.review_iterations_done, "stage 4 exit")
        restore(context.target_worktree, on_branch[0], "Stage 4 review loop", context.log, run_git)
