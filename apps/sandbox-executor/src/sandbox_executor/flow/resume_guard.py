"""The pre-run resume guard: a resume that cannot start never overwrites the checkpoint it would save to.

A ``--from-stage`` run whose context lacks the branch its first stage needs would fail in that stage and then save the
sparse context over an existing checkpoint, losing the recorded branches. The engine checks this guard next to
:func:`sandbox_executor.flow_guard.pre_run_guard_error`, before any stage runs, so such a resume records its failure in
memory only and leaves the file byte-for-byte as it was. Without an existing checkpoint there is nothing to lose and
the stage reports the missing branch itself.
"""

from __future__ import annotations

import os

from sandbox_executor.flow.checkpoint_discovery import _checkpoint_names, get_default_checkpoint_path
from sandbox_executor.flow.context import FlowContext, FlowStage

# The branch each stage reads from the flow context and fails without (Stage 1 needs none).
REQUIRED_BRANCH: dict[FlowStage, str] = {
    FlowStage.PLAN: "intent_branch",
    FlowStage.EXECUTE: "plan_branch",
    FlowStage.REVIEW: "execution_branch",
    FlowStage.CALIBRATE: "execution_branch",
}

_STAGE_ORDER = (FlowStage.INTENT, FlowStage.PLAN, FlowStage.EXECUTE, FlowStage.REVIEW, FlowStage.CALIBRATE)


def _first_running_stage(context: FlowContext, start_stage: FlowStage) -> FlowStage | None:
    """The first stage from ``start_stage`` on that is not skipped by --skip-review or --skip-calibrate."""
    skipped = {FlowStage.REVIEW: context.skip_review, FlowStage.CALIBRATE: context.skip_calibrate}
    for stage in _STAGE_ORDER[_STAGE_ORDER.index(start_stage) :]:
        if not skipped.get(stage):
            return stage
    return None


def _resume_context_error(
    context: FlowContext, start_stage: FlowStage | None, checkpoint_path: str | None = None
) -> str | None:
    """Why a run resumed at ``start_stage`` must not start, or ``None`` (always ``None`` for a run from Stage 1).

    The run is refused when its first running stage lacks its branch and the checkpoint it would save to already
    exists: ``checkpoint_path``, else the intent's default checkpoint in the target worktree.
    """
    if start_stage is None:
        return None
    stage = _first_running_stage(context, start_stage)
    field = REQUIRED_BRANCH.get(stage) if stage is not None else None
    if field is None or getattr(context, field, None):
        return None
    target = checkpoint_path or get_default_checkpoint_path(
        context.target_worktree, _checkpoint_names(context.intent_data)[1]
    )
    if not os.path.isfile(target):
        return None
    return (
        f"Cannot resume at stage '{stage}': the flow context has no {field}, so the run would overwrite the existing "
        f"checkpoint {target}. Pass --checkpoint with a checkpoint that records it, or run from an earlier stage. The "
        "checkpoint was left unchanged."
    )
