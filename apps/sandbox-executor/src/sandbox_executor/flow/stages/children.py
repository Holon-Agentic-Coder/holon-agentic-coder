from __future__ import annotations

import copy
import os
import re
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow.checkpoint import load_checkpoint, save_checkpoint
from sandbox_executor.flow.child_progress import (
    FAILED,
    IN_PROGRESS,
    MERGED,
    PROGRESS_KEY,
    REVIEW_DONE,
    _sub_intent_depth,
    checkpoint_exists,
    child_checkpoint_path,
    next_child_action,
    reconcile_with_origin,
)
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.git_refs import _fetch_refspecs_error
from sandbox_executor.flow.github.merge import merge_child_pr
from sandbox_executor.flow.stage_status import REVIEW_BOUNDARY_STATUSES
from sandbox_executor.flow.stages.integration import integrate_children
from sandbox_executor.flow.target_sync_hooks import at_turn_boundary, consume_target_merge_notice
from sandbox_executor.intent_assumption_rules import default_assumptions_fields
from sandbox_executor.redaction import redact_agent_secrets

_PR_URL_RE = re.compile(r"https?://\S+/pull/\d+")


def _child_review_pr(results: dict[str, StageResult]) -> tuple[Any, Any]:
    review = results.get(FlowStage.REVIEW.value)
    pr = review.payload.get("pr") if review is not None else None
    if not isinstance(pr, dict):
        return None, None
    pr_url = pr.get("pr_url")
    if not pr_url:
        match = _PR_URL_RE.search(str(pr.get("output") or ""))
        pr_url = match.group(0) if match else None
    return pr_url, pr.get("pr_number")


def _child_context(context: FlowContext, intent_data: dict[str, Any]) -> FlowContext:
    child = FlowContext(
        repo_dir=context.target_worktree,
        intent_data=intent_data,
        agent=context.agent,
        model=context.model,
        dry_run=context.dry_run,
        auto_calibrate=context.auto_calibrate,
        token_reduce=context.token_reduce,
        proxy_attach=context.proxy_attach,
        skip_push=context.skip_push,
        skip_calibrate=context.skip_calibrate,
        max_review_iterations=context.max_review_iterations,
        review_ensemble_size=context.review_ensemble_size,
        mitm_web=context.mitm_web,
        allow_overlap=context.allow_overlap,
        dispatcher=context.dispatcher,
    )
    if notice := consume_target_merge_notice(context):
        child._pending_target_merge_notice = notice
    return child


