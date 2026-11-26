"""The review mode of one Stage 4 pass (``full`` or ``scoped``), and its JSON-safe record."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from sandbox_executor.flow.review.delta_review_limits import DEFAULT_SCOPED_REVIEW_CONTEXT_BYTES

SCOPED, FULL = "scoped", "full"
_COMMIT_ID = re.compile(r"[0-9a-f]{7,64}")


@dataclass(frozen=True)
class ReviewMode:
    """The mode of one pass and why; a scoped pass reviews ``base..head`` over ``paths``."""

    mode: str
    reason: str
    head: str = ""
    base: str = ""
    paths: tuple[str, ...] = ()
    delta_lines: int = 0
    context_bytes: int = DEFAULT_SCOPED_REVIEW_CONTEXT_BYTES

    @property
    def scoped(self) -> bool:
        return self.mode == SCOPED

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "paths": list(self.paths)}


def decision_from_dict(data: Any) -> ReviewMode:
    """The decision ``data`` records; anything malformed (or a scoped one without a commit-id range) gives ``full``."""
    try:
        paths = data.get("paths") or []
        if not isinstance(paths, list):
            raise TypeError("malformed paths")
        fields = (data["mode"], data["reason"], data.get("head") or "", data.get("base") or "")
        if not all(isinstance(v, str) for v in (*fields, *paths)) or fields[0] not in (SCOPED, FULL):
            raise TypeError("malformed decision")
        decision = ReviewMode(*fields, tuple(paths), int(data.get("delta_lines") or 0), int(data["context_bytes"]))
    except (AttributeError, KeyError, TypeError, ValueError):
        return ReviewMode(FULL, "malformed decision")
    commits = all(_COMMIT_ID.fullmatch(c) for c in (decision.head, decision.base))
    complete = commits and decision.paths and decision.context_bytes > 0
    return decision if decision.mode == FULL or complete else ReviewMode(FULL, "malformed decision", decision.head)
