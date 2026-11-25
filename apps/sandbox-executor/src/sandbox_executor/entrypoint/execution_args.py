import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Self

from sandbox_executor.agent_defaults import FALLBACK_AGENT, default_model_for
from sandbox_executor.plan_paths import is_row_of_plan

_PLANS_LEDGER_RELPATH = "holon-knowledge/ledger/plans.jsonl"
_INTENTS_LEDGER_RELPATH = "holon-knowledge/ledger/intents.jsonl"
_FINDING_ID_SPLIT_RE = re.compile(r"[,;\s]+")


def _read_plan_row(repo_dir: str, plan_branch_prefix: str) -> dict[str, Any] | None:
    plans_file_path = os.path.join(repo_dir, _PLANS_LEDGER_RELPATH)
    latest_plan_row = None
    if os.path.exists(plans_file_path):
        with open(plans_file_path) as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if is_row_of_plan(data, plan_branch_prefix):
                        latest_plan_row = data
                except Exception as e:
                    print(f"Warning: skipping line {line_no} in plans.jsonl: {e}", file=sys.stderr)
    return latest_plan_row


def _read_intent_row(repo_dir: str, target_intent_branch: str) -> dict[str, Any] | None:
    intents_file_path = os.path.join(repo_dir, _INTENTS_LEDGER_RELPATH)
    if not os.path.exists(intents_file_path):
        return None
    normalized_target_branch = target_intent_branch.rstrip("/_")
    with open(intents_file_path) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                branch = data.get("branch")
                if isinstance(branch, str) and normalized_target_branch == branch.rstrip("/_"):
                    return data
            except Exception as e:
                print(f"Warning: skipping unparseable line in intents.jsonl: {e}", file=sys.stderr)
    return None


def _parse_review_iteration(raw_iter: Any) -> int | None:
    if raw_iter is None:
        return None
    try:
        return int(raw_iter)
    except (ValueError, TypeError):
        return None


def _parse_review_finding_ids(raw_findings: Any) -> list[str] | None:
    if not raw_findings:
        return None
    if isinstance(raw_findings, str):
        return [finding.strip() for finding in _FINDING_ID_SPLIT_RE.split(raw_findings) if finding.strip()]
    if isinstance(raw_findings, list):
        return [str(finding).strip() for finding in raw_findings if str(finding).strip()]
    return None


@dataclass(frozen=True)
class ExecutionArgs:
    plan_branch: str
    agent_name: str
    model_name: str
    partial_branch: str | None

    @classmethod
    def from_argv(cls, argv: list[str]) -> Self:
        agent_name = argv[2] if len(argv) > 2 else FALLBACK_AGENT
        return cls(
            plan_branch=argv[1],
            agent_name=agent_name,
            model_name=argv[3] if len(argv) > 3 else default_model_for(agent_name),
            partial_branch=argv[4] if len(argv) > 4 and argv[4] else None,
        )
