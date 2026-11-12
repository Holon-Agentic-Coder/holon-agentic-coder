"""Host-side fetch of the branches the review-loop container reads, so the container never needs ssh."""

from __future__ import annotations

import re

from sandbox_executor import git_flow
from sandbox_executor.cli.commands.execute import target_branch
from sandbox_executor.flow.git_refs import _has_origin_remote
from sandbox_executor.ref_names import INTENT_SUFFIX

# The flow's execution branch shape: '<intent>/P-<plan>/E-<execution>/_' (never any head that merely contains '/E-').
_EXECUTION_BRANCH_RE = re.compile(r"^(I-[^/]+/P-[^/]+)/E-[^/]+/_$")


def _plan_branch(execution_branch: str) -> str | None:
    """Derive '<intent>/P-.../_' from '<intent>/P-.../E-.../_', or None for a non-execution branch."""
    match = _EXECUTION_BRANCH_RE.match(execution_branch)
    return match.group(1) + INTENT_SUFFIX if match else None


def fetch_review_branches(worktree: str, head_branch: str, base_branch: str | None) -> None:
    """Fetch the plan, execution and base branches from origin on the host before the container is dispatched.

    The base is the pull request's base when it has one, else the target branch of the intent recorded on the fetched
    plan branch. A head that is not a flow execution branch and has no pull request base falls back to 'main', not the
    repository default branch. Without an 'origin' remote there is nothing to fetch, so local-only flows keep working.

    Raises:
        RuntimeError: If a fetch fails (the redacted git error is included).
    """
    if not _has_origin_remote(worktree):
        return
    plan_branch = _plan_branch(head_branch)
    fetched: set[str] = set()
    for branch in (plan_branch, head_branch):
        if branch and branch not in fetched:
            git_flow.fetch_origin_branch(worktree, branch)
            fetched.add(branch)
    base = base_branch or (target_branch(worktree, plan_branch) if plan_branch else "main")
    if base not in fetched:
        git_flow.fetch_origin_branch(worktree, base)
