"""Host-side fetch of the branches the review-loop container reads, so the container never needs ssh."""

from __future__ import annotations

from sandbox_executor import git_flow
from sandbox_executor.cli.commands.execute import _target_branch
from sandbox_executor.git import run_git


def _has_origin_remote(worktree: str) -> bool:
    return run_git(["remote", "get-url", "origin"], cwd=worktree, check=False).returncode == 0


def _plan_branch(execution_branch: str) -> str | None:
    """Derive '<intent>/P-.../_' from '<intent>/P-.../E-.../_', or None for a non-execution branch."""
    if "/E-" not in execution_branch:
        return None
    return execution_branch.split("/E-", 1)[0] + "/_"


def fetch_review_branches(worktree: str, head_branch: str, base_branch: str | None) -> None:
    """Fetch the plan, execution and base branches from origin on the host before the container is dispatched.

    The base is the pull request's base when it has one, else the target branch of the intent recorded on the
    fetched plan branch. Without an 'origin' remote there is nothing to fetch, so local-only flows keep working.

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
    base = base_branch or (_target_branch(worktree, plan_branch) if plan_branch else "main")
    if base not in fetched:
        git_flow.fetch_origin_branch(worktree, base)
