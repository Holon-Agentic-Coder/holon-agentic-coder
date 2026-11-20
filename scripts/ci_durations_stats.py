"""Baselines, ceilings and the slowest-versus-baseline ranking for the CI duration report.

A series is one (workflow, job, runner OS). Its baseline at a point is the median of the previous ``window``
successful ``main`` durations, the current point excluded, as the CI timing gate compares a job with the median of its
last 5 ``main`` records; it is ``None`` while fewer than ``window`` exist. The ceiling is read from the committed
workflow file: the ``--ceiling N`` flag of a ``scripts/ci_timing_gate.py`` command in the job, or the ``max-seconds``
input of a ``./.github/actions/ci-timing-gate`` ``phase: check`` step (300 when omitted). No YAML parser is a project
dependency, so the workflow reader is an indentation-aware line scan; a ceiling it cannot see is ``None``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

from scripts.ci_durations_collect import JobDuration, job_key
from scripts.ci_timing_gate import rolling_median

DEFAULT_WINDOW = 5
COMPOSITE_GATE = "./.github/actions/ci-timing-gate"
COMPOSITE_DEFAULT_SECONDS = 300
_CEILING_FLAG = re.compile(r"--ceiling[\s=]+\"?(\d+)")
_MAX_SECONDS = re.compile(r"max-seconds:\s*[\"']?(\d+)")
_PHASE_CHECK = re.compile(r"phase:\s*[\"']?check\b")
_KEY = re.compile(r"^([A-Za-z0-9_.-]+):\s*(.*)$")
_EXPRESSION = re.compile(r"\$\{\{.*?\}\}")

SeriesKey = tuple[str, str, str]
Ceilings = dict[tuple[str, str], int]
Medians = dict[JobDuration, float | None]


class Regression(NamedTuple):
    """The latest ``main`` point of a series compared with its rolling-median baseline."""

    row: JobDuration
    median: float
    delta: float
    ratio: float


def series_key(row: JobDuration) -> SeriesKey:
    """Return the (workflow, job without matrix suffix, runner OS) that a point belongs to."""
    return (row.workflow, row.job_key, row.runner_os)


def group_series(rows: list[JobDuration]) -> dict[SeriesKey, list[JobDuration]]:
    """Group the points by series, each series ordered by start time."""
    groups: dict[SeriesKey, list[JobDuration]] = defaultdict(list)
    for row in sorted(rows, key=lambda r: (r.started_at, r.run_id, r.run_attempt)):
        groups[series_key(row)].append(row)
    return dict(sorted(groups.items()))


def is_baseline_point(row: JobDuration) -> bool:
    """Return true for a point that feeds the baseline: a successful job on ``main``."""
    return row.ref_class == "main" and row.conclusion == "success"


def rolling_medians(rows: list[JobDuration], window: int = DEFAULT_WINDOW) -> Medians:
    """Map every point to the median of the ``window`` baseline points before it in its series, or ``None``."""
    medians: Medians = {}
    for points in group_series(rows).values():
        previous: list[float] = []
        for row in points:
            medians[row] = rolling_median(previous[-window:]) if len(previous) >= window else None
            if is_baseline_point(row):
                previous.append(row.duration_seconds)
    return medians


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _job_blocks(text: str) -> dict[str, list[str]]:
    """Return the lines of each job under the top-level ``jobs:`` key, keyed by job id."""
    blocks: dict[str, list[str]] = {}
    in_jobs, job_indent, current = False, -1, ""
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if _indent(line) == 0:
            in_jobs, job_indent, current = line.startswith("jobs:"), -1, ""
            continue
        if not in_jobs:
            continue
        if job_indent < 0:
            job_indent = _indent(line)
        match = _KEY.match(line.strip())
        if _indent(line) == job_indent and match:
            current = match.group(1)
            blocks[current] = []
        elif current:
            blocks[current].append(line)
    return blocks


def _job_names(key: str, lines: list[str]) -> set[str]:
    """Return the job id and its ``name:`` with any matrix expression and suffix stripped."""
    names = {key}
    property_indent = min((_indent(line) for line in lines), default=0)
    for line in lines:
        match = _KEY.match(line.strip())
        if _indent(line) == property_indent and match and match.group(1) == "name":
            names.add(job_key(_EXPRESSION.sub("", match.group(2).strip().strip("'\"")).strip()))
    return names


def _steps(lines: list[str]) -> list[str]:
    """Split the lines after a job's ``steps:`` key into the text of each step, by the indentation of the first item."""
    start = next((index + 1 for index, line in enumerate(lines) if line.strip() == "steps:"), len(lines))
    steps: list[str] = []
    step_indent = next((_indent(line) for line in lines[start:] if line.lstrip().startswith("- ")), -1)
    for line in lines[start:]:
        if _indent(line) == step_indent and line.lstrip().startswith("- "):
            steps.append("")
        if steps:
            steps[-1] += line + "\n"
    return steps


def step_ceiling(step: str) -> int | None:
    """Return the ceiling a step enforces, or ``None`` when it is not a timing-gate step."""
    flag = _CEILING_FLAG.search(step)
    if flag and "ci_timing_gate.py" in step:
        return int(flag.group(1))
    if COMPOSITE_GATE in step and _PHASE_CHECK.search(step):
        seconds = _MAX_SECONDS.search(step)
        return int(seconds.group(1)) if seconds else COMPOSITE_DEFAULT_SECONDS
    return None


def parse_ceilings(text: str) -> dict[str, int]:
    """Map each job id and job name of one workflow file to the lowest ceiling its steps enforce."""
    ceilings: dict[str, int] = {}
    for key, lines in _job_blocks(text).items():
        found = [c for c in (step_ceiling(step) for step in _steps(lines)) if c is not None]
        if found:
            ceilings.update(dict.fromkeys(_job_names(key, lines), min(found)))
    return ceilings


def load_ceilings(workflows_dir: Path) -> Ceilings:
    """Map (workflow file name, job id or name) to its ceiling for every workflow in ``workflows_dir``."""
    ceilings: Ceilings = {}
    for path in sorted([*workflows_dir.glob("*.yml"), *workflows_dir.glob("*.yaml")]):
        for name, seconds in parse_ceilings(path.read_text(encoding="utf-8")).items():
            ceilings[(path.name, name)] = seconds
    return ceilings


def ceiling_for(row: JobDuration, ceilings: Ceilings) -> int | None:
    """Return the ceiling of the point's workflow and job, or ``None`` where none is defined."""
    return ceilings.get((Path(row.workflow_path).name, row.job_key))


def slowest_vs_baseline(rows: list[JobDuration], medians: Medians, limit: int = 10) -> list[Regression]:
    """Rank the latest ``main`` point of each series by how far it sits above its baseline, largest delta first."""
    found: list[Regression] = []
    for points in group_series(rows).values():
        main = [row for row in points if row.ref_class == "main"]
        median = medians.get(main[-1]) if main else None
        if median:
            latest = main[-1]
            found.append(Regression(latest, median, latest.duration_seconds - median, latest.duration_seconds / median))
    found.sort(key=lambda r: (-r.delta, series_key(r.row)))
    return found[:limit]


def annotate(rows: list[JobDuration], medians: Medians, ceilings: Ceilings) -> list[dict[str, object]]:
    """Return each point as JSON with its ``baseline_median`` and ``ceiling``."""
    return [
        {**row.to_json(), "baseline_median": medians.get(row), "ceiling": ceiling_for(row, ceilings)} for row in rows
    ]
