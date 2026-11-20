"""Stage 3 plan refresh: a flow admitted after an overlap wait builds on the change it waited for.

A plan branch is cut from the target branch before the overlapping change merged, so an execution started from it is
written against files that no longer exist on the target and its pull request conflicts with exactly that change. When
the checkpoint's admission object records a wait (``waited_since``) and no refresh yet (``refreshed_to``), Stage 3
merges ``origin/<target>`` into the plan branch on origin before the execution branch is cut and the executor is
dispatched (:func:`plan_refresh_stage_failure`). The rules:

- merge only, through :func:`flow.github.merge.merge_branch_detailed` in a temporary worktree: never a rebase, never a
  force-push, never a write to the target branch;
- conflicts confined to ``holon-knowledge/ledger/*.jsonl`` are interleaved by ``created_at`` (:mod:`ledger_interleave`);
- any other conflict aborts the merge and stops Stage 3 before dispatch naming the files; the admission object keeps
  ``waited_since`` without ``refreshed_to``, so a resume retries the refresh once the operator merged by hand;
- a refresh (a merge, or none needed) is recorded once: a ``plan_refresh`` row on the plan branch's
  ``executions.jsonl``, then ``refreshed_to`` in the checkpoint's admission object;
- a flow admitted on its first check has no ``waited_since`` and is never touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow import admission_state
from sandbox_executor.flow.admission_state import AdmissionRecord
from sandbox_executor.flow.checkpoint import save_admission
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.git_refs import _append_ledger_row, _read_ledger_rows, fast_forward_local_branch
from sandbox_executor.flow.github.merge import merge_branch_detailed
from sandbox_executor.git import run_git
from sandbox_executor.plan_paths import REFRESH_MERGE_PREFIX

#: ``entry_type`` and ``kind`` of the executions.jsonl row a refresh appends to the plan branch.
PLAN_REFRESH_KIND = "plan_refresh"


@dataclass(frozen=True)
class PlanRefresh:
    """Outcome of :func:`refresh_plan_branch`: the target commit merged in and the merge commit, or why it failed."""

    refreshed_to: str | None = None
    merge_commit: str | None = None
    conflicts: tuple[str, ...] = ()
    error: str | None = None


def _target_branch(context: FlowContext) -> str:
    return str(context.intent_data.get("target_branch") or "main")


def _plan_id(plan_branch: str) -> str:
    return plan_branch.removesuffix("/_").rsplit("/", 1)[-1]


def refresh_due(context: FlowContext) -> bool:
    """Whether this flow was admitted after a wait and its plan branch is not yet refreshed onto the target."""
    record = AdmissionRecord.from_dict(context.admission)
    return record is not None and record.refresh_due


def refresh_plan_branch(context: FlowContext) -> PlanRefresh:
    """Merge ``origin/<target>`` into the plan branch on origin and fast-forward the local plan branch.

    ``refreshed_to`` is the target commit the merge brought in (or the one already contained); it must be an ancestor
    of the plan tip on origin, else the refresh fails closed.
    """
    repo_dir, plan_branch, target = context.target_worktree, str(context.plan_branch), _target_branch(context)
    message = f"{REFRESH_MERGE_PREFIX}{_plan_id(plan_branch)} onto origin/{target} after the admission wait"
    merged = merge_branch_detailed(repo_dir, target, plan_branch, message)
    if merged.target_sha is None:
        return PlanRefresh(conflicts=merged.conflicts, error=merged.reason or "the merge failed")
    tip, refreshed_to = merged.target_sha, merged.source_sha
    ancestry = run_git(["merge-base", "--is-ancestor", str(refreshed_to), tip], cwd=repo_dir, check=False)
    if not refreshed_to or ancestry.returncode != 0:
        return PlanRefresh(error=f"origin/{target} at {refreshed_to} is not contained in the plan tip {tip[:12]}")
    fast_forward_local_branch(context, plan_branch, tip)
    return PlanRefresh(refreshed_to=refreshed_to, merge_commit=merged.merge_commit)


def refresh_already_recorded(repo_dir: str, plan_branch: str, refreshed_to: str) -> bool:
    """True when the plan branch's ``executions.jsonl`` on origin already holds this plan's row for ``refreshed_to``."""
    return any(
        row.get("kind") == PLAN_REFRESH_KIND
        and row.get("plan_branch") == plan_branch
        and row.get("refreshed_to") == refreshed_to
        for row in _read_ledger_rows(repo_dir, f"refs/remotes/origin/{plan_branch}", "executions.jsonl")
    )


def _refresh_row(context: FlowContext, refresh: PlanRefresh, waited_since: str | None) -> dict[str, Any]:
    plan_branch = str(context.plan_branch)
    return {
        "entry_type": PLAN_REFRESH_KIND,
        "kind": PLAN_REFRESH_KIND,
        "plan_id": _plan_id(plan_branch),
        "plan_branch": plan_branch,
        "intent_id": context.intent_branch or context.intent_data.get("branch"),
        "target_branch": _target_branch(context),
        "refreshed_to": refresh.refreshed_to,
        "merge_commit": refresh.merge_commit,
        "waited_since": waited_since,
        "recorded_by": "flow",
        "created_at": admission_state.utc_now(),
    }


def record_plan_refresh(context: FlowContext, refresh: PlanRefresh) -> str | None:
    """Append the ``plan_refresh`` row (once per refreshed commit), then save ``refreshed_to``; the error on failure.

    The row is written first: if the checkpoint save then fails, a resume re-runs a no-op merge and finds the row.
    """
    record = AdmissionRecord.from_dict(context.admission)
    if record is None or not refresh.refreshed_to:
        return "the checkpoint carries no admitted record to update"
    plan_branch = str(context.plan_branch)
    if not refresh_already_recorded(context.target_worktree, plan_branch, refresh.refreshed_to):
        message = f"execute: record the refresh of {_plan_id(plan_branch)} onto {refresh.refreshed_to[:12]}"
        row = _refresh_row(context, refresh, record.waited_since)
        if (error := _append_ledger_row(context, plan_branch, "executions.jsonl", row, message)) is not None:
            return f"the plan_refresh row could not be recorded: {error}"
    return save_admission(context, record.with_refresh(refresh.refreshed_to).to_dict())


def refresh_payload(context: FlowContext) -> dict[str, Any]:
    """``{"plan_refreshed_to": sha}`` for the Stage 3 payload when the plan branch was refreshed, else empty."""
    record = AdmissionRecord.from_dict(context.admission)
    return {"plan_refreshed_to": record.refreshed_to} if record is not None and record.refreshed_to else {}


def _refresh_failure(context: FlowContext, start_time: str, refresh: PlanRefresh) -> StageResult:
    plan_branch, target = context.plan_branch, _target_branch(context)
    if refresh.conflicts:
        message = (
            f"Stage 3 stopped before dispatch: merging origin/{target} into '{plan_branch}' after the admission wait "
            f"conflicts in {', '.join(refresh.conflicts)}; the merge was aborted and nothing was pushed. Merge "
            f"origin/{target} into the plan branch by hand (merge, do not rebase) and resume the flow."
        )
    else:
        message = (
            f"Stage 3 stopped before dispatch: refreshing '{plan_branch}' onto origin/{target} after the admission "
            f"wait failed: {refresh.error}. Nothing was dispatched; resume the flow to retry."
        )
    conflicts = list(refresh.conflicts)
    return _stage_failure(
        context, FlowStage.EXECUTE, start_time, message, dispatched=False, plan_refresh_conflicts=conflicts
    )


def plan_refresh_stage_failure(context: FlowContext, start_time: str) -> StageResult | None:
    """Refresh a waited flow's plan branch before Stage 3 dispatches: ``None`` to go on, or the failed result.

    Nothing happens for a flow admitted on its first check or already refreshed. A dry run only logs the refresh a
    waiting or admitted-after-waiting flow would get.
    """
    record = AdmissionRecord.from_dict(context.admission)
    target = _target_branch(context)
    if context.dry_run:
        if record is not None and (record.blocked or record.refresh_due):
            context.log(f"Stage 3 (dry run): the plan branch would be refreshed onto origin/{target}.")
        return None
    if record is None or not record.refresh_due:
        return None
    refresh = refresh_plan_branch(context)
    if refresh.error is None and (record_error := record_plan_refresh(context, refresh)) is not None:
        refresh = PlanRefresh(error=record_error)
    if refresh.error is not None:
        return _refresh_failure(context, start_time, refresh)
    sha = str(refresh.refreshed_to)[:12]
    context.log(f"Stage 3: refreshed '{context.plan_branch}' onto origin/{target} {sha} after the admission wait")
    return None
