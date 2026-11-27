import json
import os
import sys
from typing import Any

from sandbox_executor.git import GitRepository
from sandbox_executor.ledger import atomic_ledger_append
from sandbox_executor.ledger.fragments import ledger_commit_paths
from sandbox_executor.ledger.order import utc_now_z

SYNC_CONFLICT_STATUS = "sync_conflict"


class SyncConflictMessage(str):
    conflicted_paths: tuple[str, ...]

    def __new__(cls, message: str, conflicted_paths: list[str]) -> "SyncConflictMessage":
        instance = super().__new__(cls, message)
        instance.conflicted_paths = tuple(conflicted_paths)
        return instance


def sync_conflict_paths(sync_message: str) -> tuple[str, ...] | None:
    if isinstance(sync_message, SyncConflictMessage):
        return sync_message.conflicted_paths
    return None


def superseding_failure_row(base_row: dict[str, Any], summary: str) -> dict[str, Any]:
    return {
        **base_row,
        "status": "failure",
        "summary": summary,
        "created_at": utc_now_z(),
        "ledger_revision": int(base_row.get("ledger_revision", 1)) + 1,
    }


def sync_conflict_row(failure_row: dict[str, Any], conflicted_paths: tuple[str, ...]) -> dict[str, Any]:
    summary = (
        f"Pre-push sync conflict with the target branch in {list(conflicted_paths)}. The execution branch keeps the "
        "executor's commits without a merge of the target branch; resolve the conflict to continue."
    )
    return {
        **failure_row,
        "status": SYNC_CONFLICT_STATUS,
        "summary": summary,
        "conflicted_paths": list(conflicted_paths),
    }


def record_sync_failure(
    repo: GitRepository, exec_entry: dict[str, Any], exec_id: str, ledger_dir: str, sync_msg: str
) -> bool:
    print(f"Error: Pre-push sync failed: {sync_msg}", file=sys.stderr)
    conflicted_paths = sync_conflict_paths(sync_msg)
    execution_succeeded = exec_entry.get("status") == "success"
    resumable = conflicted_paths is not None and execution_succeeded
    if resumable:
        ledger_row = sync_conflict_row(superseding_failure_row(exec_entry, sync_msg), conflicted_paths or ())
        commit_msg = f"execute: record sync conflict for {exec_id}"
    else:
        ledger_row = superseding_failure_row(
            exec_entry, f"Pre-push sync failure: {sync_msg}. The branch was not published."
        )
        commit_msg = f"execute: record sync failure for {exec_id}"
    with atomic_ledger_append(os.path.join(ledger_dir, "executions.jsonl")) as ef:
        ef.write(json.dumps(ledger_row) + "\n")
    repo.add(ledger_commit_paths(os.path.dirname(os.path.dirname(ledger_dir)), "executions.jsonl"), check=False)
    repo.commit(commit_msg, check=False)
    return resumable
