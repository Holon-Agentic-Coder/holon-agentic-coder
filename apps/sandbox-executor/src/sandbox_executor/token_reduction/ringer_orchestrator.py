"""Ringer multi-agent framework: Architect/Executor subagent delegation."""

import logging
from typing import Any

from sandbox_executor.agent_defaults import DEFAULT_MODELS

logger = logging.getLogger(__name__)


class SubtaskResult:
    """Encapsulates the execution outcome of a delegated subtask."""

    def __init__(self, task_id: str, success: bool, raw_output: str, summary: str | None = None):
        self.task_id = task_id
        self.success = success
        self.raw_output = raw_output
        self.summary = summary if summary is not None else self._summarize(raw_output)

    def _summarize(self, raw_output: str) -> str:
        """Compresses raw execution output to a high-level summary."""
        lines = raw_output.splitlines()
        if len(lines) <= 5:
            return raw_output.strip()

        status_str = "SUCCESS" if self.success else "FAILURE"
        first_lines = "\n".join(lines[:3])
        last_lines = "\n".join(lines[-3:])
        return (
            f"[Subtask {self.task_id} {status_str} | Total lines: {len(lines)}]\n"
            f"Start: {first_lines}\n...\nEnd: {last_lines}"
        )


class RingerOrchestrator:
    """Orchestrates execution between high-capability Architect models and fast, low-cost Executor subagents."""

    def __init__(
        self,
        architect_model: str = "claude-3-5-sonnet",
        executor_model: str = DEFAULT_MODELS["antigravity"],
    ):
        self.architect_model = architect_model
        self.executor_model = executor_model
        self.subtasks: list[dict[str, Any]] = []
        self.results: list[SubtaskResult] = []

    def plan_subtask(self, task_id: str, description: str, commands: list[str]) -> dict[str, Any]:
        """Registers a subtask to be assigned to an Executor model."""
        subtask = {
            "task_id": task_id,
            "description": description,
            "commands": commands,
            "assigned_model": self.executor_model,
        }
        self.subtasks.append(subtask)
        logger.info(
            "Subtask '%s' registered for Executor model '%s'",
            task_id,
            self.executor_model,
        )
        return subtask

    def record_execution_outcome(self, task_id: str, success: bool, raw_output: str) -> SubtaskResult:
        """Records the raw execution output from an Executor subagent and generates a compressed summary."""
        res = SubtaskResult(task_id=task_id, success=success, raw_output=raw_output)
        self.results.append(res)
        logger.info("Recorded outcome for subtask '%s' (success=%s)", task_id, success)
        return res

    def build_architect_summary(self) -> str:
        """Generates a compressed summary of all subtask outcomes to feed back to the Architect model."""
        if not self.results:
            return "No subtasks executed yet."

        lines = ["### 🤖 Ringer Executor Subtask Results Summary\n"]
        for res in self.results:
            icon = "✅" if res.success else "❌"
            lines.append(f"#### {icon} Subtask `{res.task_id}`")
            lines.append(res.summary)
            lines.append("")

        return "\n".join(lines)
