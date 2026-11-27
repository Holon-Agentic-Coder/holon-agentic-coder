from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.git_refs import (
    _append_superseding_execution_row,
    _latest_execution_summary,
    _read_ledger_rows,
)
from sandbox_executor.flow.host_remote import new_local_child_branches
from sandbox_executor.git import run_git


def unrecorded_executions(repo_dir: str, plan_prefix: str, before: set[str]) -> list[str]:
    held: list[str] = []
    for branch in new_local_child_branches(repo_dir, plan_prefix, "E", before):
        exec_id = branch.removesuffix("/_").rsplit("/", 1)[-1]
        rows = _read_ledger_rows(repo_dir, f"refs/heads/{branch}", "executions.jsonl")
        if _latest_execution_summary(rows, exec_id, branch) is None:
            held.append(branch)
    return held


def hold_unrecorded_executions(context: FlowContext, plan_prefix: str, before: set[str]) -> set[str]:
    held = unrecorded_executions(context.target_worktree, plan_prefix, before)
    for branch in held:
        context.log(f"Stage 3: execution branch '{branch}' has no ledger row; kept local and not pushed.")
    # why: origin must never carry an execution branch the ledger does not know
    return before | set(held)


def held_execution_failure(context: FlowContext, start_time: str, held: set[str], exit_code: int) -> StageResult | None:
    branch = next(iter(sorted(held)), None)
    if branch is None:
        return None
    exec_id = branch.removesuffix("/_").rsplit("/", 1)[-1]
    error = (
        f"Execute stage failed: the executor agent exited with code {exit_code} but '{branch}' carries no "
        f"executions.jsonl summary row for {exec_id}, so no superseding row was recorded; it was kept local and not "
        "pushed."
    )
    payload = {"execution_id": exec_id, "execution_branch": branch, "status": None, "container_status": None}
    return _stage_failure(
        context, FlowStage.EXECUTE, start_time, error, **payload, exit_code=exit_code, superseding_row_recorded=False
    )


def publish_with_first_row(context: FlowContext, branch: str, build_row: Callable[[], dict[str, Any]]) -> str | None:
    repo_dir = context.target_worktree
    # why: the ledger append commits on the origin ref, so one push creates the branch with its row
    staged = f"refs/remotes/origin/{branch}"
    if run_git(["update-ref", staged, f"refs/heads/{branch}", ""], cwd=repo_dir, check=False).returncode != 0:
        return f"could not stage '{branch}' for its first push"
    error = _append_superseding_execution_row(context, branch, build_row())
    if error is not None:
        run_git(["update-ref", "-d", staged], cwd=repo_dir, check=False)
    return error
