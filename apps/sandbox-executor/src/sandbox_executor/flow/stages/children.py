"""Stage 3 child loop of a decomposed plan: run each sub-intent under the plan branch, merge it, then integrate.

Children run strictly one after another. Each child is a new intent whose ``target_branch`` and ``parent_intent_id`` are
the plan branch, so Stage 1 cuts it from the plan branch tip on origin, which already holds every earlier child's merge
commit. A child is merged into the plan branch (through the guarded :func:`merge_child_pr`) only after its Stage 4
consensus halt and its Stage 5 calibration; the next child starts only after that merge. Progress is recorded per child
in the Stage 3 payload and checkpointed after every change, so a resume continues where the run stopped and never
dispatches the parent executor or a child that already has work on origin.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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
from sandbox_executor.flow.stages.integration import integrate_children
from sandbox_executor.redaction import redact_agent_secrets

if TYPE_CHECKING:
    # Bound at runtime by the package __init__, because pipeline imports this module.
    from sandbox_executor.flow.pipeline import PipelineEngine


_PR_URL_RE = re.compile(r"https?://\S+/pull/\d+")


def _child_review_pr(results: dict[str, StageResult]) -> tuple[Any, Any]:
    """Return the ``(pr_url, pr_number)`` a child pipeline's Stage 4 recorded, or ``(None, None)``.

    Stage 4 records the ``create_or_update_pr`` result under ``payload["pr"]``: it carries ``pr_number``, and the
    URL only inside the ``gh`` output, so the URL is read from an explicit ``pr_url`` key when present, else from that
    output.
    """
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
    """Build the FlowContext of one child intent, inheriting the parent's runner, flags and dispatcher."""
    return FlowContext(
        repo_dir=context.target_worktree,
        intent_data=intent_data,
        agent=context.agent,
        model=context.model,
        dry_run=context.dry_run,
        auto_calibrate=context.auto_calibrate,
        token_reduce=context.token_reduce,
        skip_push=context.skip_push,
        skip_review=context.skip_review,
        skip_calibrate=context.skip_calibrate,
        max_review_iterations=context.max_review_iterations,
        review_ensemble_size=context.review_ensemble_size,
        mitm_web=context.mitm_web,
        dispatcher=context.dispatcher,
    )


