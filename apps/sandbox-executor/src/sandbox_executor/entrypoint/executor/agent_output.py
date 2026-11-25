import os
import sys

import sandbox_executor.entrypoint.executor as executor_facade
from sandbox_executor.entrypoint.agent_output_block import get_agent_log_byte_budget
from sandbox_executor.entrypoint.execution_args import _parse_review_finding_ids, _parse_review_iteration
from sandbox_executor.entrypoint.executor.commands import is_action_logging_enabled
from sandbox_executor.ledger import (
    ActionRecord,
    extract_action_provenance_from_commit,
    generate_action_id,
    suppress_ledger_failures,
)
from sandbox_executor.redaction import redact_agent_secrets, redact_then_truncate

_DEFAULT_ACTION_ORIGIN = "initial_implementation"
_ERROR_CONTEXT_MAX_CHARS = 500


def _combine_output_streams(stdout: str, stderr: str) -> str:
    if stdout and stderr:
        return f"{stdout}\n{stderr}" if not stdout.endswith("\n") else f"{stdout}{stderr}"
    return stdout or stderr


class AgentOutputSteps:
    def _resolve_action_provenance(self) -> None:
        intent_data = self.intent_data
        self.action_origin = (
            os.getenv("HOLON_ACTION_ORIGIN")
            or (intent_data.get("action_origin") or intent_data.get("origin") if intent_data else None)
            or _DEFAULT_ACTION_ORIGIN
        )
        self.review_severity = os.getenv("HOLON_REVIEW_SEVERITY") or (
            intent_data.get("review_severity") if intent_data else None
        )
        self.review_iteration = _parse_review_iteration(
            os.getenv("HOLON_REVIEW_ITERATION") or (intent_data.get("review_iteration") if intent_data else None)
        )
        self.review_finding_ids = _parse_review_finding_ids(
            os.getenv("HOLON_REVIEW_FINDING_IDS") or (intent_data.get("review_finding_ids") if intent_data else None)
        )

        if self.action_origin == _DEFAULT_ACTION_ORIGIN:
            extracted_prov = extract_action_provenance_from_commit(repo_dir=self.repo_dir, commit_ref="HEAD")
            if extracted_prov.get("action_origin") != _DEFAULT_ACTION_ORIGIN:
                self.action_origin = extracted_prov["action_origin"]
                self.review_severity = extracted_prov.get("review_severity") or self.review_severity
                self.review_iteration = (
                    extracted_prov.get("review_iteration") if self.review_iteration is None else self.review_iteration
                )
                self.review_finding_ids = extracted_prov.get("review_finding_ids") or self.review_finding_ids

    def _record_agent_action(self) -> None:
        if not is_action_logging_enabled(self.intent_data):
            return
        res = self.agent_result
        agent_name, model_name = self.args.agent_name, self.args.model_name
        with suppress_ledger_failures():
            os.makedirs(self.ledger_dir, exist_ok=True)
            executor_facade.append_action_record(
                self.executions_ledger,
                ActionRecord(
                    action_id=generate_action_id(),
                    execution_id=self.exec_id,
                    event_type="command_execution",
                    action=f"Executed agent {agent_name} with model {model_name}",
                    category="code",
                    labels=["agent", agent_name, "execution"],
                    target=f"{agent_name}:{model_name}",
                    outcome="success" if self.exec_status == "success" else "failure",
                    error_context=(
                        redact_then_truncate(res.stderr, _ERROR_CONTEXT_MAX_CHARS) if res.returncode != 0 else None
                    ),
                    action_origin=self.action_origin,
                    review_severity=self.review_severity,
                    review_iteration=self.review_iteration,
                    review_finding_ids=self.review_finding_ids,
                ),
            )

    def _capture_agent_output(self) -> None:
        res = self.agent_result
        try:
            combined = _combine_output_streams(res.stdout or "", res.stderr or "")
            max_bytes = get_agent_log_byte_budget()
            self.agent_output_text, self.agent_output_truncated, _dropped = executor_facade.prepare_agent_output_block(
                combined, max_bytes
            )
            self.agent_output_bytes = len(self.agent_output_text.encode("utf-8"))
        except Exception as e:
            print(f"Warning: Failed to capture agent output: {redact_agent_secrets(str(e))}", file=sys.stderr)
            self.agent_output_text = ""
            self.agent_output_truncated = False
            self.agent_output_bytes = 0
