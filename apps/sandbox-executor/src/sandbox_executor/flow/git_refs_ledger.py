from __future__ import annotations

from typing import Any

from sandbox_executor.plan_paths import is_row_of_execution

DEFAULT_LEDGER_REVISION = 1


def _parse_ledger_revision(row: dict[str, Any]) -> int:
    try:
        return int(row.get("ledger_revision") or DEFAULT_LEDGER_REVISION)
    except (TypeError, ValueError):
        return DEFAULT_LEDGER_REVISION


def _latest_execution_summary(rows: list[dict[str, Any]], execution_id: str, branch: str = "") -> dict[str, Any] | None:
    best_rank: tuple[int, int] | None = None
    chosen_row: dict[str, Any] | None = None
    for idx, row in enumerate(rows):
        if row.get("entry_type") != "summary" or not is_row_of_execution(row, execution_id, branch):
            continue
        rank = (_parse_ledger_revision(row), idx)
        if best_rank is None or rank > best_rank:
            best_rank, chosen_row = rank, row
    return chosen_row


def _ledger_unique_id_keys(ledger: str) -> tuple[str, str]:
    if ledger == "plans.jsonl":
        return "plan_id", "intent_branch"
    return "execution_id", "plan_branch"
