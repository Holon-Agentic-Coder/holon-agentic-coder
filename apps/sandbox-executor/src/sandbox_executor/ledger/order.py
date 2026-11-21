"""Chronological order for the rows a ledger conflict resolution appends after the untouched prefix.

Only the appended tail is ordered: the prefix side's rows are published history and never move (the append-only
ledger constraint, enforced by ``assert_ledger_prefix``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime


def _created_at(line: str) -> datetime | None:
    """The row's ``created_at`` as an aware datetime, or None when the line or the timestamp does not parse."""
    try:
        value = json.loads(line).get("created_at")
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _order_appended_rows(lines: list[str]) -> list[str]:
    """Return ``lines`` with the timestamped rows in ``created_at`` order; the rest keep their positions.

    The sort is stable (file order breaks ties) and a row without a parsable ``created_at`` stays where it is, so a
    malformed or legacy row is never moved past its neighbours.
    """
    stamps = [_created_at(line) for line in lines]
    slots = [index for index, stamp in enumerate(stamps) if stamp is not None]
    ordered = sorted(slots, key=lambda index: (stamps[index], index))
    result = list(lines)
    for slot, source in zip(slots, ordered, strict=True):
        result[slot] = lines[source]
    return result
