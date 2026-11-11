"""Host-side fetch and push around the standalone 'holon intent' and 'holon plan' role containers.

The containers work in the host worktree and never reach the remote. The command fetches the base branch into the
worktree before dispatch and pushes the branch the container committed afterwards, with the host's own credentials,
exactly as the flow's stages do.
"""

from __future__ import annotations

import os
import re

from sandbox_executor import git_flow
from sandbox_executor.flow.host_remote import HostRemoteError, fetch_base, push_branch
from sandbox_executor.git import run_git


def prepare_worktree(base_branch: str, target_worktree: str | None, slug: str) -> str:
    """Return the host worktree a role container works in, with ``base_branch`` fetched into it.

    A worktree with the default branch checked out only selects the repository: the intent's flow-<slug> sibling is
    provisioned off it (or reused), so the container never clones or fetches to prepare its checkout.

    Raises:
        RuntimeError: If the base branch cannot be fetched or the flow worktree cannot be provisioned.
        ValueError: If a flow worktree is needed and the slug is empty.
    """
    worktree = target_worktree or os.getcwd()
    try:
        # Worktrees share remote-tracking refs, so fetching here also serves a flow worktree provisioned below.
        fetch_base(worktree, base_branch)
    except HostRemoteError as exc:
        raise RuntimeError(str(exc)) from exc
    if git_flow.is_default_branch_worktree(worktree, git_flow.repository_default_branch(worktree)):
        worktree = git_flow.ensure_flow_worktree_off_default(worktree, slug, None)
    return worktree


def push_checked_out_branch(worktree: str) -> str:
    """Push the branch the container left checked out in ``worktree`` (fast-forward only) and return its name.

    Raises:
        RuntimeError: If no branch is checked out, or the push fails (the message names the operation, the branch
            and the remote).
    """
    res = run_git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=worktree, check=False)
    branch = (res.stdout or "").strip() if res.returncode == 0 else ""
    if not branch:
        raise RuntimeError(f"no branch is checked out in '{worktree}' to push")
    try:
        push_branch(worktree, branch)
    except HostRemoteError as exc:
        raise RuntimeError(str(exc)) from exc
    return branch


def slug_of_branch(branch: str) -> str:
    """The slug of an intent, plan or execution branch ('I-<seconds>-<slug>/...'), else the branch's first segment."""
    intent = branch.split("/", 1)[0]
    return re.sub(r"^I-\d+-", "", intent)
