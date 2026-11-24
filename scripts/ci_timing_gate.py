#!/usr/bin/env python3
"""CI timing regression gate for the GitHub Actions workflows.

Every CI job takes a timestamp in its first step and calls this script in its last steps.

- ``record`` mode (pushes to ``main`` and manual dispatches) writes the job's wall-clock duration, job name, runner OS
  and, for test jobs, the test time and pytest's per-test durations from a JUnit XML report to a JSON record. The
  workflow uploads that record as an artefact named ``ci-timing-<workflow>-<job>-<os>`` once the job has succeeded on
  ``main``.
- ``compare`` mode (pull requests) fetches the last N records for the same artefact name from successful ``main`` runs
  and fails the job only when its duration exceeds the baseline median by more than the relative tolerance AND by more
  than the minimum absolute delta. Test jobs also print the tests whose time grew the most (the per-test report of
  ``scripts/ci_timing_noise.py``); that report never fails a job on its own.

A job with a JUnit report is judged on its test time, not on wall clock, so runner start-up, checkout and dependency
install do not count: the report's suite time ("suite test time"), or the sum of its test case times when it has none
("summed test time"). The record stores it in the optional keys ``test_seconds`` and ``test_seconds_source`` (``suite``
or ``sum``) next to the seven original keys; a record without them still loads. The two kinds differ by a factor of 2
to 4 under ``pytest -n auto`` and are never compared with each other.

Both modes fail above an absolute ceiling, which no label can override. While fewer than N baseline records exist (cold
start) the relative check is skipped with a notice. The ``timing-regression-accepted`` pull-request label turns a
relative failure into a visible warning.

Defaults (N = 5, tolerance 50 %, minimum delta 120 s, ceiling 600 s) were calibrated against real ``main`` runs; the
workflows spell every threshold out as a flag, so any change is a committed edit to a workflow file.

The script uses the standard library only and keeps Python 3.9 syntax, so runners need no set-up step. Network and
artefact access sit behind injectable functions; a failed fetch degrades to cold start with a warning.
"""

from __future__ import annotations

import argparse
import http.client
import io
import json
import os
import statistics
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

try:
    from scripts.ci_timing_noise import per_test_report, took
except ImportError:  # run as a script file: only scripts/ itself is on sys.path
    from ci_timing_noise import per_test_report, took

OVERRIDE_LABEL = "timing-regression-accepted"
API_ROOT = "https://api.github.com"
RECORD_FILE = "record.json"
REQUEST_TIMEOUT_SECONDS = 30
BASELINE_BRANCH = "main"


@dataclass(frozen=True)
class TimingRecord:
    """One job's measured duration, plus per-test durations and the test time (with its kind) for test jobs."""

    job: str
    os: str
    workflow: str
    duration: float
    commit: str = ""
    run_id: str = ""
    tests: dict[str, float] = field(default_factory=dict)
    test_seconds: float | None = None
    test_seconds_source: str = ""

    def to_json(self) -> dict[str, object]:
        """Return the record as a JSON-serialisable mapping; the test time keys appear only when it was measured."""
        data: dict[str, object] = {
            "job": self.job,
            "os": self.os,
            "workflow": self.workflow,
            "duration_seconds": self.duration,
            "commit": self.commit,
            "run_id": self.run_id,
            "tests": dict(self.tests),
        }
        if self.test_seconds is not None:
            data.update(test_seconds=self.test_seconds, test_seconds_source=self.test_seconds_source)
        return data

    @classmethod
    def from_json(cls, data: dict[str, object]) -> TimingRecord:
        """Build a record from its JSON mapping, raising ``KeyError`` or ``ValueError`` when it is malformed."""
        tests = data.get("tests") or {}
        if not isinstance(tests, dict):
            raise ValueError("record 'tests' is not a mapping")
        test_seconds = data.get("test_seconds")
        return cls(
            job=str(data["job"]),
            os=str(data["os"]),
            workflow=str(data["workflow"]),
            duration=float(data["duration_seconds"]),
            commit=str(data.get("commit", "")),
            run_id=str(data.get("run_id", "")),
            tests={str(k): float(v) for k, v in tests.items()},
            test_seconds=None if test_seconds is None else float(test_seconds),
            test_seconds_source=str(data.get("test_seconds_source") or ""),
        )

    def judged(self) -> tuple[float, str]:
        """Return the value the ceiling applies to and its kind: test time when measured, otherwise wall clock."""
        if self.test_seconds is None:
            return self.duration, ""
        return self.test_seconds, self.test_seconds_source


