"""A non-ledger pre-push sync conflict, kept resumable instead of failing the execution.

The executor aborts the merge with the target branch, so the execution branch stays a fast-forward of the plan branch
and carries only the executor's own commits. The branch is published with a ``sync_conflict`` row naming the
conflicted paths, and the flow resumes from review or hands the conflict to a resolver.
"""

from typing import Any

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


def sync_conflict_row(
    failure_row: dict[str, Any], conflicted_paths: tuple[str, ...], sync_message: str
) -> dict[str, Any]:
    """Turn the superseding row built by ``superseding_failure_row`` into the resumable ``sync_conflict`` row."""
    summary = (
        f"Pre-push sync conflict: {sync_message}. The execution branch keeps the executor's commits without a merge "
        "of the target branch; resolve the conflict to continue."
    )
    return {
        **failure_row,
        "status": SYNC_CONFLICT_STATUS,
        "summary": summary,
        "conflicted_paths": list(conflicted_paths),
    }
