"""The ``--allow-overlap`` admission override is recorded once per plan.

Each override row is a commit on the plan branch, and a commit there moves the plan tip that a resumed execution's
partial work forked from. So a Stage 3 run admitted by ``--allow-overlap`` appends its ``admission_override`` row only
when the plan branch's ``executions.jsonl`` does not already carry one for this plan; a resume under the same override
then leaves the plan tip where it was.
"""

from __future__ import annotations

from sandbox_executor.flow.git_refs import _read_ledger_rows

#: ``entry_type`` and ``kind`` of the executions.jsonl row an override appends (never a summary or an action).
ADMISSION_OVERRIDE_KIND = "admission_override"


def override_already_recorded(repo_dir: str, plan_branch: str) -> bool:
    """True when the plan branch's ``executions.jsonl`` already holds an override row for this plan.

    The fetched origin branch is read first, then the local branch. A row counts only when both its ``plan_id`` and
    its ``plan_branch`` name this plan. An unreadable ledger reads as no row, so the override is recorded (fail
    towards recording).
    """
    plan_id = plan_branch.removesuffix("/_").rsplit("/", 1)[-1]
    for ref in (f"refs/remotes/origin/{plan_branch}", f"refs/heads/{plan_branch}"):
        rows = _read_ledger_rows(repo_dir, ref, "executions.jsonl")
        if any(
            row.get("kind") == ADMISSION_OVERRIDE_KIND
            and row.get("plan_id") == plan_id
            and row.get("plan_branch") == plan_branch
            for row in rows
        ):
            return True
    return False
