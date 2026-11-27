from __future__ import annotations

from sandbox_executor.flow.git_refs import _read_ledger_rows

# why: each override row is a plan-branch commit that would move the tip a resumed execution forked from
ADMISSION_OVERRIDE_KIND = "admission_override"


def override_already_recorded(repo_dir: str, plan_branch: str) -> bool:
    plan_id = plan_branch.removesuffix("/_").rsplit("/", 1)[-1]
    # why: an unreadable ledger reads as no row, failing towards recording the override
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
