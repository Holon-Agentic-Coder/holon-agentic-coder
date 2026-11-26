"""Stage 3 resume: find an execution branch a host crash left without a summary row, and name checkout blockers.

A host that dies during Stage 3 can leave the executor's commits on an execution branch (on origin, or local only when
the host held it back) with no ``executions.jsonl`` row, so the partial-work lookup ignores it. Only the plan's newest
execution branch is considered: an older one was superseded by newer work. A branch with any summary row, success or
failure, is never touched, and any git read that fails means "nothing to adopt", so the resume goes on as before.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sandbox_executor.flow.git_refs import (
    LOCAL_HEAD_REF_PREFIX,
    ORIGIN_REF_PREFIX,
    _is_ancestor_commit,
    _resolve_branch_ref,
)
from sandbox_executor.flow.host_remote import new_local_child_branches
from sandbox_executor.flow.partial_head import _execution_id, _execution_seq, _has_summary_row, _newest_partial_head
from sandbox_executor.git import changed_paths, run_git
from sandbox_executor.redaction import redact_agent_secrets

_MAX_NAMED_PATHS = 20


@dataclass(frozen=True)
class _UnrecordedBranch:
    """The plan's newest execution branch: commits past the plan tip and no summary row for its id.

    ``checked_out`` is true when the worktree HEAD is on the branch.
    """

    branch: str
    execution_id: str
    tip: str
    checked_out: bool
    commits_past_plan: int


def _git_stdout(repo_dir: str, args: list[str]) -> str | None:
    """The stripped stdout of a read-only git command, or None when it fails."""
    try:
        res = run_git(args, cwd=repo_dir, check=False)
    except TimeoutError:
        return None
    return res.stdout.strip() if res.returncode == 0 and isinstance(res.stdout, str) else None


def _commit_sha(repo_dir: str, ref: str) -> str | None:
    return _git_stdout(repo_dir, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]) or None


def _newest_execution_branch(repo_dir: str, plan_prefix: str, existing: set[str]) -> str | None:
    """The newest execution branch of the plan, on origin (``existing``) or local only."""
    candidates = set(existing) | set(new_local_child_branches(repo_dir, plan_prefix, "E", existing))
    candidates = {branch for branch in candidates if _execution_seq(branch) >= 0}
    return max(candidates, key=_execution_seq, default=None)


def _branch_tip(repo_dir: str, local: str | None, origin: str | None) -> str | None:
    """The tip to adopt: the newer of the local head and the origin tip; None when there is none or the two diverged."""
    if not origin:
        return local
    if not local or local == origin or _is_ancestor_commit(repo_dir, local, origin):
        return origin
    if _is_ancestor_commit(repo_dir, origin, local):
        return local
    return None


def _work_past_plan(repo_dir: str, plan_ref: str, tip: str) -> int:
    """Commits from the plan tip to ``tip``, or 0 when none of them changes anything outside metadata paths."""
    count = _git_stdout(repo_dir, ["rev-list", "--count", f"{plan_ref}..{tip}"])
    if not count or not count.isdigit() or int(count) == 0:
        return 0
    try:
        return int(count) if changed_paths(repo_dir, plan_ref, tip) else 0
    except (RuntimeError, TimeoutError):
        return 0


def _find_unrecorded_branch(
    repo_dir: str,
    plan_branch: str | None,
    plan_prefix: str,
    existing: set[str],
    log: Callable[[str], None] | None = None,
) -> _UnrecordedBranch | None:
    """The plan's newest execution branch when it carries real work past the plan tip and no summary row, else None.

    ``existing`` is the origin snapshot of the plan's execution branches; local-only branches are added to it. A
    branch whose local head and origin tip diverged is left alone and reported through ``log``.
    """
    branch = _newest_execution_branch(repo_dir, plan_prefix, existing)
    plan_ref = _resolve_branch_ref(repo_dir, plan_branch) if plan_branch and branch else None
    if branch is None or plan_ref is None:
        return None
    exec_id = _execution_id(branch)
    refs = (f"{ORIGIN_REF_PREFIX}{branch}", f"{LOCAL_HEAD_REF_PREFIX}{branch}")
    origin, local = (_commit_sha(repo_dir, ref) for ref in refs)
    if any(sha and _has_summary_row(repo_dir, ref, exec_id) for sha, ref in zip((origin, local), refs, strict=True)):
        return None
    tip = _branch_tip(repo_dir, local, origin)
    if tip is None:
        if origin and local and log is not None:
            log(f"Stage 3 resume: unrecorded execution branch {branch} not adopted: local and origin diverged.")
        return None
    commits = _work_past_plan(repo_dir, plan_ref, tip)
    if commits == 0:
        return None
    checked_out = _git_stdout(repo_dir, ["symbolic-ref", "--short", "-q", "HEAD"]) == branch
    return _UnrecordedBranch(branch, exec_id, tip, checked_out, commits)


def _resume_start(repo_dir: str, plan_branch: str | None, branches: set[str]) -> str | None:
    """Where the executor's checkout starts: the newest recorded partial head, else the plan tip."""
    if newest := _newest_partial_head(repo_dir, branches):
        return newest[1]
    return _resolve_branch_ref(repo_dir, plan_branch) if plan_branch else None


def _checkout_blocking_paths(repo_dir: str, start: str | None) -> list[str]:
    """Tracked files with uncommitted changes that also differ between HEAD and ``start``: git refuses that checkout."""
    if not start:
        return []
    dirty = _git_stdout(repo_dir, ["diff", "--name-only", "HEAD", "--"])
    differing = _git_stdout(repo_dir, ["diff", "--name-only", "HEAD", start, "--"]) if dirty else None
    if not dirty or not differing:
        return []
    return sorted(set(dirty.splitlines()) & set(differing.splitlines()))


def _blocking_checkout_error(paths: list[str], start: str) -> str:
    """A Stage 3 failure message naming the files that block checking out ``start`` (a ref or a commit)."""
    start_label = start.removeprefix(ORIGIN_REF_PREFIX) if start.startswith("refs/") else start[:12]
    named = ", ".join(redact_agent_secrets(path) for path in paths[:_MAX_NAMED_PATHS])
    more = f" and {len(paths) - _MAX_NAMED_PATHS} more" if len(paths) > _MAX_NAMED_PATHS else ""
    return (
        "Execute stage failed: the mounted worktree has uncommitted changes that would be overwritten by checking "
        f"out {start_label}: {named}{more}; commit or discard them in the host worktree before resuming"
    )
