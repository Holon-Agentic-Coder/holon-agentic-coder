"""Resolve a bare execution id to its plan branch for calibration, never across plans."""

from __future__ import annotations

import logging

from sandbox_executor.calibration_ev import parse_summary_records
from sandbox_executor.ledger import read_merged_text
from sandbox_executor.plan_paths import split_plan_branch

logger = logging.getLogger(__name__)


def unique_plan_branch_of_execution(execution_id: str, executions_jsonl: str) -> str:
    """The plan branch of a bare execution id, only when exactly one plan in executions.jsonl has that execution.

    Execution ids are keyed on their full path: a new-generation id carries no hex, so two plans may each own an
    execution of the same name. An ambiguous bare id resolves to nothing rather than to an arbitrary plan.
    """
    content = read_merged_text(executions_jsonl)
    if content is not None:
        records = parse_summary_records(content)
        owners = {
            str(r["plan_branch"]) for r in records if r.get("execution_id") == execution_id and r.get("plan_branch")
        }
        plans = {split_plan_branch(owner) for owner in owners}
        if len(plans) == 1:
            return next(iter(owners))
        if len(plans) > 1:
            logger.warning("Execution id %s is ambiguous across plans %s; pass the full branch.", execution_id, owners)
    return ""