@dataclass(frozen=True)
class Thresholds:
    """The gate's configurable limits."""

    runs: int = 5
    tolerance: float = 0.5
    min_delta: float = 120.0
    ceiling: float = 600.0


@dataclass(frozen=True)
class Verdict:
    """The gate's decision and the message lines that explain it."""

    passed: bool
    lines: tuple[str, ...]


def artefact_name(workflow: str, job: str, os_name: str) -> str:
    """Return the artefact name that keys records by workflow, job and runner OS."""
    return f"ci-timing-{workflow}-{job}-{os_name}"


def read_junit(path: str) -> tuple[dict[str, float], float | None, str]:
    """Return the per-test times, the test time and its kind from one parse of a JUnit XML report.

    Per-test times map ``classname::name`` to seconds for every ``testcase``: a missing or empty ``time`` attribute
    counts as zero and repeated ids keep the largest time. Test time is the summed ``time`` of the top-level suites
    (kind ``suite``; nested suites are ignored) when each has a plain decimal one above zero, otherwise the sum of the
    per-test times (kind ``sum``); a report with neither has no test time.
    """
    root = ET.parse(path).getroot()
    times: dict[str, float] = {}
    for case in root.iter("testcase"):
        test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
        seconds = float(case.get("time") or 0)
        times[test_id] = max(seconds, times.get(test_id, 0.0))
    top_level = [root] if root.tag == "testsuite" else root.findall("testsuite")
    suites = [suite.get("time") or "" for suite in top_level]
    if suites and all(text.replace(".", "", 1).isdecimal() and float(text) > 0 for text in suites):
        return times, sum(float(text) for text in suites), "suite"
    return (times, sum(times.values()), "sum") if times else (times, None, "")


def parse_junit(path: str) -> dict[str, float]:
    """Map ``classname::name`` to seconds for every ``testcase`` in a JUnit XML report (see ``read_junit``)."""
    return read_junit(path)[0]


def rolling_median(values: Sequence[float]) -> float | None:
    """Return the median of ``values``, or ``None`` when there are none."""
    if not values:
        return None
    return float(statistics.median(values))


def exceeds_relative(duration: float, median: float, thresholds: Thresholds) -> bool:
    """Return true only when ``duration`` exceeds ``median`` by both the tolerance and the minimum delta."""
    over_tolerance = duration > median * (1 + thresholds.tolerance)
    over_delta = duration - median > thresholds.min_delta
    return over_tolerance and over_delta


def check_ceiling(duration: float, thresholds: Thresholds, kind: str = "") -> Verdict | None:
    """Return a failing verdict when ``duration``, a value of measure ``kind``, is above the ceiling, else ``None``."""
    if duration <= thresholds.ceiling:
        return None
    judged = took(duration, kind)
    return Verdict(
        False,
        (
            f"::error::{judged[0].upper()}{judged[1:]}, above the {thresholds.ceiling:.0f} s ceiling. The ceiling "
            f"cannot be overridden by the '{OVERRIDE_LABEL}' label; it changes only through a committed edit to the "
            "--ceiling flag in the workflow file.",
        ),
    )


def evaluate(duration: float, baselines: Sequence[float], thresholds: Thresholds, override: bool) -> Verdict:
    """Apply the decision rules in order: ceiling, cold start, relative check, label override."""
    ceiling = check_ceiling(duration, thresholds)
    if ceiling is not None:
        return ceiling
    if len(baselines) < thresholds.runs:
        return Verdict(
            True,
            (
                f"::notice::Cold start: {len(baselines)} of {thresholds.runs} main baseline records found, so the "
                f"relative check is skipped; only the {thresholds.ceiling:.0f} s ceiling applies "
                f"(job took {duration:.0f} s).",
            ),
        )
    median = float(statistics.median(baselines[: thresholds.runs]))
    limits = (
        f"job took {duration:.0f} s against a baseline median of {median:.0f} s over {thresholds.runs} main runs "
        f"(tolerance {thresholds.tolerance:.0%}, minimum delta {thresholds.min_delta:.0f} s)"
    )
    if not exceeds_relative(duration, median, thresholds):
        return Verdict(True, (f"Timing gate passed: {limits}.",))
    if override:
        return Verdict(True, (f"::warning::Timing regression accepted by the '{OVERRIDE_LABEL}' label: {limits}.",))
    return Verdict(
        False,
        (
            f"::error::Timing regression: {limits}. Speed the job up, or add the '{OVERRIDE_LABEL}' label to accept "
            "a deliberate slow-down.",
        ),
    )


