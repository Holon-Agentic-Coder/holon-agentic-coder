from __future__ import annotations

import re

from sandbox_executor import git_flow
from sandbox_executor.cli.commands.execute import target_branch
from sandbox_executor.flow.git_refs import _has_origin_remote
from sandbox_executor.ref_names import INTENT_SUFFIX

# why: match only flow branch '<intent>/P-<plan>/E-<execution>/_', never an arbitrary head merely containing '/E-'
_EXECUTION_BRANCH_RE = re.compile(r"^(I-[^/]+/P-[^/]+)/E-[^/]+/_$")
_FALLBACK_BASE_BRANCH = "main"


def _plan_branch(execution_branch: str) -> str | None:
    match = _EXECUTION_BRANCH_RE.match(execution_branch)
    return match.group(1) + INTENT_SUFFIX if match else None


def _resolve_base_branch(worktree: str, plan_branch: str | None, base_branch: str | None) -> str:
    if base_branch:
        return base_branch
    return target_branch(worktree, plan_branch) if plan_branch else _FALLBACK_BASE_BRANCH


def fetch_review_branches(worktree: str, head_branch: str, base_branch: str | None) -> None:
    if not _has_origin_remote(worktree):
        return
    plan_branch = _plan_branch(head_branch)
    fetched: set[str] = set()
    for branch in (plan_branch, head_branch):
        if branch and branch not in fetched:
            git_flow.fetch_origin_branch(worktree, branch)
            fetched.add(branch)
    base = _resolve_base_branch(worktree, plan_branch, base_branch)
    if base not in fetched:
        git_flow.fetch_origin_branch(worktree, base)
