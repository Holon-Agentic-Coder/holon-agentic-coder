"""Inline SVG charts for the CI duration report: duration over time and the queue-versus-run strip.

The charts are plain SVG strings with no script and no external reference. Every API-derived string is escaped, and
every point is an ``<a href>`` to its run with a ``<title>`` tooltip. ``main`` points are circles, pull-request
points squares and other runs (dispatch, schedule, branch pushes) triangles, so the ref is never told by colour alone;
failures are hollow, cancelled jobs grey and re-runs carry an outer ring. Colours come from CSS classes defined by
``scripts.ci_durations_html``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from html import escape

from scripts.ci_durations_collect import JobDuration, parse_time

WIDTH, HEIGHT = 760, 240
LEFT, RIGHT, TOP, BOTTOM = 56, 16, 14, 30
STRIP_HEIGHT = 72
FAILED = ("failure", "timed_out", "startup_failure")
Y_TICKS = 4


@dataclass(frozen=True)
class Scale:
    """A linear map from a data domain onto a pixel range."""

    lo: float
    hi: float
    start: float
    end: float

    def __call__(self, value: float) -> float:
        span = self.hi - self.lo or 1.0
        return round(self.start + (value - self.lo) / span * (self.end - self.start), 1)


def fmt_seconds(seconds: float | None) -> str:
    """Return a duration as whole seconds, or "n/a" when there is none."""
    return "n/a" if seconds is None else f"{seconds:.0f} s"


def nice_max(value: float) -> float:
    """Return ``value`` rounded up to a multiple of half its power of ten (630 becomes 650), for the axis top."""
    if value <= 0:
        return 10.0
    step = 10 ** math.floor(math.log10(value)) / 2
    return math.ceil(value / step) * step


def point_classes(row: JobDuration) -> str:
    """Return the CSS classes that encode a point's ref, outcome and attempt."""
    classes = ["pt", row.ref_class if row.ref_class in ("main", "pr") else "other"]
    if row.conclusion in FAILED:
        classes.append("fail")
    elif row.conclusion != "success":
        classes.append("cancelled")
    if row.is_rerun:
        classes.append("rerun")
    return " ".join(classes)


def tooltip(row: JobDuration, median: float | None) -> str:
    """Return the escaped tooltip text of a point."""
    ref = f"PR #{row.pr_number}" if row.pr_number is not None else row.ref_class
    text = (
        f"{row.job} on {row.runner_os}: {fmt_seconds(row.duration_seconds)} (queued {fmt_seconds(row.queue_seconds)},"
        f" baseline {fmt_seconds(median)}), {row.conclusion}, attempt {row.run_attempt}, {row.head_sha[:7]} on "
        f"{row.branch}, {ref}, started {row.started_at}"
    )
    return escape(text, quote=True)


def marker(row: JobDuration, x: float, y: float, median: float | None) -> str:
    """Return a linked point: a circle for ``main``, a square for a pull request, a triangle otherwise, with a ring
    for a re-run."""
    if row.ref_class == "main":
        shape = f'<circle class="mark" cx="{x}" cy="{y}" r="4.5"/>'
    elif row.ref_class == "pr":
        shape = f'<rect class="mark" x="{x - 4.5}" y="{y - 4.5}" width="9" height="9" rx="1"/>'
    else:
        shape = f'<polygon class="mark" points="{x},{y - 5.5} {x + 5.5},{y + 4.5} {x - 5.5},{y + 4.5}"/>'
    ring = f'<circle class="ring" cx="{x}" cy="{y}" r="8"/>' if row.is_rerun else ""
    hit = f'<circle class="hit" cx="{x}" cy="{y}" r="10"/>'
    return (
        f'<a href="{escape(row.run_url, quote=True)}" class="{point_classes(row)}">'
        f"<title>{tooltip(row, median)}</title>{hit}{ring}{shape}</a>"
    )


def _x_scale(rows: list[JobDuration]) -> Scale:
    stamps = [parse_time(row.started_at).timestamp() for row in rows]
    lo, hi = min(stamps), max(stamps)
    pad = max((hi - lo) * 0.03, 3600.0)
    return Scale(lo - pad, hi + pad, LEFT, WIDTH - RIGHT)


