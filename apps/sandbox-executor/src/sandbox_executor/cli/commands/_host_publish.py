from __future__ import annotations

import os
import re

from sandbox_executor import git_flow
from sandbox_executor.flow.host_remote import HostRemoteError, fetch_base, push_branch
from sandbox_executor.git import run_git

_HOLON_BRANCH_PREFIX_RE = re.compile(r"I-\d+-")
_LEADING_INTENT_PREFIX_RE = re.compile(r"^I-\d+-")


def prepare_worktree(base_branch: str, target_worktree: str | None, slug: str) -> str:
    shared_ref_worktree = target_worktree or os.getcwd()
    try:
        fetch_base(shared_ref_worktree, base_branch)
    except HostRemoteError as exc:
        raise RuntimeError(str(exc)) from exc
    if git_flow.is_default_branch_worktree(
        shared_ref_worktree, git_flow.repository_default_branch(shared_ref_worktree)
    ):
        return git_flow.ensure_flow_worktree_off_default(shared_ref_worktree, slug, None)
    return shared_ref_worktree


def push_checked_out_branch(worktree: str) -> str:
    res = run_git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=worktree, check=False)
    checked_out_branch_name = (res.stdout or "").strip() if res.returncode == 0 else ""
    if not checked_out_branch_name:
        raise RuntimeError(f"no branch is checked out in '{worktree}' to push")
    if not _HOLON_BRANCH_PREFIX_RE.match(checked_out_branch_name):
        raise RuntimeError(
            f"branch '{checked_out_branch_name}' checked out in '{worktree}' is not an I-/P-/E- branch; not pushing it"
        )
    try:
        push_branch(worktree, checked_out_branch_name)
    except HostRemoteError as exc:
        raise RuntimeError(str(exc)) from exc
    return checked_out_branch_name


def slug_of_branch(branch: str) -> str:
    intent = branch.split("/", 1)[0]
    return _LEADING_INTENT_PREFIX_RE.sub("", intent)
