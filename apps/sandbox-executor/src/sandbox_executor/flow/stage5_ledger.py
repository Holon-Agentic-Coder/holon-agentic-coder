from __future__ import annotations

from sandbox_executor.ledger import read_merged_rows


def already_reconciled(ledger_path: str, execution_branch: str) -> bool:
    return bool(recorded_verdicts(ledger_path, execution_branch))


def recorded_verdicts(ledger_path: str, execution_branch: str) -> list[str]:
    wanted = {"event_type": "reconciliation", "parent_action_id": execution_branch}
    return [str(row.get("verdict")) for row in read_merged_rows(ledger_path) if wanted.items() <= row.items()]