def _build_child_intent(context: FlowContext, payload: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """The intent payload of one child: nested under the plan branch, which is also its merge target."""
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
    return child_data


def _save_progress(context: FlowContext, start_time: str, payload: dict[str, Any]) -> None:
    """Checkpoint the parent with Stage 3 still running, so a crash never loses a child's recorded progress."""
    context.stage_results[FlowStage.EXECUTE.value] = StageResult(
        stage=FlowStage.EXECUTE, status=StageStatus.RUNNING, start_time=start_time, payload=payload
    )
    save_checkpoint(context)


def _set_child_summary(payload: dict[str, Any], summary: dict[str, Any]) -> None:
    """Record a child's summary in ``payload["children"]``, replacing an earlier summary of the same child."""
    children = payload.setdefault("children", [])
    for position, existing in enumerate(children):
        if isinstance(existing, dict) and existing.get("index") == summary["index"]:
            children[position] = {**existing, **summary}
            return
    children.append(summary)


def _child_summary(entry: dict[str, Any], child: FlowContext, results: dict[str, StageResult]) -> dict[str, Any]:
    """Summary of one child run for the flow summary; a child that itself decomposed carries its own children."""
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
    """Load a resumed child's own checkpoint into ``child``; return ``(from_stage, error)``.

    Without a checkpoint a child that has an execution branch or a PR is refused (re-running it would dispatch its
    executor again); a child with at most its intent branch starts from Stage 1, which reuses that branch.
    """
    path = child_checkpoint_path(context, str(entry["intent_branch"]))
    if not os.path.exists(path):
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
    """Why a child's results are not ready to merge: Stage 4 must have halted at consensus, Stage 5 succeeded or
    been skipped by flag."""
    review = results.get(FlowStage.REVIEW.value)
    calibrate = results.get(FlowStage.CALIBRATE.value)
    consensus = review.payload.get("consensus") if review is not None else None
    if review is None or review.status != StageStatus.HALTED_FOR_HUMAN:
        return f"its Stage 4 did not halt at consensus (status {review.status.value if review else 'not run'})"
    if not (isinstance(consensus, dict) and consensus.get("approved")):
        return "its Stage 4 halted without consensus approval"
    if calibrate is None or calibrate.status not in (StageStatus.SUCCESS, StageStatus.SKIPPED):
        return f"its Stage 5 did not complete (status {calibrate.status.value if calibrate else 'not run'})"
    return None


def _run_one_child(context: FlowContext, payload: dict[str, Any], entry: dict[str, Any], resume: bool) -> str | None:
    """Run (or resume) one child pipeline up to its merge boundary; return why it is not ready to merge, or None."""
    child = _child_context(context, _build_child_intent(context, payload, entry))
    from_stage, error = _load_child_checkpoint(context, child, entry) if resume else (None, None)
    if error:
        return error
    plan_branch = str(payload["plan_branch"])
    # Stage 1 cuts the child from origin's plan branch tip; refresh the local view of it first.
    refspec = f"+refs/heads/{plan_branch}:refs/remotes/origin/{plan_branch}"
    if fetch_error := _fetch_refspecs_error(context.target_worktree, [refspec]):
        return f"could not refresh plan branch '{plan_branch}' before cutting it: {fetch_error}"
    context.log(f"Stage 3: {'resuming' if resume else 'running'} sub-intent {entry['index']} '{entry['slug']}'.")
    try:
        if from_stage == FlowStage.COMPLETED:
            results = child.stage_results
        else:
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
    """Merge a child whose review and calibration completed into the plan branch; return a refusal, or None."""
    plan_branch = str(payload["plan_branch"])
    expected_head = str(entry.get("execution_branch") or "")
    result = merge_child_pr(context.target_worktree, entry["pr_number"], plan_branch, expected_head=expected_head)
    if not result.merged:
        # Stay review_done so a resume retries only this guarded merge, not the child's Stage 4/5.
        entry["merge_error"] = result.reason
        return f"could not be merged into '{plan_branch}' (PR #{entry['pr_number']}): {result.reason}"
    entry["status"], entry["merge_commit"] = MERGED, result.merge_commit
    entry.pop("merge_error", None)
    _set_child_summary(payload, {"index": entry["index"], "merged": True, "merge_commit": result.merge_commit})
    context.log(f"Stage 3: merged sub-intent '{entry['slug']}' (PR #{entry['pr_number']}) into '{plan_branch}'.")
    return None


def _advance_child(context: FlowContext, start_time: str, payload: dict[str, Any], entry: dict[str, Any]) -> str | None:
    """Take one child from wherever its progress says it is to merged; return why it stopped, or None."""
    action = next_child_action(entry, checkpoint_exists(context, entry))
    if action == "skip":
        # A child promoted to merged by origin reconciliation has no merged summary yet; record it.
        merged = {"index": entry["index"], "merged": True, "merge_commit": entry.get("merge_commit")}
        _set_child_summary(payload, merged)
        return None
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
    """Drive every child of ``payload["children_progress"]`` to merged, in order, then integrate the plan branch.

    The first child that fails or is not ready to merge is marked ``failed``; one refused only by the merge guard stays
    ``review_done`` with a ``merge_error`` so a resume retries just the merge. The parent checkpoint is saved, and
    Stage 3 fails naming the child; later children do not start. Under ``--skip-review`` no child could reach its
    consensus halt and merge, so the stage fails before any child is dispatched.
    """
    if context.skip_review:
        error = (
            "Execute stage failed: the plan was decomposed, but --skip-review skips each sub-intent's Stage 4, so no "
            "child could reach consensus and be merged into the plan branch; re-run without --skip-review."
        )
        return _stage_failure(context, FlowStage.EXECUTE, start_time, error, **payload)
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
    """Reconcile the progress with origin, then drive the children; an unexpected exception keeps the progress.

    Reconciling first (origin wins) means even a fresh Stage 3 entered without the parent checkpoint
    (``--from-stage execute``) never re-dispatches a child that already has an execution branch or PR. An exception
    that escaped to the pipeline would store a payload-less failure and overwrite the checkpointed
    ``children_progress``, so it is turned into a Stage 3 failure that keeps the entries' last status instead.
    """
    try:
        for note in reconcile_with_origin(context.target_worktree, payload[PROGRESS_KEY], str(payload["plan_branch"])):
            context.log(f"Stage 3: {note} (origin wins).")
        return _drive_children(context, start_time, payload)
    except Exception as exc:
        _save_progress(context, start_time, payload)
        error = redact_agent_secrets(f"Execute stage failed: the sub-intent loop raised {type(exc).__name__}: {exc}")
        return _stage_failure(context, FlowStage.EXECUTE, start_time, error, **payload)


def resume_children(context: FlowContext, previous: StageResult) -> StageResult:
    """Resume a checkpointed decomposed Stage 3 without dispatching the parent executor.

    The recorded progress is reconciled with origin first (origin wins), then the loop continues: merged children are
    skipped, a child whose review finished is merged, any other child with recorded work resumes from its own
    checkpoint, and only a child with nothing recorded anywhere is dispatched.
    """
    start_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {**previous.payload, PROGRESS_KEY: [dict(e) for e in previous.payload[PROGRESS_KEY]]}
    payload["children"] = [dict(c) for c in payload.get("children") or [] if isinstance(c, dict)]
    context.log("Stage 3 resume: the plan was already decomposed; continuing its children, not the parent executor.")
    if payload.get("execution_branch"):
        context.execution_branch = str(payload["execution_branch"])
    return run_children(context, start_time, payload)