def build_request(url: str, token: str) -> urllib.request.Request:
    """Return a GitHub API request; the token is unredirected, so it never reaches the artefact storage host."""
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    return request


def default_opener(request: urllib.request.Request) -> bytes:
    """Perform ``request`` with a bounded timeout and return the response body."""
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return response.read()


def read_record_zip(payload: bytes) -> TimingRecord:
    """Read ``record.json`` from an artefact zip archive held in memory."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return TimingRecord.from_json(json.loads(archive.read(RECORD_FILE)))


def is_main_artefact(item: dict[str, object]) -> bool:
    """Return true for an unexpired artefact from a ``main`` run of this repository, never from a fork's ``main``."""
    run = item.get("workflow_run") or {}
    if item.get("expired") or not isinstance(run, dict) or run.get("head_branch") != BASELINE_BRANCH:
        return False
    return run.get("head_repository_id") is not None and run.get("head_repository_id") == run.get("repository_id")


def fetch_baseline(
    repo: str,
    name: str,
    runs: int,
    token: str,
    opener: Callable[[urllib.request.Request], bytes] = default_opener,
) -> list[TimingRecord]:
    """Return up to ``runs`` records from unexpired ``main`` artefacts called ``name``, newest first."""
    url = f"{API_ROOT}/repos/{repo}/actions/artifacts?name={name}&per_page=100"
    listing = json.loads(opener(build_request(url, token)))
    candidates = [item for item in listing.get("artifacts", []) if is_main_artefact(item)]
    candidates.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return [read_record_zip(opener(build_request(item["archive_download_url"], token))) for item in candidates[:runs]]


# Every stdlib failure reachable from fetching and parsing the baseline: network and timeout (OSError),
# truncated or malformed HTTP responses (HTTPException), broken or truncated archives (BadZipFile, zlib.error,
# EOFError, RuntimeError for encrypted or unsupported members) and malformed JSON payloads (the rest).
FETCH_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    http.client.HTTPException,
    zipfile.BadZipFile,
    zlib.error,
    EOFError,
    RuntimeError,
    KeyError,
    ValueError,
    TypeError,
    AttributeError,
)


def safe_fetch(fetcher: Callable[[], list[TimingRecord]]) -> tuple[list[TimingRecord], list[str]]:
    """Run ``fetcher``; on any fetch or parse error return no records and a warning (cold start)."""
    try:
        return fetcher(), []
    except FETCH_ERRORS as exc:
        return [], [
            f"::warning::Could not fetch the timing baseline ({type(exc).__name__}: {exc}); treating as cold start."
        ]


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser."""
    parser = argparse.ArgumentParser(description="Record or compare CI job durations against main.")
    parser.add_argument("mode", choices=["record", "compare"], help="record on main, compare on pull requests")
    parser.add_argument("--workflow", required=True, help="workflow file stem, for example test-unit")
    parser.add_argument("--job", required=True, help="job id")
    parser.add_argument("--os", dest="os_name", required=True, help="runner OS label")
    parser.add_argument("--start", type=float, required=True, help="job start time in epoch seconds")
    parser.add_argument("--junit", default="", help="JUnit XML report with per-test durations")
    parser.add_argument("--output", default="", help="where to write the JSON record")
    parser.add_argument("--repo", default="", help="owner/name of the repository holding the baseline artefacts")
    parser.add_argument("--runs", type=int, default=5, help="baseline records required (default: 5)")
    parser.add_argument("--tolerance", type=float, default=0.5, help="relative tolerance (default: 0.5)")
    parser.add_argument("--min-delta", type=float, default=120.0, help="minimum absolute delta in s (default: 120)")
    parser.add_argument("--ceiling", type=float, default=600.0, help="absolute ceiling in s (default: 600)")
    parser.add_argument(
        "--override-label-present",
        choices=["true", "false"],
        default="false",
        help=f"whether the '{OVERRIDE_LABEL}' label is on the pull request",
    )
    return parser


def load_tests(path: str) -> tuple[tuple[dict[str, float], float | None, str], list[str]]:
    """Read the JUnit report at ``path``; a missing or broken report yields no tests, no test time and a warning."""
    if not path:
        return ({}, None, ""), []
    try:
        return read_junit(path), []
    except (OSError, ET.ParseError, ValueError) as exc:
        warning = f"::warning::Could not read the JUnit report {path} ({exc}); per-test data is skipped."
        return ({}, None, ""), [warning]


def measure(args: argparse.Namespace, clock: Callable[[], float]) -> tuple[TimingRecord, list[str]]:
    """Build the current job's record and any warnings raised while reading its JUnit report."""
    (tests, test_seconds, source), warnings = load_tests(args.junit)
    record = TimingRecord(
        job=args.job,
        os=args.os_name,
        workflow=args.workflow,
        duration=round(max(0.0, clock() - args.start), 1),
        commit=os.environ.get("GITHUB_SHA", ""),
        run_id=os.environ.get("GITHUB_RUN_ID", ""),
        tests=tests,
        test_seconds=None if test_seconds is None else round(test_seconds, 1),
        test_seconds_source=source,
    )
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(record.to_json(), handle, indent=2, sort_keys=True)
    return record, warnings


