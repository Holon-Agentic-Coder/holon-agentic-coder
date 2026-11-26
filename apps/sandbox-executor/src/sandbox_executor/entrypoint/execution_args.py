import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Self

from sandbox_executor.agent_defaults import FALLBACK_AGENT, default_model_for
from sandbox_executor.ledger import read_merged_rows
from sandbox_executor.plan_paths import is_row_of_plan

_PLANS_LEDGER_RELPATH = "holon-knowledge/ledger/plans.jsonl"
_INTENTS_LEDGER_RELPATH = "holon-knowledge/ledger/intents.jsonl"
_FINDING_ID_SPLIT_RE = re.compile(r"[,;\s]+")


def _read_plan_row(repo_dir: str, plan_branch_prefix: str) -> dict[str, Any] | None:
    def warn(line_no: int, exc: Exception) -> None:
        print(f"Warning: skipping line {line_no} in plans.jsonl: {exc}", file=sys.stderr)

    rows = read_merged_rows(os.path.join(repo_dir, _PLANS_LEDGER_RELPATH), on_bad_line=warn)
    return next((row for row in reversed(rows) if is_row_of_plan(row, plan_branch_prefix)), None)


def _read_intent_row(repo_dir: str, target_intent_branch: str) -> dict[str, Any] | None:
    def warn(_line_no: int, exc: Exception) -> None:
        print(f"Warning: skipping unparseable line in intents.jsonl: {exc}", file=sys.stderr)

    normalized_target_branch = target_intent_branch.rstrip("/_")
    for row in read_merged_rows(os.path.join(repo_dir, _INTENTS_LEDGER_RELPATH), on_bad_line=warn):
        branch = row.get("branch")
        if isinstance(branch, str) and normalized_target_branch == branch.rstrip("/_"):
            return row
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
