from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sandbox_executor.flow.checkpoint import save_checkpoint
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.git_refs import (
    _git_show_text,
    _list_child_branches,
    _matches_dispatch_suffix,
    _read_ledger_rows,
)
from sandbox_executor.flow.host_remote import (
    new_local_child_branches,
    publish_new_child_branches,
    push_branch,
    remote_error,
)
from sandbox_executor.plan_paths import is_row_of_plan, recorded_plan_file, split_plan_branch

AMBIGUOUS_PLAN_BRANCHES_HINT = (
    "resume with `--from-stage plan`: Stage 2 then adopts the newest complete plan branch, "
    "or delete the branches that should not be used"
)


def _plan_timestamp(branch: str) -> int:
    _, plan_id = split_plan_branch(branch)
    parts = plan_id.split("-")
    if len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    return 0


@dataclass(frozen=True)
class PlanCandidate:
    branch: str
    ref: str
    published: bool
    missing: str = ""

    @property
    def complete(self) -> bool:
        return not self.missing

    def sort_key(self) -> tuple[int, str]:
        return (_plan_timestamp(self.branch), self.branch)


def _missing_reason(repo_dir: str, branch: str, ref: str) -> str:
    rows = [r for r in _read_ledger_rows(repo_dir, ref, "plans.jsonl") if is_row_of_plan(r, branch)]
    if not rows:
        return "no plans.jsonl row"
    plan_file = recorded_plan_file(rows[-1].get("plan_file"), branch)
    if _git_show_text(repo_dir, ref, plan_file) is None:
        return "plan file not found"
    return ""


def plan_candidates(repo_dir: str, intent_prefix: str) -> list[PlanCandidate]:
    origin_branches = _list_child_branches(repo_dir, intent_prefix, "P")
    local_branches = set(new_local_child_branches(repo_dir, intent_prefix, "P", set()))
    candidates: list[PlanCandidate] = []
    for branch in origin_branches | local_branches:
        published = branch in origin_branches
        ref = f"refs/remotes/origin/{branch}" if published else f"refs/heads/{branch}"
        missing = _missing_reason(repo_dir, branch, ref)
        candidates.append(PlanCandidate(branch=branch, ref=ref, published=published, missing=missing))
    return sorted(candidates, key=PlanCandidate.sort_key)


def _log_adopted_choice(context: FlowContext, chosen: PlanCandidate) -> None:
    recorded = context.plan_branch
    if recorded == chosen.branch:
        checkpoint_note = f"matches checkpoint plan_branch '{recorded}'"
    elif recorded:
        checkpoint_note = f"differs from checkpoint plan_branch '{recorded}'"
    else:
        checkpoint_note = "checkpoint recorded no plan_branch"
    context.log(
        f"Stage 2: adopting existing plan branch '{chosen.branch}' (newest complete plan branch; "
        f"{checkpoint_note}). To plan afresh, delete the plan branch locally and on origin."
    )


def adopt_existing_plan(context: FlowContext, intent_prefix: str) -> tuple[str, str | None]:
    if str(context.intent_data.get("replan_feedback") or "").strip():
        return "", None
    repo_dir = context.target_worktree
    candidates = plan_candidates(repo_dir, intent_prefix)
    if candidates:
        names = ", ".join(c.branch for c in candidates)
        context.log(f"Stage 2: existing plan branch candidates under '{intent_prefix}': {names}")
    for candidate in candidates:
        if not candidate.complete:
            context.log(f"Stage 2: incomplete plan branch '{candidate.branch}' ignored ({candidate.missing}).")
    complete = [c for c in candidates if c.complete]
    if not complete:
        context.log(f"Stage 2: no complete existing plan branch under '{intent_prefix}'; planning as usual.")
        return "", None
    chosen = complete[-1]
    if not chosen.published:
        push_err = remote_error(push_branch, repo_dir, chosen.branch)
        if push_err is not None:
            return "", push_err
    _log_adopted_choice(context, chosen)
    return chosen.branch, None


def record_plan_branch(context: FlowContext, plan_branch: str) -> str | None:
    prev_branch = context.plan_branch
    has_plan_result = FlowStage.PLAN.value in context.stage_results
    prev_plan_result = context.stage_results.get(FlowStage.PLAN.value)
    has_feedback = "replan_feedback" in context.intent_data
    prev_feedback = context.intent_data.get("replan_feedback")
    try:
        context.plan_branch = plan_branch
        context.stage_results[FlowStage.PLAN.value] = StageResult(stage=FlowStage.PLAN, status=StageStatus.RUNNING)
        context.intent_data.pop("replan_feedback", None)
        try:
            save_checkpoint(context, context.checkpoint_path)
        except OSError as exc:
            target = context.checkpoint_path or context.target_worktree
            return f"could not record plan branch '{plan_branch}' in checkpoint '{target}': {exc}"
    finally:
        context.plan_branch = prev_branch
        if has_plan_result and prev_plan_result is not None:
            context.stage_results[FlowStage.PLAN.value] = prev_plan_result
        else:
            context.stage_results.pop(FlowStage.PLAN.value, None)
        if has_feedback:
            context.intent_data["replan_feedback"] = prev_feedback
        else:
            context.intent_data.pop("replan_feedback", None)
    return None


def publish_planned_branches(
    context: FlowContext,
    intent_prefix: str,
    before: set[str],
    suffix: str,
    run_remote: Callable[..., str | None] = remote_error,
) -> str | None:
    repo_dir = context.target_worktree
    created = [
        b for b in new_local_child_branches(repo_dir, intent_prefix, "P", before) if _matches_dispatch_suffix(b, suffix)
    ]
    if len(created) == 1:
        record_error = record_plan_branch(context, created[0])
        if record_error is not None:
            return record_error
    return run_remote(publish_new_child_branches, repo_dir, intent_prefix, "P", before)
