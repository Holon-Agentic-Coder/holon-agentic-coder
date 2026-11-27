from __future__ import annotations

import dataclasses
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

from sandbox_executor.entrypoint.execution_workspace import ENGINE_OWNED_LEDGER_PATHS, workspace_mounted
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.merge import PROTECTED_BRANCHES, _bare_branch
from sandbox_executor.flow.host_remote import HostRemoteError, push_commit
from sandbox_executor.flow.review.git_identity import REVIEW_GIT_ENV
from sandbox_executor.git import run_git
from sandbox_executor.ledger.fragments import PENDING_DIR_REL, is_ledger_commit_path
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.review_target import ReviewTarget

logger = logging.getLogger(__name__)

# why: every row Stage 4 writes lands in one of these append-only ledgers; nothing else may enter the commit.
LEDGER_RELS: tuple[str, ...] = ENGINE_OWNED_LEDGER_PATHS
_SUPPRESS_COMMIT_ATTR = "_suppress_ledger_commit"


def _set_ledger_commit_suppressed(context: FlowContext, suppressed: bool) -> None:
    setattr(context, _SUPPRESS_COMMIT_ATTR, bool(suppressed))


def _is_ledger_commit_suppressed(context: FlowContext) -> bool:
    return bool(getattr(context, _SUPPRESS_COMMIT_ATTR, False))


def _suppress_if_empty_pre_iter(context: FlowContext, passes: int, head_changed: bool, halted: bool) -> None:
    if passes == 0 and not head_changed and halted:
        _set_ledger_commit_suppressed(context, True)


@dataclass(frozen=True)
class _LedgerCommit:
    sha: str
    parent: str
    pushed: bool = False


def _pending_ledger_paths(repo_dir: str) -> list[str]:
    spec = ["--", *LEDGER_RELS, PENDING_DIR_REL]
    res = run_git(["status", "--porcelain", "--untracked-files=all", *spec], cwd=repo_dir, check=False)
    if res.returncode != 0:
        return []
    paths = (line[2:].strip() for line in (res.stdout or "").splitlines())
    return [path for path in paths if is_ledger_commit_path(path)]


def _checked_out_branch(repo_dir: str) -> str | None:
    res = run_git(["rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD"], cwd=repo_dir, check=False)
    lines = (res.stdout or "").splitlines() if res.returncode == 0 else []
    if len(lines) != 2 or os.path.realpath(lines[0]) != os.path.realpath(repo_dir) or lines[1] == "HEAD":
        return None
    return lines[1]


def _ledger_commit_message(iteration: int, reason: str = "") -> str:
    subject = f"chore(review): record review ledger rows ({reason or f'iteration {iteration}'})"
    trailers = ["Action-Origin: pr_review"] + ([f"Review-Iteration: {iteration}"] if iteration >= 1 else [])
    return subject + "\n\n" + "\n".join(trailers)


def _commit_ledger_rows(
    repo_dir: str, branch: str, message: str, log: Callable[[str], None] | None = None
) -> _LedgerCommit | None:
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
    # why: an explicit pathspec leaves other staged or modified files exactly as they were.
    run_git(["commit", "-q", "-m", message, "--", *pending], cwd=repo_dir, env={**os.environ, **REVIEW_GIT_ENV})
    return _LedgerCommit(sha=run_git(["rev-parse", "HEAD"], cwd=repo_dir).stdout.strip(), parent=parent)


# why: a resolver commit the host refused to push stays local; a ledger push must never carry it.
def _only_ledger_commits_ahead(repo_dir: str, rev: str, branch: str) -> bool:
    res = run_git(["rev-list", "--parents", f"refs/remotes/origin/{branch}..{rev}"], cwd=repo_dir, check=False)
    if res.returncode != 0:
        return False
    for line in (res.stdout or "").splitlines():
        shas = line.split()
        if len(shas) != 2:
            return False
        names = run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", shas[0]], cwd=repo_dir, check=False)
        if names.returncode != 0 or not all(map(is_ledger_commit_path, (names.stdout or "").splitlines())):
            return False
    return True


def _push_ledger_commit(context: FlowContext, commit: _LedgerCommit, branch: str) -> _LedgerCommit:
    if not _only_ledger_commits_ahead(context.target_worktree, commit.sha, branch):
        _warn(context, f"pushing ledger commit {commit.sha[:8]} refused: it sits on unpushed non-ledger commits")
        return commit
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


# why: uncommitted rows are lost on a worktree reset and twice blocked a target-branch merge by hand.
# why: this never raises: it runs at iteration boundaries, before conflict merges and as Stage 4's exit backstop.
def _commit_pending_ledger_rows(context: FlowContext, iteration: int, reason: str = "") -> _LedgerCommit | None:
    branch = context.execution_branch
    # why: inside a role container the host commits the mounted worktree.
    if (
        _is_ledger_commit_suppressed(context)
        or context.dry_run
        or workspace_mounted()
        or not branch
        or _bare_branch(branch) in PROTECTED_BRANCHES
    ):
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


# why: a ledger-only move is no diff change for convergence.
def _advance_past_ledger_commit(
    target: ReviewTarget | None, prev_head_sha: str, commit: _LedgerCommit | None
) -> tuple[ReviewTarget | None, str]:
    if target is None or commit is None or commit.parent != target.head_sha:
        return target, prev_head_sha
    carried = commit.sha if prev_head_sha == target.head_sha else prev_head_sha
    return dataclasses.replace(target, head_sha=commit.sha), carried


def _close_review_stage(context: FlowContext, on_branch: list[str], restore: Callable[..., object]) -> None:
    try:
        if on_branch:
            # why: restoring first re-attaches a HEAD left detached at the tip, so its rows are committed, not skipped.
            restore(context.target_worktree, on_branch[0], "Stage 4 review loop", context.log, run_git)
            _commit_pending_ledger_rows(context, context.review_iterations_done, "stage 4 exit")
    finally:
        _set_ledger_commit_suppressed(context, False)
