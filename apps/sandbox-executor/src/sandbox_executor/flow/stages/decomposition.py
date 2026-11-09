"""Stage 3 decomposition: run each sub-intent of an over-budget plan as a child flow and summarise the
children.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sandbox_executor.budgets import MAX_SUB_INTENT_DEPTH
from sandbox_executor.flow.child_progress import _child_intent_branch, _sub_intent_depth
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _stage_failure

if TYPE_CHECKING:
    # Bound at runtime by the package __init__, because pipeline imports this module.
    from sandbox_executor.flow.pipeline import PipelineEngine


def _sub_intent_depth_error(context: FlowContext) -> str | None:
    """Return the Stage 3 failure message when this intent's children would exceed ``MAX_SUB_INTENT_DEPTH``."""
    depth = _sub_intent_depth(context.intent_data) + 1
    if depth <= MAX_SUB_INTENT_DEPTH:
        return None
    return (
        f"Execute stage failed: the plan lists a sub-intent list at depth {depth}, deeper than the limit of "
        f"{MAX_SUB_INTENT_DEPTH}; refusing to recurse further."
    )


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


def _decomposition_child_lines(children: list[Any], parent_branch: str, indent: str, failed: bool) -> list[str]:
    """Summary lines for one level of children; a child that itself decomposed lists its own children below it."""
    lines: list[str] = []
    for index, child in enumerate((c for c in children if isinstance(c, dict)), start=1):
        if not child.get("decomposed"):
            where = child.get("pr_url") or child.get("execution_branch") or child.get("intent_branch") or "no branch"
            lines.append(f"{indent}{index}. {child.get('slug')}: {where} ({child.get('status')})")
            continue
        grandchildren = [c for c in child.get("children") or [] if isinstance(c, dict)]
        branch = child.get("intent_branch") or "its intent branch"
        if failed:
            note = f"decomposed into {len(grandchildren)} sub-intent(s) ({child.get('status')})"
        else:
            note = (
                f"decomposed into {len(grandchildren)} sub-intent(s), so it has no PR of its own; merge its child "
                f"PRs below into '{branch}', then raise a PR from '{branch}' to '{parent_branch}' (not yet automated)"
            )
        lines.append(f"{indent}{index}. {child.get('slug')}: {note}:")
        lines.extend(_decomposition_child_lines(grandchildren, branch, indent + "   ", failed))
    return lines


def format_decomposition_summary(execute_result: StageResult | None) -> list[str]:
    """Lines for the flow summary of a decomposed Stage 3: each child's PR or branch, then the pending parent step.

    Each child pipeline that executes its plan stops at its own Stage 4 merge boundary with a PR into the parent
    intent branch. A child that itself decomposed has no PR of its own: it is listed as decomposed, with its own
    children (whose PRs need merging) nested below it. Aggregating the merged children and raising an intent branch's
    PR to its own target are not yet automated, so the summary names that remaining step explicitly. When the
    decomposed Stage 3 failed, the merge guidance and the pending parent step are omitted so a failed run never reads
    as success. Returns an empty list when Stage 3 was not decomposed.
    """
    if execute_result is None or not execute_result.payload.get("decomposed"):
        return []
    payload = execute_result.payload
    children = [c for c in payload.get("children") or [] if isinstance(c, dict)]
    raw_items = payload.get("sub_intents")
    # Count the items the plan listed, not only the children that ran: a failed run stops at its first failing child,
    # and a depth refusal runs none.
    listed = sum(isinstance(i, dict) for i in raw_items) if isinstance(raw_items, list) else len(children)
    failed = execute_result.status == StageStatus.FAILED
    if failed:
        lines = [
            f"Decomposed into {listed} sub-intent(s); {len(children)} of {listed} ran before Stage 3 failed, so no "
            "child PR is ready to merge:"
        ]
    else:
        lines = [f"Decomposed into {listed} sub-intent(s); merge each child PR into the parent intent branch:"]
    parent_branch = payload.get("parent_branch") or "the parent intent branch"
    lines.extend(_decomposition_child_lines(children, parent_branch, "  ", failed))
    if failed:
        return lines
    parent_target = payload.get("parent_target_branch") or "its target branch"
    lines.append(
        f"Pending (not yet automated): after the child PRs merge, raise a PR from '{parent_branch}' to "
        f"'{parent_target}'."
    )
    return lines


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


