from __future__ import annotations

import json
from datetime import UTC, datetime


def _created_at(line: str) -> datetime | None:
    try:
        value = json.loads(line).get("created_at")
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _order_appended_rows(lines: list[str]) -> list[str]:
    parsed_timestamps = [_created_at(line) for line in lines]
    timestamped_indices = [index for index, stamp in enumerate(parsed_timestamps) if stamp is not None]
    chronological_indices = sorted(timestamped_indices, key=lambda index: (parsed_timestamps[index], index))
    reordered_lines = list(lines)
    for slot_index, source_index in zip(timestamped_indices, chronological_indices, strict=True):
        reordered_lines[slot_index] = lines[source_index]
    return reordered_lines
