"""Read-only ledger lookups for Stage 5: whether, and with which verdict, an execution was already reconciled."""

from __future__ import annotations

from sandbox_executor.ledger import read_merged_rows


def already_reconciled(ledger_path: str, execution_branch: str) -> bool:
    """True when the ledger already holds a ``reconciliation`` event for ``execution_branch``."""
    return bool(recorded_verdicts(ledger_path, execution_branch))


def recorded_verdicts(ledger_path: str, execution_branch: str) -> list[str]:
    """The verdicts of the ledger's ``reconciliation`` events for ``execution_branch``, in ledger order."""
    wanted = {"event_type": "reconciliation", "parent_action_id": execution_branch}
    return [str(row.get("verdict")) for row in read_merged_rows(ledger_path) if wanted.items() <= row.items()]
