"""A non-ledger pre-push sync conflict, kept resumable instead of failing the execution.

The executor aborts the merge with the target branch, so the execution branch stays a fast-forward of the plan branch
and carries only the executor's own commits. The branch is published with a ``sync_conflict`` row naming the
conflicted paths, and the flow resumes from review or hands the conflict to a resolver.
"""

import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.git import GitRepository
from sandbox_executor.ledger import atomic_ledger_append

SYNC_CONFLICT_STATUS = "sync_conflict"


class SyncConflictMessage(str):
    """The failure message of a non-ledger merge conflict, which also carries the conflicted paths."""

    conflicted_paths: tuple[str, ...]

    def __new__(cls, message: str, conflicted_paths: list[str]) -> "SyncConflictMessage":
        instance = super().__new__(cls, message)
        instance.conflicted_paths = tuple(conflicted_paths)
        return instance


def sync_conflict_paths(sync_message: str) -> tuple[str, ...] | None:
    """Return the conflicted paths when ``sync_message`` reports a non-ledger conflict, else None."""
    if isinstance(sync_message, SyncConflictMessage):
        return sync_message.conflicted_paths
    return None


def superseding_failure_row(base_row: dict[str, Any], summary: str) -> dict[str, Any]:
    """Failure summary row that supersedes ``base_row`` in the append-only ``executions.jsonl``.

    Readers select the row with the highest ``ledger_revision`` and never merge fields backwards
    (docs/ledger_schema.md, "Reader Contract for Superseding Rows"), so the new row repeats every
    authoritative field of the row it replaces (agent output accounting, execution notes, elapsed and
    budget seconds, sub-intents) and changes only the status, summary, timestamp and revision.
    """
    return {
        **base_row,
        "status": "failure",
        "summary": summary,
        "created_at": datetime.now(UTC).isoformat(),
        "ledger_revision": int(base_row.get("ledger_revision", 1)) + 1,
    }


def sync_conflict_row(failure_row: dict[str, Any], conflicted_paths: tuple[str, ...]) -> dict[str, Any]:
    """Turn the superseding row built by ``superseding_failure_row`` into the resumable ``sync_conflict`` row."""
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
    """Commit the row for a failed pre-push sync and return whether the execution branch is still publishable.

    A non-ledger conflict of a successful execution keeps the executor's own commits and records a resumable
    ``sync_conflict`` row. Any other sync failure, and a conflict after a failed, over-budget or decomposed
    execution, records a superseding failure and the branch is not published.
    """
    print(f"Error: Pre-push sync failed: {sync_msg}", file=sys.stderr)
    conflicted_paths = sync_conflict_paths(sync_msg)
    # Also reachable when the agent was skipped, whose exec_entry is the decomposed or over_budget row.
    resumable = conflicted_paths is not None and exec_entry.get("status") == "success"
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
    repo.add("holon-knowledge/ledger/executions.jsonl", check=False)
    repo.commit(commit_msg, check=False)
    return resumable
