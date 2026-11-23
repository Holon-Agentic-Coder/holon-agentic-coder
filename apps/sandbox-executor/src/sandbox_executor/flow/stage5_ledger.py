"""Read-only ledger lookups for Stage 5: whether, and with which verdict, an execution was already reconciled."""

from __future__ import annotations

import contextlib
import json
from typing import Any


def already_reconciled(ledger_path: str, execution_branch: str) -> bool:
    """True when the ledger already holds a ``reconciliation`` event for ``execution_branch``."""
    return bool(recorded_verdicts(ledger_path, execution_branch))


def recorded_verdicts(ledger_path: str, execution_branch: str) -> list[str]:
    """The verdicts of the ledger's ``reconciliation`` events for ``execution_branch``, in ledger order."""
    wanted = {"event_type": "reconciliation", "parent_action_id": execution_branch}
    with open(ledger_path, encoding="utf-8") as f:
        rows = [_json_row(line) for line in f]
    return [str(row.get("verdict")) for row in rows if wanted.items() <= row.items()]


def _json_row(line: str) -> dict[str, Any]:
    with contextlib.suppress(ValueError):
        row = json.loads(line)
        return row if isinstance(row, dict) else {}
    return {}
