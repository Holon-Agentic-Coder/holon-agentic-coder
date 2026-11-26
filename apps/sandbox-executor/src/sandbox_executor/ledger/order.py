from __future__ import annotations

import json
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Sequence
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


def interleave_by_instant[T](
    kept: Sequence[T], kept_instants: Sequence[datetime | None], incoming: Sequence[tuple[datetime, T]]
) -> list[T]:
    running_max: list[datetime] = []
    high = _EARLIEST
    for instant in kept_instants:
        high = max(high, instant or _EARLIEST)
        running_max.append(high)
    slots: defaultdict[int, list[tuple[datetime, int, T]]] = defaultdict(list)
    for index, (instant, item) in enumerate(incoming):
        slots[bisect_right(running_max, instant)].append((instant, index, item))
    merged: list[T] = []
    for position in range(len(kept) + 1):
        merged.extend(item for _instant, _index, item in sorted(slots[position], key=lambda entry: entry[:2]))
        if position < len(kept):
            merged.append(kept[position])
    return merged


def merge_by_instant(canonical_rows: list[dict[str, Any]], fragment_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timed: list[tuple[datetime, dict[str, Any]]] = []
    untimed: list[dict[str, Any]] = []
    for row in fragment_rows:
        instant = _row_instant(row)
        if instant is None:
            untimed.append(row)
        else:
            timed.append((instant, row))
    return interleave_by_instant(canonical_rows, [_row_instant(row) for row in canonical_rows], timed) + untimed


def _format_utc_z(instant: datetime) -> str:
    return instant.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def utc_now_z() -> str:
    return _format_utc_z(datetime.now(UTC))


def to_utc_z(value: object) -> str | None:
    instant = parse_created_at(value)
    return None if instant is None else _format_utc_z(instant)
