"""Final-outcome rules for a plan's summary rows in the executions ledger (Stage 5 calibration).

Rows are ordered by flow order: their parsed ``created_at`` instant first (``+00:00`` and ``Z`` both read as UTC),
``ledger_revision`` only to break a tie at the same instant, then ledger line order (the sort is stable). Callers pass
only the plan's Stage 3 execution rows (``calibration_rows``), and a later row of the same execution id supersedes its
earlier rows (``latest_row_per_execution``); separate Stage 3 attempts keep their own rows.

A failed, non-zero-exit or ``sync_conflict`` row that a later delivered (converged, zero-exit) execution row follows is
a resolved failure, not a bug; a ``sync_conflict`` row is also resolved by a later delivered row of any stage (Stage 4
resolves a resumable conflict). A final unresolved ``sync_conflict`` row is a failure. A row whose failure names a
harness condition (``calibration_rows.is_infrastructure_failure``) never counts as a bug.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sandbox_executor.calibration_rows import is_infrastructure_failure

# Mirrors the status Stage 3 writes when its pre-push sync with main conflicts (kept literal: no entrypoint import).
SYNC_CONFLICT_STATUS = "sync_conflict"

_EARLIEST = datetime.min.replace(tzinfo=UTC)

# 'halted_for_human' is kept for historical ledger rows.
CONVERGED_SUMMARY_STATUSES: frozenset[str] = frozenset({"success", "approved", "halted_for_human"})


def is_converged_record(record: dict[str, Any]) -> bool:
    """True for a summary row that ends a run successfully.

    ``success``, ``approved`` and legacy ``halted_for_human`` do; so does the superseding ``decomposed`` row Stage 3
    writes once every child merged and the plan branch was integrated (``integrated: true``), which carries no
    ``exit_code``.
    """
    status = str(record.get("status", "success"))
    return status in CONVERGED_SUMMARY_STATUSES or (status == "decomposed" and record.get("integrated") is True)


def record_exit_code(record: dict[str, Any]) -> int:
    """The row's exit code; a row without one is 0 when it converged or is a (not failed) ``decomposed`` row, else 1."""
    ok = is_converged_record(record) or str(record.get("status", "success")) == "decomposed"
    return int(record.get("exit_code", 0 if ok else 1))


def _created_at_instant(record: dict[str, Any]) -> datetime:
    """The row's ``created_at`` as an aware UTC instant; a missing or unparseable value is the earliest instant."""
    raw = str(record.get("created_at", "") or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        instant = datetime.fromisoformat(raw)
    except ValueError:
        return _EARLIEST
    return instant if instant.tzinfo is not None else instant.replace(tzinfo=UTC)


def summary_row_order_key(record: dict[str, Any]) -> tuple[datetime, int]:
    """Sort key putting summary rows in flow order: instant first, ``ledger_revision`` (default 1) as the tie-break."""
    try:
        revision = int(record.get("ledger_revision") or 1)
    except (TypeError, ValueError):
        revision = 1
    return (_created_at_instant(record), revision)


def latest_summary_row(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The last summary row in flow order (the last ledger line on a full tie), or None for no rows."""
    return sorted(records, key=summary_row_order_key)[-1] if records else None


def _is_sync_conflict(record: dict[str, Any]) -> bool:
    return str(record.get("status", "")) == SYNC_CONFLICT_STATUS


def _counts_as_failure(record: dict[str, Any]) -> bool:
    """True for a ``failed``, non-zero-exit or ``sync_conflict`` row."""
    status = str(record.get("status", "success"))
    return status == "failed" or _is_sync_conflict(record) or record_exit_code(record) != 0


def is_delivered_record(record: dict[str, Any]) -> bool:
    """True for a converged row with a zero exit code: the run delivered."""
    return is_converged_record(record) and record_exit_code(record) == 0


def _is_superseded_failure(
    record: dict[str, Any], later_records: list[dict[str, Any]], later_plan_rows: list[dict[str, Any]]
) -> bool:
    """True for a failing row that a later delivered execution row resolves, or a ``sync_conflict`` row that a later
    delivered row of any stage resolves (Stage 4 resolves a resumable conflict and approves the head)."""
    if any(is_delivered_record(later) for later in later_records):
        return True
    return _is_sync_conflict(record) and any(is_delivered_record(later) for later in later_plan_rows)


def _rows_after(record: dict[str, Any], ordered_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows strictly after ``record`` in flow order (``record`` itself excluded, by identity or order key)."""
    key = summary_row_order_key(record)
    return [row for row in ordered_rows if row is not record and summary_row_order_key(row) > key]


def latest_row_per_execution(ordered_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The last row (in flow order) of each execution id, kept in flow order: a superseding revision replaces its
    earlier rows, while separate Stage 3 attempts (different ids) all stay."""
    latest: dict[str, dict[str, Any]] = {}
    for record in sorted(ordered_records, key=summary_row_order_key):
        latest.pop(str(record.get("execution_id") or ""), None)
        latest[str(record.get("execution_id") or "")] = record
    return list(latest.values())


def _later_plan_rows(record: dict[str, Any], plan_rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return _rows_after(record, sorted(plan_rows or [], key=summary_row_order_key))


def has_delivered(ordered_records: list[dict[str, Any]], plan_rows: list[dict[str, Any]] | None = None) -> bool:
    """True when an execution row delivered, or a ``sync_conflict`` row was resolved by a later delivered row of the
    plan (any stage): the Clean EV outcome."""
    return any(
        is_delivered_record(record)
        or (_is_sync_conflict(record) and any(is_delivered_record(r) for r in _later_plan_rows(record, plan_rows)))
        for record in ordered_records
    )


def has_unresolved_failure(
    ordered_records: list[dict[str, Any]], plan_rows: list[dict[str, Any]] | None = None
) -> bool:
    """True when a non-infrastructure execution row (in flow order) failed and nothing later resolved it.

    A final ``sync_conflict`` row is unresolved unless a later delivered row of the plan supersedes it.

    Args:
        ordered_records: The plan's Stage 3 execution rows in flow order.
        plan_rows: Every summary row of the plan (any stage), consulted only to resolve a ``sync_conflict`` row; None
            uses ``ordered_records``.
    """
    rows = ordered_records if plan_rows is None else plan_rows
    return any(
        _counts_as_failure(record)
        and not is_infrastructure_failure(record)
        and not _is_superseded_failure(record, ordered_records[index + 1 :], _later_plan_rows(record, rows))
        for index, record in enumerate(ordered_records)
    )
