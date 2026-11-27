from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sandbox_executor.budgets import MAX_SUB_INTENT_DEPTH
from sandbox_executor.flow.child_progress import PROGRESS_KEY, _sub_intent_depth, initial_progress
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.stages.children import run_children
from sandbox_executor.ref_names import RefTooLongError


@dataclass(frozen=True)
class PlanDecomposition:
    context: FlowContext
    items: list[dict[str, Any]]
    plan_branch: str

    @classmethod
    def from_summary_row(cls, context: FlowContext, summary_row: dict[str, Any]) -> PlanDecomposition:
        raw_items = summary_row.get("sub_intents")
        items = [i for i in raw_items if isinstance(i, dict)] if isinstance(raw_items, list) else []
        return cls(context, items, str(context.plan_branch or summary_row.get("plan_branch") or ""))

    def depth(self) -> int:
        return _sub_intent_depth(self.context.intent_data) + 1

    def depth_error(self) -> str | None:
        depth = self.depth()
        if depth <= MAX_SUB_INTENT_DEPTH:
            return None
        return (
            f"Execute stage failed: the plan lists a sub-intent list at depth {depth}, deeper than the limit of "
            f"{MAX_SUB_INTENT_DEPTH}; refusing to recurse further."
        )

    def validation_error(self, payload: dict[str, Any]) -> str | None:
        if not self.items:
            return "Execute stage failed: the plan row records a decomposed decision without any sub-intent list items."
        payload["plan_branch"] = self.plan_branch or None
        depth_error = self.depth_error()
        if depth_error is not None:
            return depth_error
        # why: children never fall back to the intent branch when no plan branch is known
        if not self.plan_branch:
            return "Execute stage failed: no plan branch is known, so the sub-intents cannot be nested under it."
        return None

    def start_payload(self, payload: dict[str, Any], summary_row: dict[str, Any]) -> None:
        payload["parent_target_branch"] = str(self.context.intent_data.get("target_branch") or "main")
        payload["entropy_budget"] = summary_row.get("entropy_budget")
        payload[PROGRESS_KEY] = initial_progress(self.items, self.plan_branch)


def _sub_intent_depth_error(context: FlowContext) -> str | None:
    return PlanDecomposition(context, [], "").depth_error()


def _run_decomposed_plan(
    context: FlowContext, start_time: str, summary_row: dict[str, Any], base_payload: dict[str, Any]
) -> StageResult:
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