def _axes(x: Scale, y: Scale, rows: list[JobDuration]) -> str:
    parts = []
    for tick in range(Y_TICKS + 1):
        value = y.lo + (y.hi - y.lo) * tick / Y_TICKS
        parts.append(
            f'<line class="grid" x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{y(value)}" y2="{y(value)}"/>'
            f'<text class="tick" x="{LEFT - 6}" y="{y(value) + 4}" text-anchor="end">{value:.0f} s</text>'
        )
    first, last = rows[0].started_at, rows[-1].started_at
    for stamp, anchor in ((first, "start"), (last, "end")):
        position = x(parse_time(stamp).timestamp())
        parts.append(
            f'<text class="tick" x="{position}" y="{HEIGHT - 8}" text-anchor="{anchor}">{escape(stamp[:10])}</text>'
        )
    return "".join(parts)


def _median_line(rows: list[JobDuration], medians: dict[JobDuration, float | None], x: Scale, y: Scale) -> str:
    coords = [
        f"{x(parse_time(row.started_at).timestamp())},{y(median)}"
        for row in rows
        if (median := medians.get(row)) is not None
    ]
    if not coords:
        return ""
    return f'<polyline class="median" points="{" ".join(coords)}"><title>rolling median</title></polyline>'


def _ceiling_line(ceiling: int | None, y: Scale) -> str:
    if ceiling is None:
        return ""
    at = y(ceiling)
    return (
        f'<line class="ceiling" x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{at}" y2="{at}"/>'
        f'<text class="ceiling-label" x="{WIDTH - RIGHT}" y="{at - 5}" text-anchor="end">ceiling {ceiling} s</text>'
    )


def duration_chart(rows: list[JobDuration], medians: dict[JobDuration, float | None], ceiling: int | None) -> str:
    """Return the duration-over-time chart of one series (rows ordered by start time)."""
    top = max([row.duration_seconds for row in rows] + [m for row in rows if (m := medians.get(row))] + [ceiling or 0])
    x, y = _x_scale(rows), Scale(0.0, nice_max(top * 1.05), HEIGHT - BOTTOM, TOP)
    points = "".join(
        marker(row, x(parse_time(row.started_at).timestamp()), y(row.duration_seconds), medians.get(row))
        for row in rows
    )
    return (
        f'<svg class="chart" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="Duration over time">'
        f"{_axes(x, y, rows)}{_ceiling_line(ceiling, y)}{_median_line(rows, medians, x, y)}{points}</svg>"
    )


def queue_strip(rows: list[JobDuration], medians: dict[JobDuration, float | None]) -> str:
    """Return a strip of stacked bars, one per point in start order: queue time below, run time above."""
    total = nice_max(max(row.queue_seconds + row.duration_seconds for row in rows))
    y = Scale(0.0, total, STRIP_HEIGHT - 4, 4)
    step = (WIDTH - LEFT - RIGHT) / len(rows)
    width = max(1.0, min(14.0, step - 2))
    bars = []
    for index, row in enumerate(rows):
        left = round(LEFT + index * step + (step - width) / 2, 1)
        queue_top, run_top = y(row.queue_seconds), y(row.queue_seconds + row.duration_seconds)
        bars.append(
            f'<a href="{escape(row.run_url, quote=True)}"><title>{tooltip(row, medians.get(row))}</title>'
            f'<rect class="queue" x="{left}" y="{queue_top}" width="{width}" height="{round(y(0) - queue_top, 1)}"/>'
            f'<rect class="run" x="{left}" y="{run_top}" width="{width}" '
            f'height="{round(max(0.0, queue_top - run_top - 2), 1)}"/></a>'
        )
    return (
        f'<svg class="strip" viewBox="0 0 {WIDTH} {STRIP_HEIGHT}" role="img" aria-label="Queue and run time">'
        f'<text class="tick" x="{LEFT - 6}" y="12" text-anchor="end">{total:.0f} s</text>{"".join(bars)}</svg>'
    )
