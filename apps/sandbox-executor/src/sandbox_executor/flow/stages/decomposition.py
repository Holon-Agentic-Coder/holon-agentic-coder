"""Stage 3 decomposition: validate a recorded sub-intent list and hand it to the child loop.

The child loop itself lives in :mod:`sandbox_executor.flow.stages.children`, the final integration in
:mod:`sandbox_executor.flow.stages.integration` and the flow summary lines in
:mod:`sandbox_executor.flow.stages.decomposition_summary`; ``_run_decomposed_plan`` keeps the signature Stage 3 calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sandbox_executor.budgets import MAX_SUB_INTENT_DEPTH
from sandbox_executor.flow.child_progress import PROGRESS_KEY, _child_intent_branch, _sub_intent_depth, initial_progress
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.stages.children import run_children
from sandbox_executor.ref_names import FitResult, RefTooLongError


@dataclass(frozen=True)
class PlanDecomposition:
    """A recorded sub-intent list of one plan: its items, the plan branch its children nest under, and their limits."""

    context: FlowContext
    items: list[dict[str, Any]]
    plan_branch: str

    @classmethod
    def from_summary_row(cls, context: FlowContext, summary_row: dict[str, Any]) -> PlanDecomposition:
        """The dict items of the row's ``sub_intents``; the plan branch from the context, else from the row."""
        raw_items = summary_row.get("sub_intents")
        items = [i for i in raw_items if isinstance(i, dict)] if isinstance(raw_items, list) else []
        return cls(context, items, str(context.plan_branch or summary_row.get("plan_branch") or ""))

    def depth(self) -> int:
        """The sub-intent depth this intent's children would have."""
        return _sub_intent_depth(self.context.intent_data) + 1

    def depth_error(self) -> str | None:
        """The Stage 3 failure message when the children would exceed ``MAX_SUB_INTENT_DEPTH``, else None."""
        depth = self.depth()
        if depth <= MAX_SUB_INTENT_DEPTH:
            return None
        return (
            f"Execute stage failed: the plan lists a sub-intent list at depth {depth}, deeper than the limit of "
            f"{MAX_SUB_INTENT_DEPTH}; refusing to recurse further."
        )

    def child_branch(self, index: int, slug: str) -> FitResult:
        """The fitted bare intent branch of the ``index``-th (1-based) child under the plan branch."""
        return _child_intent_branch(self.plan_branch, index, slug)

    def validation_error(self, payload: dict[str, Any]) -> str | None:
        """The first reason the list cannot run (no items, too deep, no plan branch), or None.

        Past the items check, ``payload`` records the plan branch (None when unknown), as each failure reports it.
        """
        if not self.items:
            return "Execute stage failed: the plan row records a decomposed decision without any sub-intent list items."
        payload["plan_branch"] = self.plan_branch or None
        depth_error = self.depth_error()
        if depth_error is not None:
            return depth_error
        if not self.plan_branch:
            return "Execute stage failed: no plan branch is known, so the sub-intents cannot be nested under it."
        return None

    def start_payload(self, payload: dict[str, Any], summary_row: dict[str, Any]) -> None:
        """Add the parent target branch, the entropy budget and the initial child progress to ``payload``.

        Raises:
            RefTooLongError: A child's intent branch cannot be named within the ref length limit.
        """
        payload["parent_target_branch"] = str(self.context.intent_data.get("target_branch") or "main")
        payload["entropy_budget"] = summary_row.get("entropy_budget")
        payload[PROGRESS_KEY] = initial_progress(self.items, self.plan_branch)


def _sub_intent_depth_error(context: FlowContext) -> str | None:
    """Return the Stage 3 failure message when this intent's children would exceed ``MAX_SUB_INTENT_DEPTH``."""
    return PlanDecomposition(context, [], "").depth_error()


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
    decomposition = PlanDecomposition.from_summary_row(context, summary_row)
    payload: dict[str, Any] = {**base_payload, "decomposed": True, "sub_intents": decomposition.items, "children": []}
    error = decomposition.validation_error(payload)
    if error is None:
        try:
            decomposition.start_payload(payload, summary_row)
        except RefTooLongError as e:
            error = f"Execute stage failed: {e}"
    if error is not None:
        return _stage_failure(context, FlowStage.EXECUTE, start_time, error, **payload)
    count, plan_branch = len(decomposition.items), decomposition.plan_branch
    context.log(f"Stage 3: running {count} sub-intent(s) one after another under '{plan_branch}'.")
    return run_children(context, start_time, payload)
