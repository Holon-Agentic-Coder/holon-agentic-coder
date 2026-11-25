from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from sandbox_executor.git_flow import default_branch_worktree_problem

if TYPE_CHECKING:
    from sandbox_executor.flow import FlowContext

SKIP_PUSH_DISPATCH_ERROR: str = (
    "holon flow dispatches containerised role agents that fetch from origin, reviews the origin execution branch and "
    "pushes the calibrated branch to origin; --skip-push is only valid with --dry-run."
)


def pre_run_guard_error(
    context: FlowContext,
    worktree_validator: Callable[[str], str | None] | None = None,
) -> str | None:
    if context.skip_push and not context.dry_run:
        return SKIP_PUSH_DISPATCH_ERROR
    if worktree_validator is None:
        return None
    base_branch = context.intent_data.get("target_branch") if isinstance(context.intent_data, dict) else None
    return worktree_validator(context.target_worktree) or default_branch_worktree_problem(
        context.target_worktree, base_branch
    )
