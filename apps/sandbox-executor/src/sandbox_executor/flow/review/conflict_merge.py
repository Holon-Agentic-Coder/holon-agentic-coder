"""The conflict gate's merge: ``origin/<target>`` into ``origin/<head>`` in a temporary worktree, then pushed.

Ledger-only conflicts are resolved without a test run (target rows stay in place, head-only rows are inserted at
their ``created_at`` slot); a code conflict aborts the merge. :mod:`.conflict_gate` decides when to merge.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, replace

from sandbox_executor.flow.git_refs import _fetch_refspecs_error
from sandbox_executor.flow.github.merge import PROTECTED_BRANCHES, _bare_branch
from sandbox_executor.flow.host_remote import push_commit
from sandbox_executor.flow.ledger_reconcile import reconcile_ledger_file
from sandbox_executor.flow.review.git_identity import REVIEW_GIT_ENV
from sandbox_executor.git import run_git
from sandbox_executor.ledger import LedgerRewriteError
from sandbox_executor.redaction import redact_agent_secrets


@dataclass(frozen=True)
class _MergeOutcome:
    """How merging the target branch went: ``ledger_only`` when only ledgers conflicted; tests are never run."""

    merged: bool
    ledger_only: bool = False
    conflicting_paths: tuple[str, ...] = ()
    error: str = ""
    head_sha: str = ""  # The pushed merge commit, when merged.


def _is_ledger_path(path: str) -> bool:
    """True for an append-only JSONL ledger under ``holon-knowledge/ledger/``."""
    return path.startswith("holon-knowledge/ledger/") and path.endswith(".jsonl")


def _merge_target_ledger_only(worktree: str, target_ref: str, message: str) -> _MergeOutcome:
    """Merge ``target_ref`` into the worktree's HEAD; resolve a ledger-only conflict, abort on any code conflict.

    The ledgers keep the target side's (``theirs`` in a merge) rows in place and insert the head rows by
    ``created_at``. No test command runs.
    The merge commit carries ``message`` (with its provenance trailers) whether or not a ledger conflict was resolved.
    """
    env = {**os.environ, **REVIEW_GIT_ENV}
    merge = run_git(["merge", "--no-ff", "-m", message, target_ref], cwd=worktree, check=False, env=env)
    if merge.returncode == 0:
        return _MergeOutcome(merged=True)
    unmerged = run_git(["diff", "--name-only", "--diff-filter=U"], cwd=worktree, check=False).stdout or ""
    paths = tuple(line.strip() for line in unmerged.splitlines() if line.strip())
    if not paths or not all(_is_ledger_path(path) for path in paths):
        run_git(["merge", "--abort"], cwd=worktree, check=False)
        error = "" if paths else redact_agent_secrets((merge.stderr or merge.stdout or "").strip())
        return _MergeOutcome(merged=False, conflicting_paths=paths, error=error)
    for path in paths:
        if not reconcile_ledger_file(os.path.join(worktree, path), prefix_side="theirs"):
            run_git(["merge", "--abort"], cwd=worktree, check=False)
            return _MergeOutcome(merged=False, conflicting_paths=paths, error=f"could not reconcile {path}")
        run_git(["add", path], cwd=worktree)
    run_git(["commit", "-m", message], cwd=worktree, env=env)
    return _MergeOutcome(merged=True, ledger_only=True, conflicting_paths=paths)


def _follow_merged_head(repo_dir: str, branch: str, merged_sha: str) -> None:
    """Fast-forward the local ``branch`` to the pushed merge, so its next ledger commit still pushes as a fast-forward.

    Never forced: a local branch that is not an ancestor of the merge, or a fast-forward git refuses, stays as it is
    (Stage 5's sync levels it with origin).
    """
    ref = f"refs/heads/{branch}"
    old = run_git(["rev-parse", "--verify", "--quiet", ref], cwd=repo_dir, check=False).stdout.strip()
    if not old or run_git(["merge-base", "--is-ancestor", old, merged_sha], cwd=repo_dir, check=False).returncode:
        return
    if run_git(["symbolic-ref", "--quiet", "HEAD"], cwd=repo_dir, check=False).stdout.strip() == ref:
        run_git(["merge", "--ff-only", "-q", merged_sha], cwd=repo_dir, check=False)
    else:
        run_git(["update-ref", ref, merged_sha, old], cwd=repo_dir, check=False)


def _merge_and_push(repo_dir: str, branch: str, target_branch: str, trailers: str = "") -> _MergeOutcome:
    """Merge ``origin/<target_branch>`` into ``origin/<branch>`` in a temporary worktree and push the result.

    Like :func:`sandbox_executor.flow.github.merge.merge_branch_detailed`, a protected ``branch`` is refused and a
    failed fetch stops the merge; unlike it, the target's ledger rows stay in place (see the module docstring).
    """
    if _bare_branch(branch) in PROTECTED_BRANCHES:
        return _MergeOutcome(merged=False, error=f"refusing to push to protected branch '{branch}'")
    fetch_error = _fetch_refspecs_error(
        repo_dir, [f"+refs/heads/{b}:refs/remotes/origin/{b}" for b in (target_branch, branch)]
    )
    if fetch_error:
        return _MergeOutcome(merged=False, error=f"fetching '{target_branch}' and '{branch}' failed: {fetch_error}")
    message = f"Merge {target_branch} into {branch} (conflict gate)" + (f"\n\n{trailers}" if trailers else "")
    tmp_root = tempfile.mkdtemp(prefix="holon-conflict-gate-")
    worktree = os.path.join(tmp_root, "worktree")
    try:
        run_git(["worktree", "add", "--detach", worktree, f"refs/remotes/origin/{branch}"], cwd=repo_dir)
        outcome = _merge_target_ledger_only(worktree, f"refs/remotes/origin/{target_branch}", message)
        if outcome.merged:
            merged_sha = run_git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()
            push_commit(repo_dir, merged_sha, branch)
            run_git(["update-ref", f"refs/remotes/origin/{branch}", merged_sha], cwd=repo_dir, check=False)
            _follow_merged_head(repo_dir, branch, merged_sha)
            return replace(outcome, head_sha=merged_sha)
        return outcome
    except LedgerRewriteError:
        raise
    except (RuntimeError, TimeoutError, OSError, ValueError) as exc:
        return _MergeOutcome(merged=False, error=redact_agent_secrets(str(exc)))
    finally:
        run_git(["worktree", "remove", "--force", worktree], cwd=repo_dir, check=False)
        run_git(["worktree", "prune"], cwd=repo_dir, check=False)
        shutil.rmtree(tmp_root, ignore_errors=True)
