"""The execution verdict ``holon calibrate`` reconciles: read from the execution's summary rows in the ledger.

The flow takes the verdict from its own Stage 3 result; a hand-run calibration has no stage results, so it applies the
``calibration_outcome`` rules to the latest summary row instead, and never guesses when there is none.
"""

from __future__ import annotations

import os
from pathlib import Path

from sandbox_executor.calibration import LEDGER_EXECUTIONS_REL, read_git_file
from sandbox_executor.calibration_ev import is_matching_record, parse_summary_records
from sandbox_executor.calibration_outcome import is_converged_record, latest_summary_row, record_exit_code
from sandbox_executor.plan_paths import plan_branch_of


def resolve_execution_verdict(repo_dir: str, execution_branch: str) -> str | None:
    """``success`` or ``failure`` from the execution's latest summary row in the ledger, or None without one.

    Reads the ledger at the execution branch first, then from disk, and applies the ``calibration_outcome`` rules.
    """
    plan_branch = plan_branch_of(execution_branch)
    execution_id = next((part for part in execution_branch.split("/") if part.startswith("E-")), "")
    disk_path = os.path.join(repo_dir, LEDGER_EXECUTIONS_REL)
    for content in (
        read_git_file(execution_branch, LEDGER_EXECUTIONS_REL, repo_dir),
        Path(disk_path).read_text(encoding="utf-8") if os.path.exists(disk_path) else None,
    ):
        rows = [r for r in parse_summary_records(content or "") if is_matching_record(r, execution_id, plan_branch)]
        latest = latest_summary_row(rows)
        if latest is not None:
            return "success" if is_converged_record(latest) and record_exit_code(latest) == 0 else "failure"
    return None
