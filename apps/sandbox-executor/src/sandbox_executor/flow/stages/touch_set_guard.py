from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.budgets import MAX_REPLAN_ATTEMPTS
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import TEST_MEASUREMENT_KEYS, _stage_failure
from sandbox_executor.flow.git_refs import (
    _append_superseding_execution_row,
    _execution_parent_plan_branch,
    _parse_ledger_revision,
    _read_ledger_rows,
    _resolve_branch_ref,
)
from sandbox_executor.flow.in_flight import read_plan_touch_set
from sandbox_executor.git import run_git
from sandbox_executor.module_budget_guard import (
    MODULE_BUDGET_CHANGE_REFUSED,
    budget_config_changes,
    module_budget_change_authorised,
    refusal_message,
)
from sandbox_executor.plan_paths import bare_branch, is_row_of_plan, split_plan_branch
from sandbox_executor.touch_set import TouchSet, out_of_set_files

TOUCH_SET_VIOLATION = "touch_set_violation"
_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def superseding_failure_row(
    summary_row: dict[str, Any], status: str, failure_reason: str, summary: str
) -> dict[str, Any]:
    superseding = {key: value for key, value in summary_row.items() if key not in TEST_MEASUREMENT_KEYS}
    try:
        prior_revision = int(summary_row.get("ledger_revision") or 1)
    except (TypeError, ValueError):
        prior_revision = 1
    superseding.update(
        {
            "status": status,
            "failure_reason": failure_reason,
            "summary": summary,
            "created_at": datetime.now(UTC).isoformat(),
            "ledger_revision": max(2, prior_revision + 1),
        }
    )
    return superseding


def _effective_plan_branch(context: FlowContext) -> str | None:
    if context.plan_branch:
        return str(context.plan_branch)
    if context.execution_branch:
        return _execution_parent_plan_branch(str(context.execution_branch))
    return None


def _is_replan_row_for_plan(row: dict[str, Any], plan_branch: str) -> bool:
    if is_row_of_plan(row, plan_branch):
        return True
    parent, plan_id = split_plan_branch(plan_branch)
    if not plan_id or bare_branch(str(row.get("intent_branch") or "")) != parent:
        return False
    target_bare = bare_branch(plan_branch)
    if bare_branch(str(row.get("plan_branch") or "")) == target_bare:
        return True
    replan_for = str(row.get("replan_for") or "").strip()
    if replan_for and (replan_for == plan_id or bare_branch(replan_for) == target_bare):
        return True
    return str(row.get("supersedes_plan_id") or "").strip() == plan_id


def _latest_touch_set_at_ref(repo_dir: str, ref: str, plan_branch: str) -> TouchSet | None:
    rows = _read_ledger_rows(repo_dir, ref, "plans.jsonl")
    best_rank: tuple[int, int] | None = None
    chosen: TouchSet | None = None
    for index, row in enumerate(rows):
        if not _is_replan_row_for_plan(row, plan_branch):
            continue
        parsed = TouchSet.from_row(row.get("touch_set"))
        if parsed is None:
            continue
        rank = (_parse_ledger_revision(row), index)
        if best_rank is None or rank > best_rank:
            best_rank, chosen = rank, parsed
    return chosen


def _resolve_effective_touch_set(context: FlowContext, *, head_ref: str | None = None) -> TouchSet | None:
    repo_dir = context.target_worktree
    plan_branch = _effective_plan_branch(context)
    if plan_branch and repo_dir and os.path.exists(os.path.join(repo_dir, ".git")):
        preferred_ref = head_ref or (
            _resolve_branch_ref(repo_dir, str(context.execution_branch)) if context.execution_branch else None
        )
        if preferred_ref:
            found = _latest_touch_set_at_ref(repo_dir, preferred_ref, plan_branch)
            if found is not None:
                return found
        found = read_plan_touch_set(repo_dir, plan_branch)
        if found is not None:
            return found
    scope = (context.intent_data or {}).get("scope")
    if isinstance(scope, list) and scope:
        return TouchSet.from_row(scope)
    return None


def touch_set_violation(context: FlowContext, changed: list[str], *, head_ref: str | None = None) -> list[str]:
    declared = _resolve_effective_touch_set(context, head_ref=head_ref)
    return out_of_set_files(declared, changed) if declared is not None else []


def _commit_changed_paths(repo_dir: str, sha: str) -> list[str] | None:
    res = run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha, "--"], cwd=repo_dir, check=False)
    if res.returncode != 0 or not isinstance(res.stdout, str):
        return None
    return list(dict.fromkeys(line for line in res.stdout.splitlines() if line))


