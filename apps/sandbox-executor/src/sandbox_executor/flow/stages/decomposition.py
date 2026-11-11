"""Stage 3 decomposition: validate a recorded sub-intent list, hand it to the child loop and summarise the children.

The child loop itself lives in :mod:`sandbox_executor.flow.stages.children` and the final integration in
:mod:`sandbox_executor.flow.stages.integration`; ``_run_decomposed_plan`` keeps the signature Stage 3 calls.
"""

from __future__ import annotations

from typing import Any

from sandbox_executor.budgets import MAX_SUB_INTENT_DEPTH
from sandbox_executor.flow.child_progress import PROGRESS_KEY, _sub_intent_depth, initial_progress
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.stages.children import run_children
from sandbox_executor.ref_names import RefTooLongError


def _sub_intent_depth_error(context: FlowContext) -> str | None:
    """Return the Stage 3 failure message when this intent's children would exceed ``MAX_SUB_INTENT_DEPTH``."""
    depth = _sub_intent_depth(context.intent_data) + 1
    if depth <= MAX_SUB_INTENT_DEPTH:
        return None
    return (
        f"Execute stage failed: the plan lists a sub-intent list at depth {depth}, deeper than the limit of "
        f"{MAX_SUB_INTENT_DEPTH}; refusing to recurse further."
    )


def _decomposition_child_lines(children: list[Any], parent_branch: str, indent: str) -> list[str]:
    """Summary lines for one level of children; a child that itself decomposed lists its own children below it.

    Each child is shown with its PR (or branch) and whether it was merged into ``parent_branch``. A child that itself
    decomposed has its own combined PR, listed the same way, with its children nested below it.
    """
    lines: list[str] = []
    for index, child in enumerate((c for c in children if isinstance(c, dict)), start=1):
        where = child.get("pr_url") or child.get("execution_branch") or child.get("intent_branch") or "no branch"
        state = f"merged into '{parent_branch}'" if child.get("merged") else str(child.get("status"))
        if not child.get("decomposed"):
            lines.append(f"{indent}{index}. {child.get('slug')}: {where} ({state})")
            continue
        grandchildren = [c for c in child.get("children") or [] if isinstance(c, dict)]
        note = f"decomposed into {len(grandchildren)} sub-intent(s), combined in {where} ({state})"
        lines.append(f"{indent}{index}. {child.get('slug')}: {note}:")
        nested_parent = str(child.get("plan_branch") or "its plan branch")
        lines.extend(_decomposition_child_lines(grandchildren, nested_parent, indent + "   "))
    return lines


def format_decomposition_summary(execute_result: StageResult | None) -> list[str]:
    """Lines for the flow summary of a decomposed Stage 3: each child's PR and its merge into the plan branch.

    Each child PR is merged into the plan branch after its own review consensus and calibration; Stage 3 then
    integrates the plan branch into the parent execution branch, and Stage 4 raises and reviews the combined PR into
    the intent's target branch, so the summary names that next step. When the decomposed Stage 3 failed, the summary
    counts how many children ran and merged and omits the hand-off, so a failed run never reads as success. Returns an
    empty list when Stage 3 was not decomposed.
    """
    if execute_result is None or not execute_result.payload.get("decomposed"):
        return []
    payload = execute_result.payload
    children = [c for c in payload.get("children") or [] if isinstance(c, dict)]
    raw_items = payload.get("sub_intents")
    # Count the items the plan listed, not only the children that ran: a failed run stops at its first failing child,
    # and a depth refusal runs none.
    listed = sum(isinstance(i, dict) for i in raw_items) if isinstance(raw_items, list) else len(children)
    plan_branch = payload.get("plan_branch") or "the plan branch"
    failed = execute_result.status == StageStatus.FAILED
    if failed:
        merged = sum(bool(c.get("merged")) for c in children)
        lines = [
            f"Decomposed into {listed} sub-intent(s); {len(children)} of {listed} ran and {merged} merged into "
            f"'{plan_branch}' before Stage 3 failed:"
        ]
    else:
        lines = [f"Decomposed into {listed} sub-intent(s); each child PR was merged into '{plan_branch}':"]
    lines.extend(_decomposition_child_lines(children, plan_branch, "  "))
    if failed:
        return lines
    target = payload.get("parent_target_branch") or "its target branch"
    source = payload.get("execution_branch") or "the execution branch"
    lines.append(
        f"Combined PR: Stage 4 raises and reviews '{source}' (with '{plan_branch}' merged in) into '{target}'."
    )
    return lines


def _run_decomposed_plan(
    context: FlowContext, start_time: str, summary_row: dict[str, Any], base_payload: dict[str, Any]
) -> StageResult:
    """Drive each item of a recorded sub-intent list through the full pipeline under the plan branch.

    Validates the items and the depth limit, then hands the list to
    :func:`sandbox_executor.flow.stages.children.run_children`: each child is cut from the plan branch tip, targets the
    plan branch (never the intent branch or ``main``), and is merged into it after its consensus and calibration;
    after the last child the plan branch is integrated into this execution branch for the combined PR. The plan
    branch comes from the context, else from the decision record; without one the stage fails rather than fall back
    to the intent branch.
    """
    raw_items = summary_row.get("sub_intents")
    items = [i for i in raw_items if isinstance(i, dict)] if isinstance(raw_items, list) else []
    payload: dict[str, Any] = {**base_payload, "decomposed": True, "sub_intents": items, "children": []}
    if not items:
        return _stage_failure(
            context,
            FlowStage.EXECUTE,
            start_time,
            "Execute stage failed: the plan row records a decomposed decision without any sub-intent list items.",
            **payload,
        )
    plan_branch = str(context.plan_branch or summary_row.get("plan_branch") or "")
    payload["plan_branch"] = plan_branch or None
    depth_error = _sub_intent_depth_error(context)
    if depth_error is not None:
        return _stage_failure(context, FlowStage.EXECUTE, start_time, depth_error, **payload)
    if not plan_branch:
        error = "Execute stage failed: no plan branch is known, so the sub-intents cannot be nested under it."
        return _stage_failure(context, FlowStage.EXECUTE, start_time, error, **payload)

    payload["parent_target_branch"] = str(context.intent_data.get("target_branch") or "main")
    payload["entropy_budget"] = summary_row.get("entropy_budget")
    try:
        payload[PROGRESS_KEY] = initial_progress(items, plan_branch)
    except RefTooLongError as e:
        return _stage_failure(context, FlowStage.EXECUTE, start_time, f"Execute stage failed: {e}", **payload)
    context.log(f"Stage 3: running {len(items)} sub-intent(s) one after another under '{plan_branch}'.")
    return run_children(context, start_time, payload)
