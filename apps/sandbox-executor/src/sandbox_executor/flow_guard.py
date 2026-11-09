"""Pre-run guards that stop a Holon Flow pipeline before any stage runs or a checkpoint is written."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

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
    """Return why a flow run must not start, or None.

    --skip-push is only valid with --dry-run, whichever stage the run starts or resumes from. When
    ``worktree_validator`` is given (``run_flow_pipeline`` passes
    :func:`sandbox_executor.git_flow.target_worktree_problem`), ``context.target_worktree`` must also pass it, so
    Stages 4 and 5 never meet a target that is not an attached git working tree. Direct ``PipelineEngine`` use leaves
    it unset and keeps arbitrary ``target_worktree`` values.
    """
    if context.skip_push and not context.dry_run:
        return SKIP_PUSH_DISPATCH_ERROR
    if worktree_validator is not None:
        return worktree_validator(context.target_worktree)
    return None