def _run_decomposed_plan(
    context: FlowContext, start_time: str, summary_row: dict[str, Any], base_payload: dict[str, Any]
) -> StageResult:
    """Drive each item of a recorded sub-intent list through the full pipeline, strictly one after another.

    Each child is a new intent (created by the normal Stage 1 intent creator) whose ``target_branch`` and
    ``parent_intent_id`` are this intent's branch, so it merges into its immediate parent and never into ``main``. A
    child succeeds when its own pipeline ends without a failed stage (normally at its own Stage 4 consensus halt, the
    merge boundary). The first failing child stops the loop and fails this stage, naming the child.
    """
    raw_items = summary_row.get("sub_intents")
    items = [i for i in raw_items if isinstance(i, dict)] if isinstance(raw_items, list) else []
    payload: dict[str, Any] = {**base_payload, "decomposed": True, "sub_intents": items, "children": []}
    if not items:
        return _stage_failure(
            context,
            FlowStage.EXECUTE,
            start_time,
            "Execute stage failed: the executor recorded a decomposed plan without any sub-intent list items.",
            **payload,
        )
    depth = _sub_intent_depth(context.intent_data) + 1
    depth_error = _sub_intent_depth_error(context)
    if depth_error is not None:
        return _stage_failure(context, FlowStage.EXECUTE, start_time, depth_error, **payload)

    parent_branch = context.intent_branch or str(summary_row.get("plan_branch") or "").split("/P-")[0] + "/_"
    payload["parent_branch"] = parent_branch
    payload["parent_target_branch"] = str(context.intent_data.get("target_branch") or "main")
    entropy_budget = summary_row.get("entropy_budget")
    labels = context.intent_data.get("labels")
    total = len(items)
    for index, item in enumerate(items, start=1):
        slug = str(item.get("slug") or f"sub-intent-{index}")
        child_data: dict[str, Any] = {
            "branch": _child_intent_branch(parent_branch, index, slug),
            "slug": slug,
            "description": str(item.get("description") or item.get("goal") or slug),
            "goal": str(item.get("goal") or item.get("description") or slug),
            "parent_intent_id": parent_branch,
            "target_branch": parent_branch,
            "sub_intent_depth": depth,
        }
        if entropy_budget is not None:
            child_data["entropy_budget"] = entropy_budget
        if isinstance(labels, list):
            child_data["labels"] = list(labels)
        child = _child_context(context, child_data)
        context.log(f"Stage 3: running sub-intent {index}/{total} '{slug}' through the full pipeline.")
        try:
            results = PipelineEngine(child).run()
        finally:
            context.logs.extend(f"[sub-intent {slug}] {line}" for line in child.logs)
        failed = [name for name, res in results.items() if res.status == StageStatus.FAILED]
        final_stage = next(reversed(results)) if results else None
        pr_url, pr_number = _child_review_pr(results)
        child_summary = {
            "slug": slug,
            "intent_branch": child.intent_branch,
            "execution_branch": child.execution_branch,
            "pr_url": pr_url,
            "pr_number": pr_number,
            "final_stage": final_stage,
            "status": results[final_stage].status.value if final_stage else None,
        }
        child_execute = results.get(FlowStage.EXECUTE.value)
        if child_execute is not None and child_execute.payload.get("decomposed"):
            # The child ran its own sub-intents instead of opening a PR: carry them so the summary can list them.
            child_summary["decomposed"] = True
            child_summary["children"] = list(child_execute.payload.get("children") or [])
        payload["children"].append(child_summary)
        if failed or not results:
            error = (results[failed[0]].error if failed else None) or "no stage ran"
            return _stage_failure(
                context,
                FlowStage.EXECUTE,
                start_time,
                f"Execute stage failed: sub-intent {index}/{total} '{slug}' failed at stage "
                f"'{failed[0] if failed else 'none'}': {error}",
                **payload,
            )

    context.log(f"Stage 3 completed: all {total} sub-intent(s) of '{context.plan_branch}' ran through the pipeline.")
    result = StageResult(
        stage=FlowStage.EXECUTE,
        status=StageStatus.SUCCESS,
        start_time=start_time,
        end_time=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        payload=payload,
    )
    context.stage_results["execute"] = result
    return result
