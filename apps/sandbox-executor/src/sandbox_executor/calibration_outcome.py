from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sandbox_executor.calibration_rows import is_infrastructure_failure

# why: mirrors Stage 3's pre-push sync conflict status; kept literal to avoid an entrypoint import.
SYNC_CONFLICT_STATUS = "sync_conflict"

_EARLIEST = datetime.min.replace(tzinfo=UTC)

# why: 'halted_for_human' is kept for historical ledger rows.
CONVERGED_SUMMARY_STATUSES: frozenset[str] = frozenset({"success", "approved", "halted_for_human"})


# why: an integrated ``decomposed`` row supersedes its children once the plan branch merged and carries no exit code.
def is_converged_record(record: dict[str, Any]) -> bool:
    status = str(record.get("status", "success"))
    return status in CONVERGED_SUMMARY_STATUSES or (status == "decomposed" and record.get("integrated") is True)


def record_exit_code(record: dict[str, Any]) -> int:
    ok = is_converged_record(record) or str(record.get("status", "success")) == "decomposed"
    return int(record.get("exit_code", 0 if ok else 1))


def _created_at_instant(record: dict[str, Any]) -> datetime:
    raw = str(record.get("created_at", "") or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        instant = datetime.fromisoformat(raw)
    except ValueError:
        return _EARLIEST
    return instant if instant.tzinfo is not None else instant.replace(tzinfo=UTC)


def summary_row_order_key(record: dict[str, Any]) -> tuple[datetime, int]:
    try:
        revision = int(record.get("ledger_revision") or 1)
    except (TypeError, ValueError):
        revision = 1
    return (_created_at_instant(record), revision)


def latest_summary_row(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    return sorted(records, key=summary_row_order_key)[-1] if records else None


def _is_sync_conflict(record: dict[str, Any]) -> bool:
    return str(record.get("status", "")) == SYNC_CONFLICT_STATUS


def _counts_as_failure(record: dict[str, Any]) -> bool:
    status = str(record.get("status", "success"))
    return status == "failed" or _is_sync_conflict(record) or record_exit_code(record) != 0


def is_delivered_record(record: dict[str, Any]) -> bool:
    return is_converged_record(record) and record_exit_code(record) == 0


# why: Stage 4 resolves a resumable ``sync_conflict`` and approves the head, so any later delivered plan row clears it.
def _is_superseded_failure(
    record: dict[str, Any], later_records: list[dict[str, Any]], later_plan_rows: list[dict[str, Any]]
) -> bool:
    if any(is_delivered_record(later) for later in later_records):
        return True
    return _is_sync_conflict(record) and any(is_delivered_record(later) for later in later_plan_rows)


def _rows_after(record: dict[str, Any], ordered_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    index = next((i for i, row in enumerate(ordered_rows) if row is record), None)
    if index is not None:
        return ordered_rows[index + 1 :]
    key = summary_row_order_key(record)
    return [row for row in ordered_rows if summary_row_order_key(row) > key]


def latest_row_per_execution(ordered_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in sorted(ordered_records, key=summary_row_order_key):
        latest.pop(str(record.get("execution_id") or ""), None)
        latest[str(record.get("execution_id") or "")] = record
    return list(latest.values())


def _later_plan_rows(record: dict[str, Any], plan_rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return _rows_after(record, sorted(plan_rows or [], key=summary_row_order_key))


def has_delivered(ordered_records: list[dict[str, Any]], plan_rows: list[dict[str, Any]] | None = None) -> bool:
    return any(
        is_delivered_record(record)
        or (_is_sync_conflict(record) and any(is_delivered_record(r) for r in _later_plan_rows(record, plan_rows)))
        for record in ordered_records
    )


def has_unresolved_failure(
    ordered_records: list[dict[str, Any]], plan_rows: list[dict[str, Any]] | None = None
) -> bool:
    return any(
        _counts_as_failure(record)
        and not is_infrastructure_failure(record)
        and not _is_superseded_failure(record, ordered_records[index + 1 :], _later_plan_rows(record, plan_rows))
        for index, record in enumerate(ordered_records)
    )
