from __future__ import annotations

import os
import sys

from sandbox_executor.git_flow import default_branch_worktree_problem, is_working_tree, repository_default_branch

DEFAULT_BRANCH_GUARD_HELP: str = (
    "Target git worktree directory path (default: current directory). A worktree that has the repository's default "
    "branch checked out (normally main) is refused; run it in the flow-<slug> worktree of the run instead."
)


def default_branch_worktree_allowed(target_worktree: str | None, command: str) -> bool:
    path = os.path.abspath(target_worktree or ".")
    if not is_working_tree(path):
        return True
    problem = default_branch_worktree_problem(path, repository_default_branch(path), command=command)
    if problem is None:
        return True
    print(f"Error: {problem}", file=sys.stderr)
    return False
