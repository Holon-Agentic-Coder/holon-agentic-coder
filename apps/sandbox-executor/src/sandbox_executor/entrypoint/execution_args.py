"""The executor command line and the plan, intent and review values an execution reads from the ledger."""

import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Self

from sandbox_executor.agent_defaults import FALLBACK_AGENT, default_model_for
from sandbox_executor.plan_paths import is_row_of_plan


def _read_plan_row(repo_dir: str, plan_branch_prefix: str) -> dict[str, Any] | None:
    """Return the newest plans.jsonl row of the plan, or None; unparseable lines are skipped with a warning."""
    plans_file_path = os.path.join(repo_dir, "holon-knowledge/ledger/plans.jsonl")
    plan_data = None
    if os.path.exists(plans_file_path):
        with open(plans_file_path) as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    # Keyed on the intent and the plan id together; the newest matching row wins, so a
                    # superseding row the flow back-filled is read.
                    if is_row_of_plan(data, plan_branch_prefix):
                        plan_data = data
                except Exception as e:
                    print(f"Warning: skipping line {line_no} in plans.jsonl: {e}", file=sys.stderr)
    return plan_data


def _read_intent_row(repo_dir: str, target_intent_branch: str) -> dict[str, Any] | None:
    """Return the first intents.jsonl row of the intent branch, or None, skipping unparseable lines with a warning."""
    intents_file_path = os.path.join(repo_dir, "holon-knowledge/ledger/intents.jsonl")
    if not os.path.exists(intents_file_path):
        return None
    with open(intents_file_path) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                branch = data.get("branch")
                if isinstance(branch, str) and (target_intent_branch.rstrip("/_") == branch.rstrip("/_")):
                    return data
            except Exception as e:
                # Ignore invalid or corrupted lines in intents ledger
                print(f"Warning: skipping unparseable line in intents.jsonl: {e}", file=sys.stderr)
    return None


def _parse_review_iteration(raw_iter: Any) -> int | None:
    """The review iteration as an int, or None when it is absent or not a number."""
    if raw_iter is None:
        return None
    try:
        return int(raw_iter)
    except (ValueError, TypeError):
        return None


def _parse_review_finding_ids(raw_findings: Any) -> list[str] | None:
    """The review finding ids from a comma, semicolon or whitespace separated string or a list, or None."""
    if not raw_findings:
        return None
    if isinstance(raw_findings, str):
        return [f.strip() for f in re.split(r"[,;\s]+", raw_findings) if f.strip()]
    if isinstance(raw_findings, list):
        return [str(f).strip() for f in raw_findings if str(f).strip()]
    return None


@dataclass(frozen=True)
class ExecutionArgs:
    """The executor command line: ``<plan_branch> [agent_name] [model_name] [partial_execution_branch]``.

    The host CLI always passes resolved values; there is no harness detection inside the container.
    """

    plan_branch: str
    agent_name: str
    model_name: str
    # Passed by 'holon flow' only on a Stage 3 resume: a failed execution branch whose partial work to continue.
    partial_branch: str | None

    @classmethod
    def from_argv(cls, argv: list[str]) -> Self:
        """Parse ``sys.argv`` (at least the plan branch): a missing agent or model takes its default, an empty fourth
        argument means no partial branch."""
        agent_name = argv[2] if len(argv) > 2 else FALLBACK_AGENT
        return cls(
            plan_branch=argv[1],
            agent_name=agent_name,
            model_name=argv[3] if len(argv) > 3 else default_model_for(agent_name),
            partial_branch=argv[4] if len(argv) > 4 and argv[4] else None,
        )
