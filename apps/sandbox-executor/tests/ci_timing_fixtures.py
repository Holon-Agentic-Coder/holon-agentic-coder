"""Shared fakes for the tests of ``scripts/ci_timing_gate.py``: clocks, fetchers, records, reports and artefacts."""

from __future__ import annotations

import io
import json
import urllib.error
import zipfile
from pathlib import Path

import pytest

from scripts.ci_timing_gate import TimingRecord

START = 1_000_000.0


def record(duration: float, tests: dict[str, float] | None = None) -> TimingRecord:
    """Build a baseline record for the unit job."""
    return TimingRecord(job="unit", os="ubuntu-latest", workflow="test-unit", duration=duration, tests=tests or {})


def fixed_clock(duration: float):
    """Return a clock that reads ``duration`` seconds after ``START``."""
    return lambda: START + duration


def fake_fetcher(records: list[TimingRecord]):
    """Return a fetcher that serves ``records`` and remembers what it was asked for."""
    calls: list[tuple[str, str, int]] = []

    def fetch(repo: str, name: str, runs: int) -> list[TimingRecord]:
        calls.append((repo, name, runs))
        return records[:runs]

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def failing_fetcher(repo: str, name: str, runs: int) -> list[TimingRecord]:
    """A fetcher whose request always fails."""
    raise urllib.error.URLError("network unreachable")


def gate_args(mode: str, *extra: str) -> list[str]:
    """Return the command line the unit workflow uses, plus ``extra`` flags."""
    return [
        mode,
        "--workflow",
        "test-unit",
        "--job",
        "unit",
        "--os",
        "ubuntu-latest",
        "--start",
        str(START),
        "--repo",
        "owner/repo",
        *extra,
    ]


def clear_github_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the job summary and run metadata out of a test unless the test sets them."""
    for name in ("GITHUB_STEP_SUMMARY", "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def write_junit(path: Path, cases: dict[str, float], suite_time: float | None = None) -> None:
    """Write a minimal JUnit report with ``classname::name`` cases, and the suite's ``time`` when one is given."""
    body = "".join(
        f'<testcase classname="{test_id.split("::")[0]}" name="{test_id.split("::")[1]}" time="{t}"/>'
        for test_id, t in cases.items()
    )
    time_attribute = "" if suite_time is None else f' time="{suite_time}"'
    path.write_text(f'<testsuites><testsuite name="pytest"{time_attribute}>{body}</testsuite></testsuites>')


def zipped(rec: TimingRecord) -> bytes:
    """Return an in-memory artefact archive holding ``rec`` as record.json."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("record.json", json.dumps(rec.to_json()))
    return buffer.getvalue()


def artefact(index: int, branch: str = "main", expired: bool = False, head_repo: int = 1) -> dict[str, object]:
    """Return one artefact listing entry; ``head_repo`` other than 1 marks a run from a fork."""
    return {
        "id": index,
        "expired": expired,
        "created_at": f"2026-10-{index:02d}T00:00:00Z",
        "archive_download_url": f"https://api.github.com/download/{index}",
        "workflow_run": {"head_branch": branch, "repository_id": 1, "head_repository_id": head_repo},
    }
