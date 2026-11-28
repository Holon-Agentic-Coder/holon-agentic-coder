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
from sandbox_executor.ledger.fragments import ledger_commit_paths

_LEDGER_DIR_REL = "holon-knowledge/ledger"
_DEFAULT_ACTION_ORIGIN = "initial_implementation"


def _recorded_decision(
    plan_data: dict[str, Any], plan_content: str, intent_data: dict[str, Any] | None
) -> DecompositionDecision:
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
    def record_decision(self) -> bool:
        self.timestamp_str = datetime.now(UTC).isoformat()
        self.ledger_dir = os.path.join(self.repo_dir, _LEDGER_DIR_REL)
        os.makedirs(self.ledger_dir, exist_ok=True)

        self._decision = _recorded_decision(self.plan_data, self.plan_content, self.intent_data)
        if self.decision.decision == DECISION_EXECUTE:
            return False
        self._record_non_execute_row(self.decision)
        return True

    def _record_non_execute_row(self, decision: DecompositionDecision) -> None:
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
                        action_origin=_DEFAULT_ACTION_ORIGIN,
                    ),
                )

        self.repo.add(ledger_commit_paths(self.repo_dir, "executions.jsonl"))

    def _describe_non_execute_decision(self, decision: DecompositionDecision) -> str:
        plan_branch = self.plan_branch
        if decision.decision == DECISION_DECOMPOSED:
            count = len(decision.sub_intents)
            print(f"The plan records an explicit sub-intent list of {count} item(s); referring to it.")
            # why: the plan row is the only record of the sub-intent list; this summary refers to it, never copies it
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
