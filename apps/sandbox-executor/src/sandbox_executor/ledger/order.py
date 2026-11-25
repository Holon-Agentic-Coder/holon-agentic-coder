from __future__ import annotations

import json
from bisect import bisect_right
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

_EARLIEST = datetime.min.replace(tzinfo=UTC)


def parse_created_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _created_at(line: str) -> datetime | None:
    try:
        value = json.loads(line).get("created_at")
    except (ValueError, TypeError, AttributeError):
        return None
    return parse_created_at(str(value))


def _order_appended_rows(lines: list[str]) -> list[str]:
    parsed_timestamps = [_created_at(line) for line in lines]
    timestamped_indices = [index for index, stamp in enumerate(parsed_timestamps) if stamp is not None]
    chronological_indices = sorted(timestamped_indices, key=lambda index: (parsed_timestamps[index], index))
    reordered_lines = list(lines)
    for slot_index, source_index in zip(timestamped_indices, chronological_indices, strict=True):
        reordered_lines[slot_index] = lines[source_index]
    return reordered_lines


def _row_instant(row: dict[str, Any]) -> datetime | None:
    return parse_created_at(row.get("created_at"))


def merge_by_instant(canonical_rows: list[dict[str, Any]], fragment_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    running_max: list[datetime] = []
    high = _EARLIEST
    for row in canonical_rows:
        high = max(high, _row_instant(row) or _EARLIEST)
        running_max.append(high)
    slots: defaultdict[int, list[tuple[datetime, int, dict[str, Any]]]] = defaultdict(list)
    untimed: list[dict[str, Any]] = []
    for index, row in enumerate(fragment_rows):
        instant = _row_instant(row)
        if instant is None:
            untimed.append(row)
            continue
        slots[bisect_right(running_max, instant)].append((instant, index, row))
    merged: list[dict[str, Any]] = []
    for position in range(len(canonical_rows) + 1):
        merged.extend(row for _instant, _index, row in sorted(slots[position], key=lambda entry: entry[:2]))
        if position < len(canonical_rows):
            merged.append(canonical_rows[position])
    return merged + untimed
