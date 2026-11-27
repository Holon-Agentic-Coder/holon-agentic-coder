from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow import admission_state
from sandbox_executor.flow.admission_state import AdmissionRecord
from sandbox_executor.flow.checkpoint import save_admission
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.git_refs import _append_ledger_row, _read_ledger_rows, fast_forward_local_branch
from sandbox_executor.flow.github.merge import _fetched_tips, merge_branch_detailed
from sandbox_executor.flow.target_sync_branch import _can_sync_worktree
from sandbox_executor.git import run_git
from sandbox_executor.plan_paths import REFRESH_MERGE_PREFIX

PLAN_REFRESH_KIND = "plan_refresh"


@dataclass(frozen=True)
class PlanRefresh:
    refreshed_to: str | None = None
    merge_commit: str | None = None
    conflicts: tuple[str, ...] = ()
    error: str | None = None


def _target_branch(context: FlowContext) -> str:
    return str(context.intent_data.get("target_branch") or "main")


def _plan_id(plan_branch: str) -> str:
    return plan_branch.removesuffix("/_").rsplit("/", 1)[-1]


def refresh_due(context: FlowContext) -> bool:
    record = AdmissionRecord.from_dict(context.admission)
    return record is not None and record.refresh_due(str(context.plan_branch))


def _plan_behind_target(repo_dir: str, target: str, plan_branch: str) -> tuple[bool, str | None]:
    if not plan_branch or not _can_sync_worktree(repo_dir, target):
        return False, None
    tips, fetch_error = _fetched_tips(repo_dir, target, plan_branch)
    if fetch_error:
        return False, fetch_error
    plan_sha, target_sha = tips
    ancestry = run_git(["merge-base", "--is-ancestor", target_sha, plan_sha], cwd=repo_dir, check=False)
    if ancestry.returncode == 0:
        return False, None
    if ancestry.returncode == 1:
        return True, None
    detail = (ancestry.stderr or "").strip() if isinstance(ancestry.stderr, str) else ""
    return False, detail or f"git merge-base --is-ancestor exited {ancestry.returncode}"


def refresh_plan_branch(context: FlowContext) -> PlanRefresh:
    repo_dir, plan_branch, target = context.target_worktree, str(context.plan_branch), _target_branch(context)
    message = f"{REFRESH_MERGE_PREFIX}{_plan_id(plan_branch)} onto origin/{target}"
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
    if not refresh.refreshed_to:
        return "the plan refresh did not record a target commit"
    record = AdmissionRecord.from_dict(context.admission)
    plan_branch = str(context.plan_branch)
    waited_since = record.waited_since if record is not None else None
    # why: the row goes first, so a failed checkpoint save resumes into a no-op merge that finds the row.
    if not refresh_already_recorded(context.target_worktree, plan_branch, refresh.refreshed_to):
        message = f"execute: record the refresh of {_plan_id(plan_branch)} onto {refresh.refreshed_to[:12]}"
        row = _refresh_row(context, refresh, waited_since)
        if (error := _append_ledger_row(context, plan_branch, "executions.jsonl", row, message)) is not None:
            return f"the plan_refresh row could not be recorded: {error}"
    if record is None:
        return None
    return save_admission(context, record.with_refresh(refresh.refreshed_to, plan_branch).to_dict())


def refresh_payload(context: FlowContext) -> dict[str, Any]:
    record = AdmissionRecord.from_dict(context.admission)
    refreshed_to = record.refreshed_on(str(context.plan_branch)) if record is not None else None
    return {"plan_refreshed_to": refreshed_to} if refreshed_to else {}


def _refresh_failure(context: FlowContext, start_time: str, refresh: PlanRefresh) -> StageResult:
    plan_branch, target = context.plan_branch, _target_branch(context)
    if refresh.conflicts:
        message = (
            f"Stage 3 stopped before dispatch: merging origin/{target} into '{plan_branch}' "
            f"conflicts in {', '.join(refresh.conflicts)}; the merge was aborted and nothing was pushed. Merge "
            f"origin/{target} into the plan branch by hand (merge, do not rebase) and resume the flow."
        )
    else:
        message = (
            f"Stage 3 stopped before dispatch: refreshing '{plan_branch}' onto origin/{target} "
            f"failed: {refresh.error}. Nothing was dispatched; resume the flow to retry."
        )
    conflicts = list(refresh.conflicts)
    return _stage_failure(
        context, FlowStage.EXECUTE, start_time, message, dispatched=False, plan_refresh_conflicts=conflicts
    )


def _dry_run_would_refresh(record: AdmissionRecord | None, due: bool, behind: bool) -> bool:
    return behind or (record is not None and (record.blocked or due))


def plan_refresh_stage_failure(context: FlowContext, start_time: str) -> StageResult | None:
    record = AdmissionRecord.from_dict(context.admission)
    target, due = _target_branch(context), refresh_due(context)
    behind, probe_error = _plan_behind_target(context.target_worktree, target, str(context.plan_branch or ""))
    if context.dry_run:
        if _dry_run_would_refresh(record, due, behind):
            context.log(f"Stage 3 (dry run): the plan branch would be refreshed onto origin/{target}.")
        return None
    if probe_error is not None:
        return _refresh_failure(context, start_time, PlanRefresh(error=probe_error))
    if not due and not behind:
        return None
    refresh = refresh_plan_branch(context)
    if refresh.error is None and (record_error := record_plan_refresh(context, refresh)) is not None:
        refresh = PlanRefresh(error=record_error)
    if refresh.error is not None:
        return _refresh_failure(context, start_time, refresh)
    sha = str(refresh.refreshed_to)[:12]
    context.log(f"Stage 3: refreshed '{context.plan_branch}' onto origin/{target} {sha}")
    return None
