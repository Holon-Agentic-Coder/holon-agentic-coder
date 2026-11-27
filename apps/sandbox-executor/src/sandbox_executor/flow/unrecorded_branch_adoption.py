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
    branch: str
    execution_id: str
    tip: str
    checked_out: bool
    commits_past_plan: int


def _git_stdout(repo_dir: str, args: list[str]) -> str | None:
    try:
        res = run_git(args, cwd=repo_dir, check=False)
    except TimeoutError:
        return None
    return res.stdout.strip() if res.returncode == 0 and isinstance(res.stdout, str) else None


def _commit_sha(repo_dir: str, ref: str) -> str | None:
    return _git_stdout(repo_dir, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]) or None


def _newest_execution_branch(repo_dir: str, plan_prefix: str, existing: set[str]) -> str | None:
    candidates = set(existing) | set(new_local_child_branches(repo_dir, plan_prefix, "E", existing))
    candidates = {branch for branch in candidates if _execution_seq(branch) >= 0}
    return max(candidates, key=_execution_seq, default=None)


def _branch_tip(repo_dir: str, local: str | None, origin: str | None) -> str | None:
    if not origin:
        return local
    if not local or local == origin or _is_ancestor_commit(repo_dir, local, origin):
        return origin
    if _is_ancestor_commit(repo_dir, origin, local):
        return local
    # why: adopting either side of a diverged local head and origin tip could drop the other side's commits
    return None


def _work_past_plan(repo_dir: str, plan_ref: str, tip: str) -> int:
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
    # why: a host that dies during Stage 3 can leave executor commits on an execution branch with no summary row;
    # why: only the newest branch counts, and any summary row or failed git read means nothing to adopt
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
    if newest := _newest_partial_head(repo_dir, branches):
        return newest[1]
    return _resolve_branch_ref(repo_dir, plan_branch) if plan_branch else None


def _checkout_blocking_paths(repo_dir: str, start: str | None) -> list[str]:
    if not start:
        return []
    # why: git refuses a checkout only when dirty tracked files also differ between HEAD and the start point
    dirty = _git_stdout(repo_dir, ["diff", "--name-only", "HEAD", "--"])
    differing = _git_stdout(repo_dir, ["diff", "--name-only", "HEAD", start, "--"]) if dirty else None
    if not dirty or not differing:
        return []
    return sorted(set(dirty.splitlines()) & set(differing.splitlines()))


def _blocking_checkout_error(paths: list[str], start: str) -> str:
    start_label = start.removeprefix(ORIGIN_REF_PREFIX) if start.startswith("refs/") else start[:12]
    named = ", ".join(redact_agent_secrets(path) for path in paths[:_MAX_NAMED_PATHS])
    more = f" and {len(paths) - _MAX_NAMED_PATHS} more" if len(paths) > _MAX_NAMED_PATHS else ""
    return (
        "Execute stage failed: the mounted worktree has uncommitted changes that would be overwritten by checking "
        f"out {start_label}: {named}{more}; commit or discard them in the host worktree before resuming"
    )
