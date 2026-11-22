"""What the authoring turn leaves behind: its action provenance, its action record and its bounded output.

``append_action_record`` and ``prepare_agent_output_block`` are patch seams, read through the package facade at call
time.
"""

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


class AgentOutputSteps:
    """The `ExecutionRun` steps that follow the authoring turn: provenance, the agent action record and the output.

    Relies on these `ExecutionRun` attributes: ``intent_data``, ``repo_dir``, ``agent_result``, ``args``,
    ``ledger_dir``, ``executions_ledger``, ``exec_id`` and ``exec_status``; sets ``action_origin``,
    ``review_severity``, ``review_iteration``, ``review_finding_ids``, ``agent_output_text``,
    ``agent_output_truncated`` and ``agent_output_bytes``.
    """

    def _resolve_action_provenance(self) -> None:
        """Resolve the action origin and review provenance: environment, then intent row, else HEAD's trailers."""
        intent_data = self.intent_data
        self.action_origin = (
            os.getenv("HOLON_ACTION_ORIGIN")
            or (intent_data.get("action_origin") or intent_data.get("origin") if intent_data else None)
            or "initial_implementation"
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

        if self.action_origin == "initial_implementation":
            extracted_prov = extract_action_provenance_from_commit(repo_dir=self.repo_dir, commit_ref="HEAD")
            if extracted_prov.get("action_origin") != "initial_implementation":
                self.action_origin = extracted_prov["action_origin"]
                self.review_severity = extracted_prov.get("review_severity") or self.review_severity
                self.review_iteration = (
                    extracted_prov.get("review_iteration") if self.review_iteration is None else self.review_iteration
                )
                self.review_finding_ids = extracted_prov.get("review_finding_ids") or self.review_finding_ids

    def _record_agent_action(self) -> None:
        """Append the ``command_execution`` action record of the agent run, when action logging is on."""
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
                    error_context=(redact_then_truncate(res.stderr, 500) if res.returncode != 0 else None),
                    action_origin=self.action_origin,
                    review_severity=self.review_severity,
                    review_iteration=self.review_iteration,
                    review_finding_ids=self.review_finding_ids,
                ),
            )

    def _capture_agent_output(self) -> None:
        """Keep the implementation turn's combined output, redacted and byte-bounded; a failure keeps nothing."""
        res = self.agent_result
        try:
            stdout = res.stdout or ""
            stderr = res.stderr or ""
            if stdout and stderr:
                combined = f"{stdout}\n{stderr}" if not stdout.endswith("\n") else f"{stdout}{stderr}"
            else:
                combined = stdout or stderr

            max_bytes = get_agent_log_byte_budget()
            # Sweep, bound on a line boundary, redact, then refit: see prepare_agent_output_block
            # for why that order and that final refit are both required.
            self.agent_output_text, self.agent_output_truncated, _dropped = executor_facade.prepare_agent_output_block(
                combined, max_bytes
            )
            self.agent_output_bytes = len(self.agent_output_text.encode("utf-8"))
        except Exception as e:
            print(f"Warning: Failed to capture agent output: {redact_agent_secrets(str(e))}", file=sys.stderr)
            self.agent_output_text = ""
            self.agent_output_truncated = False
            self.agent_output_bytes = 0
