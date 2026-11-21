"""The plan's decomposition decision: execute it, or record that it was decomposed into sub-intents or refused.

A decomposed or over-budget plan skips the agent and gets one ``executions.jsonl`` summary row (and a
``plan_decomposition`` action record). The ledger writers are patch seams, read through the package facade at call
time.
"""

import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

import sandbox_executor.entrypoint.executor as executor_facade
from sandbox_executor.decomposition_rule import (
    DECISION_DECOMPOSED,
    DECISION_EXECUTE,
    DecompositionDecision,
    InvalidPlanDecisionError,
    should_decompose,
)
from sandbox_executor.entrypoint.executor.commands import is_action_logging_enabled
from sandbox_executor.ledger import ActionRecord, generate_action_id, suppress_ledger_failures


def _recorded_decision(
    plan_data: dict[str, Any], plan_content: str, intent_data: dict[str, Any] | None
) -> DecompositionDecision:
    """Return the decomposition decision the plan row records.

    Only a row that predates the record (an older planner image) is decided here, by the same rule: in a direct
    'holon execute', or in a flow run whose back-fill write failed. A corrupt record exits 1 before any row is written;
    the flow fails such a plan before dispatch.
    """
    try:
        recorded = DecompositionDecision.from_plan_row(plan_data)
    except InvalidPlanDecisionError as exc:
        print(
            f"Error: the plan row of {plan_data.get('plan_id')} records an invalid decomposition decision: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)
    return recorded if recorded is not None else should_decompose(plan_data, plan_content, intent_data)


class SubIntentSteps:
    """The `ExecutionRun` step that records the plan's decomposition decision.

    Relies on these `ExecutionRun` attributes: ``repo_dir``, ``plan_data``, ``plan_content``, ``intent_data``,
    ``exec_id``, ``plan_branch``, ``args``, ``runner``, ``repo``, ``decision`` and ``executions_ledger``; sets
    ``timestamp_str``, ``ledger_dir``, ``_decision``, ``exec_entry`` and ``commit_msg``.
    """

    def record_decision(self) -> bool:
        """Decide whether the plan is executed; returns True when the agent is skipped.

        A decomposed or over-budget plan gets one ``executions.jsonl`` summary row (and a ``plan_decomposition`` action
        record), staged for the commit. A corrupt recorded decision exits 1 before any row is written.
        """
        self.timestamp_str = datetime.now(UTC).isoformat()
        self.ledger_dir = os.path.join(self.repo_dir, "holon-knowledge/ledger")
        os.makedirs(self.ledger_dir, exist_ok=True)

        self._decision = _recorded_decision(self.plan_data, self.plan_content, self.intent_data)
        # Both non-execute outcomes record one summary row, commit it (the host pushes it) and skip the agent.
        if self.decision.decision == DECISION_EXECUTE:
            return False
        self._record_non_execute_row(self.decision)
        return True

    def _record_non_execute_row(self, decision: DecompositionDecision) -> None:
        """Append the decomposed or over-budget summary row and its action record, and stage the ledger."""
        self.exec_entry = {
            "entry_type": "summary",
            "execution_id": self.exec_id,
            "plan_branch": self.plan_branch,
            "agent": self.args.agent_name,
            "agent_version": self.runner.get_version(),
            "model": self.args.model_name,
            "status": decision.decision,
            "entropy": decision.entropy,
            "entropy_budget": decision.entropy_budget,
            "created_at": self.timestamp_str,
        }
        action_text = self._describe_non_execute_decision(decision)
        with executor_facade.atomic_ledger_append(self.executions_ledger) as ef:
            ef.write(json.dumps(self.exec_entry) + "\n")

        if is_action_logging_enabled(self.intent_data):
            with suppress_ledger_failures():
                executor_facade.append_action_record(
                    self.executions_ledger,
                    ActionRecord(
                        action_id=generate_action_id(),
                        execution_id=self.exec_id,
                        event_type="plan_decomposition",
                        action=action_text,
                        category="config",
                        labels=["decomposition", "workflow"],
                        target=self.plan_branch,
                        outcome="success" if decision.decision == DECISION_DECOMPOSED else "failure",
                        action_origin="initial_implementation",
                    ),
                )

        self.repo.add("holon-knowledge/ledger/executions.jsonl")

    def _describe_non_execute_decision(self, decision: DecompositionDecision) -> str:
        """Fill the row's summary (and decomposition reference) and the commit message; returns the action text."""
        plan_branch = self.plan_branch
        if decision.decision == DECISION_DECOMPOSED:
            count = len(decision.sub_intents)
            print(f"The plan records an explicit sub-intent list of {count} item(s); referring to it.")
            # The plan row is the only record of the list; this summary refers to it and never copies it.
            self.exec_entry["plan_decision"] = {
                "plan_id": self.plan_data.get("plan_id"),
                "plan_branch": plan_branch,
                "decision": DECISION_DECOMPOSED,
                "sub_intent_count": count,
            }
            self.exec_entry["summary"] = (
                f"The plan {plan_branch} records {count} sub-intent(s); the flow runs each one through the full "
                "pipeline."
            )
            self.commit_msg = f"execute: the plan {plan_branch} records {count} sub-intent(s)"
            return f"The plan {plan_branch} records {count} sub-intent(s)"
        self.exec_entry["summary"] = (
            f"Plan entropy {decision.entropy} exceeds the entropy budget {decision.entropy_budget} and the "
            "plan lists no sub-intent list; the plan was not executed."
        )
        print(self.exec_entry["summary"])
        self.commit_msg = f"execute: plan {plan_branch} is over its entropy budget"
        return f"Refused over-budget plan {plan_branch}"
