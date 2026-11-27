from __future__ import annotations

import logging

from sandbox_executor.calibration_ev import parse_summary_records
from sandbox_executor.ledger import read_merged_text
from sandbox_executor.plan_paths import split_plan_branch

logger = logging.getLogger(__name__)


# why: new-generation execution ids carry no hex, so two plans may own the same bare id; ambiguity resolves to nothing.
def unique_plan_branch_of_execution(execution_id: str, executions_jsonl: str) -> str:
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
