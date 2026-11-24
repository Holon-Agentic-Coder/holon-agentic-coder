"""Per-test evidence for the CI timing gate (``scripts/ci_timing_gate.py``).

The gate hands this module the per-test times of the current job and of its ``main`` baseline records. The module
renders the per-test report: the tests whose time grew the most, which never fails a job on its own.

The module uses the standard library only and keeps Python 3.9 syntax, because the workflows run the gate with the
runner's own ``python3``. It never imports from ``scripts/ci_timing_gate.py``; the gate imports it.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

REPORT_LIMIT = 10
REPORT_GROWTH = 0.5
REPORT_MIN_SECONDS = 5.0


@dataclass(frozen=True)
class Offender:
    """A test whose time grew beyond the report thresholds."""

    test_id: str
    baseline: float
    current: float

    @property
    def growth(self) -> float:
        """Return the absolute growth in seconds."""
        return self.current - self.baseline


def top_offenders(
    current: dict[str, float],
    baselines: Sequence[dict[str, float]],
    limit: int = REPORT_LIMIT,
    growth: float = REPORT_GROWTH,
    min_seconds: float = REPORT_MIN_SECONDS,
) -> list[Offender]:
    """Return the tests whose time grew by more than ``growth`` and ``min_seconds`` against their baseline median.

    Tests absent from every baseline record are ignored. Rows are sorted by absolute growth, largest first.
    """
    rows: list[Offender] = []
    for test_id, seconds in current.items():
        values = [b[test_id] for b in baselines if test_id in b]
        if not values:
            continue
        median = float(statistics.median(values))
        if seconds > median * (1 + growth) and seconds - median > min_seconds:
            rows.append(Offender(test_id, median, seconds))
    rows.sort(key=lambda row: (-row.growth, row.test_id))
    return rows[:limit]


def format_report(rows: Sequence[Offender]) -> list[str]:
    """Render the per-test report as markdown lines."""
    if not rows:
        return ["Per-test report: no test grew by more than 50 % and 5 s against its baseline median."]
    lines = [
        f"Per-test report: top {len(rows)} tests that grew by more than 50 % and 5 s against their baseline median.",
        "",
        "| test | baseline median (s) | current (s) | growth (s) |",
        "| ---- | ------------------- | ----------- | ---------- |",
    ]
    for row in rows:
        lines.append(f"| `{row.test_id}` | {row.baseline:.1f} | {row.current:.1f} | +{row.growth:.1f} |")
    return lines


def per_test_report(current: dict[str, float], baselines: Sequence[dict[str, float]]) -> list[str]:
    """Render the per-test report, or say it was skipped when no baseline record carries per-test times."""
    if not any(baselines):
        return ["Per-test report: skipped, no baseline records with per-test times yet."]
    return format_report(top_offenders(current, baselines))
