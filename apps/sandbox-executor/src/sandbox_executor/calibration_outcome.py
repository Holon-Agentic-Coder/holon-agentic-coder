"""Final-outcome rules for a plan's summary rows in the executions ledger (Stage 5 calibration).

Rows are ordered by flow order: their parsed ``created_at`` instant first (``+00:00`` and ``Z`` both read as UTC),
``ledger_revision`` only to break a tie at the same instant, then ledger line order (the sort is stable). A Stage 3
``sync_conflict`` row that a later converged row supersedes is a resolved conflict, not a failure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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


def _counts_as_failure(record: dict[str, Any]) -> bool:
    return str(record.get("status", "success")) == "failed" or record_exit_code(record) != 0


def _is_sync_conflict(record: dict[str, Any]) -> bool:
    return str(record.get("status", "")) == SYNC_CONFLICT_STATUS


def _is_superseded_sync_conflict(record: dict[str, Any], later_records: list[dict[str, Any]]) -> bool:
    """True for a ``sync_conflict`` row that a later converged, zero-exit row resolves."""
    return _is_sync_conflict(record) and any(
        is_converged_record(later) and record_exit_code(later) == 0 for later in later_records
    )


def has_unresolved_failure(ordered_records: list[dict[str, Any]]) -> bool:
    """True when any row (in flow order) failed, bar a superseded ``sync_conflict``; a final conflict is unresolved."""
    if ordered_records and _is_sync_conflict(ordered_records[-1]):
        return True
    return any(
        _counts_as_failure(record) and not _is_superseded_sync_conflict(record, ordered_records[index + 1 :])
        for index, record in enumerate(ordered_records)
    )
