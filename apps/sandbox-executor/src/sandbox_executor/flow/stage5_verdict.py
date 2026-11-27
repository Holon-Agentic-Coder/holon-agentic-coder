from __future__ import annotations

import os

from sandbox_executor.calibration import LEDGER_EXECUTIONS_REL, read_git_file
from sandbox_executor.calibration_ev import is_matching_record, parse_summary_records
from sandbox_executor.calibration_outcome import is_converged_record, latest_summary_row, record_exit_code
from sandbox_executor.ledger import read_merged_text, with_ref_fragments
from sandbox_executor.plan_paths import plan_branch_of


# why: a hand-run calibration has no stage results, so the verdict comes from the ledger's latest summary row
def resolve_execution_verdict(repo_dir: str, execution_branch: str) -> str | None:
    plan_branch = plan_branch_of(execution_branch)
    execution_id = next((part for part in execution_branch.split("/") if part.startswith("E-")), "")
    canonical_content = read_git_file(execution_branch, LEDGER_EXECUTIONS_REL, repo_dir)
    # why: pending ledger fragments live on the execution branch, so it is read before the checkout on disk
    for content in (
        with_ref_fragments(canonical_content, repo_dir, execution_branch, "executions.jsonl"),
        read_merged_text(os.path.join(repo_dir, LEDGER_EXECUTIONS_REL)),
    ):
        rows = [r for r in parse_summary_records(content or "") if is_matching_record(r, execution_id, plan_branch)]
        latest = latest_summary_row(rows)
        if latest is not None:
            return "success" if is_converged_record(latest) and record_exit_code(latest) == 0 else "failure"
    return None
