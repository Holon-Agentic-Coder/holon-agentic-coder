from __future__ import annotations

import os

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.git import run_git


def _checked_out_branch(worktree: str) -> str:
    res = run_git(["symbolic-ref", "-q", "--short", "HEAD"], cwd=worktree, check=False)
    return (res.stdout or "").strip() if res.returncode == 0 and isinstance(res.stdout, str) else ""


def _rev_sha(worktree: str, ref: str) -> str:
    res = run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=worktree, check=False)
    return res.stdout.strip() if res.returncode == 0 and isinstance(res.stdout, str) else ""


def _can_sync_worktree(worktree: str, target_branch: str) -> bool:
    return bool(worktree and os.path.isdir(worktree) and _rev_sha(worktree, f"origin/{target_branch}"))


def _best_branch_ref(worktree: str, branch: str) -> str | None:
    local_ref, remote_ref = f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"
    res = run_git(
        ["for-each-ref", "--format=%(refname) %(objectname)", local_ref, remote_ref],
        cwd=worktree,
        check=False,
    )
    refs: dict[str, str] = {}
    if res.returncode == 0 and isinstance(res.stdout, str):
        for line in res.stdout.splitlines():
            name, _, sha = line.partition(" ")
            if name and sha:
                refs[name] = sha
    local_sha, remote_sha = refs.get(local_ref, ""), refs.get(remote_ref, "")
    if not local_sha:
        return remote_ref if remote_sha else None
    if not remote_sha or local_sha == remote_sha:
        return local_ref
    # why: origin/<branch> wins only when it fast-forwards the local branch, so unpushed local commits are never dropped
    ff = run_git(["merge-base", "--is-ancestor", local_sha, remote_sha], cwd=worktree, check=False)
    return remote_ref if ff.returncode == 0 else local_ref


def _checkout_active_branch(context: FlowContext, branch: str, ref: str | None = None) -> tuple[str, bool, str | None]:
    worktree = context.target_worktree
    ref = ref or _best_branch_ref(worktree, branch)
    if ref is None:
        return "", False, None
    local_ref = f"refs/heads/{branch}"
    # why: checking out a ref the local branch is not an ancestor of would discard unpushed local commits
    if (
        ref != local_ref
        and (local_sha := _rev_sha(worktree, local_ref))
        and run_git(["merge-base", "--is-ancestor", local_sha, ref], cwd=worktree, check=False).returncode != 0
    ):
        ref = local_ref
    current = _checked_out_branch(worktree)
    if current == branch and ref == local_ref:
        return current, False, None
    detached = not current
    prev = current or (run_git(["rev-parse", "HEAD"], cwd=worktree, check=False).stdout or "").strip()
    args = ["checkout", "-q", branch] if ref == local_ref else ["checkout", "-q", "-B", branch, ref]
    res = run_git(args, cwd=worktree, check=False)
    if res.returncode != 0:
        detail = (res.stderr or "").strip() if isinstance(res.stderr, str) else ""
        return prev, detached, detail or f"could not check out '{branch}' from '{ref}'"
    return prev, detached, None


def _restore_checked_out_branch(worktree: str, prev: str, detached: bool, active_branch: str) -> None:
    if prev and prev != active_branch:
        args = ["checkout", "-q", "--detach", prev] if detached else ["checkout", "-q", prev]
        run_git(args, cwd=worktree, check=False)