def thresholds_from(args: argparse.Namespace) -> Thresholds:
    """Return the thresholds given on the command line."""
    return Thresholds(runs=args.runs, tolerance=args.tolerance, min_delta=args.min_delta, ceiling=args.ceiling)


def run_record(args: argparse.Namespace, clock: Callable[[], float]) -> Verdict:
    """Write the record for this job and enforce the ceiling on its test time, or on wall clock without one."""
    record, warnings = measure(args, clock)
    summary = (
        f"Recorded {artefact_name(record.workflow, record.job, record.os)}: {record.duration:.0f} s, "
        f"{len(record.tests)} tests."
    )
    seconds, kind = record.judged()
    if kind:
        summary += f" The {took(seconds, kind)}."
    verdict = check_ceiling(seconds, thresholds_from(args), kind) or Verdict(True, ())
    return Verdict(verdict.passed, (*warnings, summary, *verdict.lines))


def write_step_summary(lines: Sequence[str]) -> None:
    """Append ``lines`` to the job summary when ``GITHUB_STEP_SUMMARY`` is set."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def run_compare(
    args: argparse.Namespace,
    clock: Callable[[], float],
    fetcher: Callable[[str, str, int], list[TimingRecord]],
) -> Verdict:
    """Compare this job with its ``main`` baseline and return the verdict, with the per-test report after its lines."""
    record, warnings = measure(args, clock)
    thresholds = thresholds_from(args)
    name = artefact_name(record.workflow, record.job, record.os)
    if args.repo:
        baselines, fetch_warnings = safe_fetch(lambda: fetcher(args.repo, name, thresholds.runs))
    else:
        baselines, fetch_warnings = [], ["::warning::No --repo given, so no baseline was fetched."]
    verdict = evaluate(
        record.duration,
        [b.duration for b in baselines],
        thresholds,
        args.override_label_present == "true",
    )
    report = per_test_report(record.tests, [b.tests for b in baselines]) if args.junit else []
    write_step_summary([f"### Timing gate: {name}", "", *(line.split("::")[-1] for line in verdict.lines), "", *report])
    return Verdict(verdict.passed, (*warnings, *fetch_warnings, *verdict.lines, *report))


def github_fetcher(repo: str, name: str, runs: int) -> list[TimingRecord]:
    """Fetch baseline records from the GitHub API with the token in ``GITHUB_TOKEN``."""
    return fetch_baseline(repo, name, runs, os.environ.get("GITHUB_TOKEN", ""))


def main(
    argv: Sequence[str] | None = None,
    clock: Callable[[], float] = time.time,
    fetcher: Callable[[str, str, int], list[TimingRecord]] = github_fetcher,
) -> int:
    """Run the gate; return 0 on pass, 1 on gate failure and 2 on bad input."""
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    verdict = run_record(args, clock) if args.mode == "record" else run_compare(args, clock, fetcher)
    for line in verdict.lines:
        print(line)
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
