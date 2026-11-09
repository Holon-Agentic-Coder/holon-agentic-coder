"""Default-branch worktree guard for the direct entry points that write to the worktree they run in.

``holon review`` (with its ``review-loop`` and ``pr-review-loop`` aliases) and ``holon calibrate`` check out branches
and commit review or calibration records in their target worktree, so they refuse a worktree that has the
repository's default branch checked out. ``holon flow`` redirects such a target instead (see ``_flow_worktree``).
"""

from __future__ import annotations

import os
import sys

from sandbox_executor.git_flow import default_branch_worktree_problem, is_working_tree, repository_default_branch

DEFAULT_BRANCH_GUARD_HELP: str = (
    "Target git worktree directory path (default: current directory). A worktree that has the repository's default "
    "branch checked out (normally main) is refused; run it in the flow-<slug> worktree of the run instead."
)


def default_branch_worktree_allowed(target_worktree: str | None, command: str) -> bool:
    """Return whether command may run in target_worktree, printing an actionable error when it may not.

    A path that is not a working tree is allowed here, so the command's own error handling reports it. A detached
    HEAD is allowed.

    Args:
        target_worktree: The command's --target-worktree value (None or '' mean the current directory).
        command: The command name shown in the error, e.g. 'holon review-loop'.

    Returns:
        bool: False after printing 'Error: ...' to stderr when the worktree has the default branch checked out,
            True otherwise.
    """
    path = os.path.abspath(target_worktree or ".")
    if not is_working_tree(path):
        return True
    problem = default_branch_worktree_problem(path, repository_default_branch(path), command=command)
    if problem is None:
        return True
    print(f"Error: {problem}", file=sys.stderr)
    return False
