from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.decomposition_rule import DecompositionDecision, InvalidPlanDecisionError, should_decompose
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.git_refs import _append_ledger_row, _git_show_text, _read_ledger_rows, _resolve_branch_ref
from sandbox_executor.flow.stages.plan import _format_entropy
from sandbox_executor.flow.stages.plan_support import plan_id_of
from sandbox_executor.plan_paths import is_row_of_plan, recorded_plan_file


@dataclass(frozen=True)
class PlanBudget:
    context: FlowContext
    plan_branch: str
    plan_ref: str

    @property
    def plan_id(self) -> str:
        return plan_id_of(self.plan_branch)

    @classmethod
    def locate(cls, context: FlowContext) -> PlanBudget | None:
        if not context.plan_branch:
            return None
        plan_ref = _resolve_branch_ref(context.target_worktree, context.plan_branch)
        if not plan_ref:
            return None
        return cls(context, context.plan_branch, plan_ref)

    def latest_row(self) -> dict[str, Any] | None:
        rows = [
            r
            for r in _read_ledger_rows(self.context.target_worktree, self.plan_ref, "plans.jsonl")
            if is_row_of_plan(r, self.plan_branch)
        ]
        return rows[-1] if rows else None

    def decision(self) -> DecompositionDecision | None:
        plan_row = self.latest_row()
        if plan_row is None:
            return None
        try:
            recorded = DecompositionDecision.from_plan_row(plan_row)
        except InvalidPlanDecisionError as exc:
            raise InvalidPlanDecisionError(
                f"Execute stage failed: the plan row of {self.plan_id} records an invalid decomposition decision: {exc}"
            ) from exc
        if recorded is not None:
            return recorded
        plan_md_rel = recorded_plan_file(plan_row.get("plan_file"), self.plan_branch)
        plan_content = _git_show_text(self.context.target_worktree, self.plan_ref, plan_md_rel) or ""
        decision = should_decompose(plan_row, plan_content, self.context.intent_data)
        self.backfill(plan_row, decision)
        return decision

    def backfill(self, plan_row: dict[str, Any], decision: DecompositionDecision) -> str | None:
        return _backfill_plan_row(self.context, plan_row, decision)


def _plan_budget_decision(context: FlowContext) -> DecompositionDecision | None:
    budget = PlanBudget.locate(context)
    return budget.decision() if budget is not None else None


# why: the back-fill stays a module-level function because tests patch it here.
def _backfill_plan_row(context: FlowContext, plan_row: dict[str, Any], decision: DecompositionDecision) -> str | None:
    try:
        prior_revision = int(plan_row.get("ledger_revision") or 1)
    except (TypeError, ValueError):
        prior_revision = 1
    row = {
        **plan_row,
        **decision.to_plan_fields(),
        "ledger_revision": prior_revision + 1,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "decision_recorded_by": "flow",
    }
    plan_branch = str(context.plan_branch)
    commit_msg = f"plan: record the decomposition decision of {row.get('plan_id')}"
    error = _append_ledger_row(context, plan_branch, "plans.jsonl", row, commit_msg)
    # why: a failed write is only logged; the decision is still the one rule applied to the same inputs.
    if error is not None:
        context.log(f"Warning: could not record the decomposition decision on '{plan_branch}': {error}")
        return error
    context.log(f"Recorded the decomposition decision '{decision.decision}' on '{plan_branch}'.")
    return None


def _over_budget_failure(
    context: FlowContext, start_time: str, entropy: Any, entropy_budget: Any, *, dispatched: bool, **payload: Any
) -> StageResult:
    payload.update(
        {"over_budget": True, "entropy": entropy, "entropy_budget": entropy_budget, "dispatched": dispatched}
    )
    return _stage_failure(
        context,
        FlowStage.EXECUTE,
        start_time,
        f"Execute stage failed: plan entropy {_format_entropy(entropy)} exceeds the entropy budget "
        f"{_format_entropy(entropy_budget)} and the plan lists no sub-intent list.",
        **payload,
    )
