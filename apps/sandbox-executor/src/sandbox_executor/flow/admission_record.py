from __future__ import annotations

from sandbox_executor.flow.git_refs import _read_ledger_rows

ADMISSION_OVERRIDE_KIND = "admission_override"


# why: each override row is a commit that moves the plan tip a resumed execution forked from
def override_already_recorded(repo_dir: str, plan_branch: str) -> bool:
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