def _range_commit_paths(repo_dir: str, base_sha: str, head_sha: str) -> list[tuple[str, list[str]]] | None:
    listed = run_git(
        ["rev-list", "--reverse", "--first-parent", "--no-merges", f"{base_sha}..{head_sha}"],
        cwd=repo_dir,
        check=False,
    )
    if listed.returncode != 0 or not isinstance(listed.stdout, str):
        return None
    commits = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    if commits:
        result: list[tuple[str, list[str]]] = []
        for sha in commits:
            paths = _commit_changed_paths(repo_dir, sha)
            if paths is None:
                return None
            result.append((sha, paths))
        return result
    diff = run_git(["diff", "--name-only", base_sha, head_sha, "--"], cwd=repo_dir, check=False)
    if diff.returncode != 0 or not isinstance(diff.stdout, str):
        return None
    return [(head_sha, list(dict.fromkeys(line for line in diff.stdout.splitlines() if line)))]


def resolver_commit_touch_set_refusal(context: FlowContext, base_sha: str, head_sha: str) -> str | None:
    if not (_OBJECT_ID.match(head_sha) and _OBJECT_ID.match(base_sha)):
        return None
    repo_dir = context.target_worktree
    # why: non-git worktree directories are handled by the push itself rather than failing here.
    if not repo_dir or not os.path.exists(os.path.join(repo_dir, ".git")):
        return None
    commit_paths = _range_commit_paths(repo_dir, base_sha, head_sha)
    if commit_paths is None:
        return f"{TOUCH_SET_VIOLATION}: could not inspect resolver commits since {base_sha[:12]}"
    offending_commits: list[str] = []
    violating_files: list[str] = []
    for sha, paths in commit_paths:
        files = touch_set_violation(context, paths, head_ref=head_sha)
        if files:
            offending_commits.append(sha[:12])
            violating_files.extend(files)
    unique_files = list(dict.fromkeys(violating_files))
    if not unique_files:
        return None
    commits_text = ", ".join(dict.fromkeys(offending_commits))
    return (
        f"{TOUCH_SET_VIOLATION}: resolver commit(s) {commits_text} changed {len(unique_files)} file(s) "
        f"outside the plan's touch set: {', '.join(unique_files)}"
    )


def _record_refusal(
    context: FlowContext, summary_row: dict[str, Any], base_payload: dict[str, Any], reason: str, summary: str
) -> dict[str, Any]:
    exec_branch = str(base_payload["execution_branch"])
    row = superseding_failure_row(summary_row, "failed", reason, summary)
    write_error = _append_superseding_execution_row(context, exec_branch, row)
    if write_error:
        what = reason.replace("_", " ").replace("touch set", "touch-set")
        context.log(f"Warning: could not record the {what} row on '{exec_branch}': {write_error}")
    return {
        **base_payload,
        "status": "failed",
        "container_status": base_payload.get("status"),
        "failure_reason": reason,
        "superseding_row_recorded": write_error is None,
    }


def _budget_refusal_result(
    context: FlowContext, start_time: str, summary_row: dict[str, Any], changed: list[str], base_payload: dict[str, Any]
) -> StageResult | None:
    paths = [] if module_budget_change_authorised(context.intent_data) else budget_config_changes(changed)
    if not paths:
        return None
    summary = refusal_message(paths)
    payload = _record_refusal(context, summary_row, base_payload, MODULE_BUDGET_CHANGE_REFUSED, summary)
    payload[MODULE_BUDGET_CHANGE_REFUSED] = paths
    return _stage_failure(context, FlowStage.EXECUTE, start_time, f"Execute stage failed: {summary}", **payload)


def touch_set_violation_result(
    context: FlowContext, start_time: str, summary_row: dict[str, Any], changed: list[str], base_payload: dict[str, Any]
) -> StageResult | None:
    # why: a budget configuration change is refused without a re-plan; the refusal tells the agent to split the module.
    refused = _budget_refusal_result(context, start_time, summary_row, changed, base_payload)
    if refused is not None:
        return refused
    files = touch_set_violation(context, changed)
    if not files:
        return None
    summary = f"the execution changed {len(files)} file(s) outside the plan's touch set: {', '.join(files)}"
    payload = _record_refusal(context, summary_row, base_payload, TOUCH_SET_VIOLATION, summary)
    payload.update({TOUCH_SET_VIOLATION: files, "replan_requested": True})
    error = f"Execute stage failed: {summary}; the plan returns to Stage 2 to widen its touch set."
    return _stage_failure(context, FlowStage.EXECUTE, start_time, error, **payload)


def touch_set_replan_feedback(plan_branch: str, files: list[str], attempt: int) -> str:
    return (
        f"The execution of the previous plan '{plan_branch}' changed files outside its touch set: {', '.join(files)}. "
        f"Re-plan attempt {attempt} of {MAX_REPLAN_ATTEMPTS}: widen the '## Touch Set' section to include these "
        "files, or decompose the plan."
    )


def touch_set_replan_exhausted(files: list[str], attempts: int) -> str:
    return (
        f"Execute stage failed: the execution changed files outside the plan's touch set ({', '.join(files)}) after "
        f"{attempts} re-plan attempts."
    )