def _build_child_intent(context: FlowContext, payload: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("sub_intents") or []
    index = int(entry["index"])
    item = items[index - 1] if 0 < index <= len(items) and isinstance(items[index - 1], dict) else {}
    slug = str(entry["slug"])
    plan_branch = str(payload["plan_branch"])
    child_data: dict[str, Any] = {
        "branch": str(entry["intent_branch"]).removesuffix("/_"),
        "slug": slug,
        "description": str(item.get("description") or item.get("goal") or slug),
        "goal": str(item.get("goal") or item.get("description") or slug),
        "parent_intent_id": plan_branch,
        "target_branch": plan_branch,
        "sub_intent_depth": _sub_intent_depth(context.intent_data) + 1,
    }
    if payload.get("entropy_budget") is not None:
        child_data["entropy_budget"] = payload["entropy_budget"]
    labels = context.intent_data.get("labels")
    if isinstance(labels, list):
        child_data["labels"] = list(labels)
    if entry.get("original_slug"):
        child_data["original_slug"] = entry["original_slug"]
    for field_name, default_value in default_assumptions_fields().items():
        if field_name in item and item[field_name] is not None:
            child_data[field_name] = copy.deepcopy(item[field_name])
        elif field_name in context.intent_data and context.intent_data[field_name] is not None:
            child_data[field_name] = copy.deepcopy(context.intent_data[field_name])
        else:
            child_data[field_name] = copy.deepcopy(default_value)
    return child_data


def _save_progress(context: FlowContext, start_time: str, payload: dict[str, Any]) -> None:
    context.stage_results[FlowStage.EXECUTE.value] = StageResult(
        stage=FlowStage.EXECUTE, status=StageStatus.RUNNING, start_time=start_time, payload=payload
    )
    save_checkpoint(context)


def _set_child_summary(payload: dict[str, Any], summary: dict[str, Any]) -> None:
    children = payload.setdefault("children", [])
    for position, existing in enumerate(children):
        if isinstance(existing, dict) and existing.get("index") == summary["index"]:
            children[position] = {**existing, **summary}
            return
    children.append(summary)


def _child_summary(entry: dict[str, Any], child: FlowContext, results: dict[str, StageResult]) -> dict[str, Any]:
    final_stage = next(reversed(results)) if results else None
    summary: dict[str, Any] = {
        "index": entry["index"],
        "slug": entry["slug"],
        "intent_branch": child.intent_branch or entry["intent_branch"],
        "execution_branch": entry.get("execution_branch"),
        "pr_url": entry.get("pr_url"),
        "pr_number": entry.get("pr_number"),
        "final_stage": final_stage,
        "status": results[final_stage].status.value if final_stage else None,
    }
    child_execute = results.get(FlowStage.EXECUTE.value)
    if child_execute is not None and child_execute.payload.get("decomposed"):
        summary["decomposed"] = True
        summary["plan_branch"] = child_execute.payload.get("plan_branch")
        summary["children"] = list(child_execute.payload.get("children") or [])
    return summary


def _load_child_checkpoint(context: FlowContext, child: FlowContext, entry: dict[str, Any]) -> tuple[Any, str | None]:
    path = child_checkpoint_path(context, str(entry["intent_branch"]))
    if not os.path.exists(path):
        # why: re-running a child with an execution branch or PR would dispatch its executor again
        if entry.get("execution_branch") or entry.get("pr_number"):
            return (
                None,
                f"it has an execution branch or PR on origin but no checkpoint at {path}; refusing to re-run it",
            )
        return None, None
    try:
        checkpoint = load_checkpoint(path)
    except (OSError, ValueError) as exc:
        return None, f"could not be resumed: its checkpoint {path} could not be read: {exc}"
    saved = checkpoint.context
    child.stage_results = dict(saved.stage_results)
    child.intent_branch, child.plan_branch = saved.intent_branch, saved.plan_branch
    child.execution_branch, child.replan_attempts = saved.execution_branch, saved.replan_attempts
    return checkpoint.current_stage, None


def _merge_readiness_error(results: dict[str, StageResult]) -> str | None:
    review = results.get(FlowStage.REVIEW.value)
    calibrate = results.get(FlowStage.CALIBRATE.value)
    consensus = review.payload.get("consensus") if review is not None else None
    if review is None or review.status not in REVIEW_BOUNDARY_STATUSES:
        return f"its Stage 4 did not reach consensus approval (status {review.status.value if review else 'not run'})"
    if not (isinstance(consensus, dict) and consensus.get("approved")):
        return "its Stage 4 did not reach consensus approval"
    if calibrate is None or calibrate.status not in (StageStatus.SUCCESS, StageStatus.SKIPPED):
        return f"its Stage 5 did not complete (status {calibrate.status.value if calibrate else 'not run'})"
    return None


def _run_one_child(context: FlowContext, payload: dict[str, Any], entry: dict[str, Any], resume: bool) -> str | None:
    child = _child_context(context, _build_child_intent(context, payload, entry))
    from_stage, error = _load_child_checkpoint(context, child, entry) if resume else (None, None)
    if error:
        return error
    plan_branch = str(payload["plan_branch"])
    # why: Stage 1 cuts the child from origin's plan branch tip, so the local view of it must be fresh first.
    refspec = f"+refs/heads/{plan_branch}:refs/remotes/origin/{plan_branch}"
    if fetch_error := _fetch_refspecs_error(context.target_worktree, [refspec]):
        return f"could not refresh plan branch '{plan_branch}' before cutting it: {fetch_error}"
    context.log(f"Stage 3: {'resuming' if resume else 'running'} sub-intent {entry['index']} '{entry['slug']}'.")
    try:
        if from_stage == FlowStage.COMPLETED:
            results = child.stage_results
        else:
            # why: pipeline imports this module, so importing it at module load would be circular
            from sandbox_executor.flow.pipeline import PipelineEngine

            results = PipelineEngine(child, from_stage=from_stage).run()
    finally:
        context.logs.extend(f"[sub-intent {entry['slug']}] {line}" for line in child.logs)
    pr_url, pr_number = _child_review_pr(results)
    entry["execution_branch"] = child.execution_branch or entry.get("execution_branch")
    entry["pr_url"], entry["pr_number"] = pr_url or entry.get("pr_url"), pr_number or entry.get("pr_number")
    _set_child_summary(payload, _child_summary(entry, child, results))
    failed = [name for name, res in results.items() if res.status == StageStatus.FAILED]
    if failed or not results:
        cause = (results[failed[0]].error if failed else None) or "no stage ran"
        return f"failed at stage '{failed[0] if failed else 'none'}': {cause}"
    error = _merge_readiness_error(results)
    if error is None and not entry.get("pr_number"):
        error = "its Stage 4 recorded no PR number"
    return f"is not ready to merge into '{plan_branch}': {error}" if error else None


def _merge_ready_child(context: FlowContext, payload: dict[str, Any], entry: dict[str, Any]) -> str | None:
    plan_branch = str(payload["plan_branch"])
    expected_head = str(entry.get("execution_branch") or "")
    result = merge_child_pr(context.target_worktree, entry["pr_number"], plan_branch, expected_head=expected_head)
    if not result.merged:
        # why: stay review_done so a resume retries only this guarded merge, not the child's Stage 4/5.
        entry["merge_error"] = result.reason
        return f"could not be merged into '{plan_branch}' (PR #{entry['pr_number']}): {result.reason}"
    entry["status"], entry["merge_commit"] = MERGED, result.merge_commit
    entry.pop("merge_error", None)
    _set_child_summary(payload, {"index": entry["index"], "merged": True, "merge_commit": result.merge_commit})
    context.log(f"Stage 3: merged sub-intent '{entry['slug']}' (PR #{entry['pr_number']}) into '{plan_branch}'.")
    return None


def _sync_sub_intent_plan_branch(context: FlowContext, payload: dict[str, Any], index: int) -> StageResult | None:
    prev_plan, prev_exec = context.plan_branch, context.execution_branch
    context.plan_branch = str(payload.get("plan_branch") or context.plan_branch or "") or None
    context.execution_branch = None
    try:
        return at_turn_boundary(context, FlowStage.EXECUTE, index)
    finally:
        context.plan_branch, context.execution_branch = prev_plan, prev_exec


def _advance_child(context: FlowContext, start_time: str, payload: dict[str, Any], entry: dict[str, Any]) -> str | None:
    action = next_child_action(entry, checkpoint_exists(context, entry))
    if action == "skip":
        # why: a child promoted to merged by origin reconciliation has no merged summary yet.
        merged = {"index": entry["index"], "merged": True, "merge_commit": entry.get("merge_commit")}
        _set_child_summary(payload, merged)
        return None
    if int(entry["index"]) > 1 and (turn_err := _sync_sub_intent_plan_branch(context, payload, int(entry["index"]))):
        return turn_err.error or "target sync blocked at sub-intent boundary"
    if action in ("dispatch", "resume"):
        entry["status"] = IN_PROGRESS
        _save_progress(context, start_time, payload)
        error = _run_one_child(context, payload, entry, resume=action == "resume")
        if error:
            return error
        entry["status"] = REVIEW_DONE
        _save_progress(context, start_time, payload)
    error = _merge_ready_child(context, payload, entry)
    _save_progress(context, start_time, payload)
    return error


def _drive_children(context: FlowContext, start_time: str, payload: dict[str, Any]) -> StageResult:
    progress = payload[PROGRESS_KEY]
    total = len(progress)
    for entry in progress:
        error = _advance_child(context, start_time, payload, entry)
        if error:
            if entry.get("status") != REVIEW_DONE:
                entry["status"] = FAILED
            _save_progress(context, start_time, payload)
            return _stage_failure(
                context,
                FlowStage.EXECUTE,
                start_time,
                f"Execute stage failed: sub-intent {entry['index']}/{total} '{entry['slug']}' {error}",
                **payload,
            )
    context.log(f"Stage 3: all {total} sub-intent(s) merged into '{payload['plan_branch']}'.")
    return integrate_children(context, start_time, payload)


def run_children(context: FlowContext, start_time: str, payload: dict[str, Any]) -> StageResult:
    try:
        for note in reconcile_with_origin(context.target_worktree, payload[PROGRESS_KEY], str(payload["plan_branch"])):
            context.log(f"Stage 3: {note} (origin wins).")
        return _drive_children(context, start_time, payload)
    # why: an exception reaching the pipeline would store a payload-less failure over the checkpointed progress
    except Exception as exc:
        error = f"Execute stage failed: the sub-intent loop raised {type(exc).__name__}: {exc}"
        try:
            _save_progress(context, start_time, payload)
        except Exception as save_exc:
            # why: a failing save never replaces the original failure; it is reported next to it.
            error += f" (the progress checkpoint could not be saved either: {type(save_exc).__name__}: {save_exc})"
        error = redact_agent_secrets(error)
        return _stage_failure(context, FlowStage.EXECUTE, start_time, error, **payload)


def resume_children(context: FlowContext, previous: StageResult) -> StageResult:
    start_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {**previous.payload, PROGRESS_KEY: [dict(e) for e in previous.payload[PROGRESS_KEY]]}
    payload["children"] = [dict(c) for c in payload.get("children") or [] if isinstance(c, dict)]
    context.log("Stage 3 resume: the plan was already decomposed; continuing its children, not the parent executor.")
    if payload.get("execution_branch"):
        context.execution_branch = str(payload["execution_branch"])
    return run_children(context, start_time, payload)
