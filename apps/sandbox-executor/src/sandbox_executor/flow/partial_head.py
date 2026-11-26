"""Failed partial head recovery for execution branches."""

from __future__ import annotations

from sandbox_executor.flow.git_refs import _latest_execution_summary, _read_ledger_rows
from sandbox_executor.git import EMPTY_DIFF_STATUS

# The executor entrypoint has historically written "failure"; guard rows use "failed". Both mean failure.
FAILED_EXECUTION_STATUSES: frozenset[str] = frozenset({"failed", "failure"})


def failed_partial_head(repo_dir: str, ref: str, execution_id: str) -> str | None:
    """The ``partial_head`` on ``ref``'s latest summary row for ``execution_id`` when that row is a failure.

    Both failure statuses count: ``failure`` (a budget breach, an agent error or any other failure) and
    ``EMPTY_DIFF_STATUS`` (an empty or unverified diff), since either row can carry salvaged partial work.
    """
    row = _latest_execution_summary(_read_ledger_rows(repo_dir, ref, "executions.jsonl"), execution_id, ref) or {}
    partial_head = row.get("partial_head")
    if row.get("status") in ("failure", EMPTY_DIFF_STATUS) and isinstance(partial_head, str) and partial_head:
        return partial_head
    return None


def _has_summary_row(repo_dir: str, ref: str, execution_id: str) -> bool:
    """Whether ``ref`` carries any summary row (success or failure) for ``execution_id``."""
    rows = _read_ledger_rows(repo_dir, ref, "executions.jsonl")
    return _latest_execution_summary(rows, execution_id, ref) is not None
