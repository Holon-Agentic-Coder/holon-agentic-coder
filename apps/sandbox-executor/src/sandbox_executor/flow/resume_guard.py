from __future__ import annotations

import os

from sandbox_executor.flow.base_files import base_branch_name
from sandbox_executor.flow.checkpoint_discovery import _checkpoint_names, get_default_checkpoint_path
from sandbox_executor.flow.context import FlowContext, FlowStage
from sandbox_executor.flow.target_sync_merge import merge_in_progress
from sandbox_executor.flow.target_sync_result import SyncBoundary, SyncRequest
from sandbox_executor.flow.target_sync_resume import adopt_merge_in_progress
from sandbox_executor.git import run_git

REQUIRED_BRANCH: dict[FlowStage, str] = {
    FlowStage.PLAN: "intent_branch",
    FlowStage.EXECUTE: "plan_branch",
    FlowStage.REVIEW: "execution_branch",
    FlowStage.CALIBRATE: "execution_branch",
}

_STAGE_ORDER = (FlowStage.INTENT, FlowStage.PLAN, FlowStage.EXECUTE, FlowStage.REVIEW, FlowStage.CALIBRATE)


def _first_running_stage(context: FlowContext, start_stage: FlowStage) -> FlowStage | None:
    skipped = {FlowStage.CALIBRATE: context.skip_calibrate}
    for stage in _STAGE_ORDER[_STAGE_ORDER.index(start_stage) :]:
        if not skipped.get(stage):
            return stage
    return None


def _resume_sync_request(context: FlowContext, start_stage: FlowStage) -> SyncRequest | None:
    worktree = context.target_worktree
    res = run_git(["symbolic-ref", "-q", "--short", "HEAD"], cwd=worktree, check=False)
    branch = (res.stdout or "").strip() if res.returncode == 0 and isinstance(res.stdout, str) else ""
    if not branch:
        return None
    segments = [part for part in (context.execution_branch or "").split("/") if part != "_"]
    segments = segments or [part for part in branch.split("/") if part != "_"] or [branch]
    execution_id = next((part for part in reversed(segments) if part.startswith("E-")), segments[-1])
    ledger_path = os.path.join(worktree, "holon-knowledge", "ledger", "executions.jsonl")
    target = base_branch_name(context)
    return SyncRequest(worktree, branch, target, start_stage.value, SyncBoundary.FLOW_START, ledger_path, execution_id)


def resume_merge_error(context: FlowContext, start_stage: FlowStage | None) -> str | None:
    worktree = context.target_worktree
    if start_stage is None or not merge_in_progress(worktree):
        return None
    if context.dry_run:
        context.log(f"Dry run: a merge is in progress in {worktree}; a real resume adopts it.")
        return None
    request = _resume_sync_request(context, start_stage)
    adoption = adopt_merge_in_progress(request) if request is not None else None
    if not merge_in_progress(worktree):
        return None
    detail = adoption.error if adoption is not None else "no branch is checked out"
    return f"Cannot resume at stage '{start_stage}': the merge in progress in {worktree} was not adopted: {detail}."


def _resume_context_error(
    context: FlowContext, start_stage: FlowStage | None, checkpoint_path: str | None = None
) -> str | None:
    if start_stage is None:
        return None
    # why: the branch check runs first, so a resume it refuses never adopts (merges, records and pushes) a merge.
    return _missing_branch_error(context, start_stage, checkpoint_path) or resume_merge_error(context, start_stage)


def _missing_branch_error(context: FlowContext, start_stage: FlowStage, checkpoint_path: str | None) -> str | None:
    stage = _first_running_stage(context, start_stage)
    field = REQUIRED_BRANCH.get(stage) if stage is not None else None
    if field is None or getattr(context, field, None):
        return None
    target = checkpoint_path or get_default_checkpoint_path(
        context.target_worktree, _checkpoint_names(context.intent_data)[1]
    )
    # why: without an existing checkpoint nothing can be lost, and the stage reports the missing branch itself.
    if not os.path.isfile(target):
        return None
    return (
        f"Cannot resume at stage '{stage}': the flow context has no {field}, so the run would overwrite the existing "
        f"checkpoint {target}. Pass --checkpoint with a checkpoint that records it, or run from an earlier stage. The "
        "checkpoint was left unchanged."
    )
