"""Unit tests for the record format of scripts/ci_timing_gate.py: the test time keys next to the original seven."""

from __future__ import annotations

import io
import json
import urllib.request
import zipfile
from pathlib import Path

import pytest
from sandbox_executor.flow.github.ci_slowest_tests import durations_from_records

from scripts.ci_timing_gate import TimingRecord, fetch_baseline, main, safe_fetch
from tests.ci_timing_fixtures import (
    artefact,
    clear_github_environment,
    failing_fetcher,
    fixed_clock,
    gate_args,
    record,
    write_junit,
    zipped,
)


@pytest.fixture(autouse=True)
def no_step_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the job summary and run metadata out of every test unless a test sets them."""
    clear_github_environment(monkeypatch)


# The record format: two optional keys for the test time next to the seven original keys

ORIGINAL_KEYS = ("job", "os", "workflow", "duration_seconds", "commit", "run_id", "tests")


def written_record(tmp_path: Path, duration: float, *extra: str) -> tuple[int, dict[str, object]]:
    """Run ``record`` mode at ``duration`` seconds of wall clock and return the exit code and the written record."""
    output = tmp_path / "ci-timing" / "record.json"
    code = main(
        gate_args("record", "--output", str(output), *extra), clock=fixed_clock(duration), fetcher=failing_fetcher
    )
    return code, json.loads(output.read_text())


def original_reader(data: dict[str, object]) -> TimingRecord:
    """Read a record as the gate did before the test time keys existed: the seven original keys and nothing else."""
    tests = data.get("tests") or {}
    assert isinstance(tests, dict)
    return TimingRecord(
        job=str(data["job"]),
        os=str(data["os"]),
        workflow=str(data["workflow"]),
        duration=float(data["duration_seconds"]),  # type: ignore[arg-type]
        commit=str(data.get("commit", "")),
        run_id=str(data.get("run_id", "")),
        tests={str(k): float(v) for k, v in tests.items()},
    )


def test_record_with_a_suite_time_adds_the_two_keys_and_keeps_the_original_seven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_SHA", "cc4bf1b")
    monkeypatch.setenv("GITHUB_RUN_ID", "40812897144")
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, {"tests.m::fast": 0.5, "tests.m::slow": 140.0}, suite_time=118.26)
    code, data = written_record(tmp_path, 132.0, "--junit", str(junit))
    assert code == 0
    assert data == {
        "job": "unit",
        "os": "ubuntu-latest",
        "workflow": "test-unit",
        "duration_seconds": 132.0,
        "commit": "cc4bf1b",
        "run_id": "40812897144",
        "tests": {"tests.m::fast": 0.5, "tests.m::slow": 140.0},
        "test_seconds": 118.3,
        "test_seconds_source": "suite",
    }
    out = capsys.readouterr().out
    assert out == "Recorded ci-timing-test-unit-unit-ubuntu-latest: 132 s, 2 tests. The suite test time was 118 s.\n"


def test_record_without_a_suite_time_stores_the_sum_of_the_test_cases(tmp_path: Path) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, {"tests.m::fast": 0.5, "tests.m::slow": 140.0})
    code, data = written_record(tmp_path, 132.0, "--junit", str(junit))
    assert code == 0
    assert (data["test_seconds"], data["test_seconds_source"]) == (140.5, "sum")
    assert data["duration_seconds"] == 132.0


def test_record_without_a_junit_report_writes_exactly_the_original_keys(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, data = written_record(tmp_path, 41.0)
    assert code == 0
    assert sorted(data) == sorted(ORIGINAL_KEYS)
    assert data["tests"] == {}
    assert capsys.readouterr().out == "Recorded ci-timing-test-unit-unit-ubuntu-latest: 41 s, 0 tests.\n"


def test_record_with_an_unreadable_junit_report_has_no_test_time(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, data = written_record(tmp_path, 41.0, "--junit", str(tmp_path / "missing.xml"))
    assert code == 0
    assert sorted(data) == sorted(ORIGINAL_KEYS)
    assert "::warning::Could not read the JUnit report" in capsys.readouterr().out


def test_record_in_the_original_shape_still_loads_and_fetches() -> None:
    old = record(229.0, {"tests.m::slow": 5.0})
    data = old.to_json()
    assert sorted(data) == sorted(ORIGINAL_KEYS)
    loaded = TimingRecord.from_json(data)
    assert loaded == old
    assert loaded.test_seconds is None
    assert loaded.test_seconds_source == ""
    assert TimingRecord.from_json({**data, "test_seconds": None}).test_seconds is None

    def opener(request: urllib.request.Request) -> bytes:
        if "artifacts?name=" in request.full_url:
            return json.dumps({"artifacts": [artefact(1)]}).encode()
        return zipped(old)

    assert fetch_baseline("owner/repo", "ci-timing-test-unit-unit-ubuntu-latest", 5, "", opener) == [old]


def test_record_with_test_time_round_trips_and_fetches() -> None:
    new = TimingRecord(
        job="unit",
        os="macos-latest",
        workflow="test-unit",
        duration=565.0,
        commit="27dd49af",
        run_id="41",
        tests={"tests.m::slow": 5.0},
        test_seconds=547.3,
        test_seconds_source="suite",
    )
    assert TimingRecord.from_json(json.loads(json.dumps(new.to_json()))) == new

    def opener(request: urllib.request.Request) -> bytes:
        if "artifacts?name=" in request.full_url:
            return json.dumps({"artifacts": [artefact(1)]}).encode()
        return zipped(new)

    assert fetch_baseline("owner/repo", "ci-timing-test-unit-unit-macos-latest", 5, "", opener) == [new]


def test_malformed_test_time_degrades_to_cold_start() -> None:
    data = {**record(229.0).to_json(), "test_seconds": "fast", "test_seconds_source": "suite"}
    with pytest.raises(ValueError, match="could not convert"):
        TimingRecord.from_json(data)

    def opener(request: urllib.request.Request) -> bytes:
        if "artifacts?name=" in request.full_url:
            return json.dumps({"artifacts": [artefact(1)]}).encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("record.json", json.dumps(data))
        return buffer.getvalue()

    records, warnings = safe_fetch(lambda: fetch_baseline("o/r", "n", 5, "", opener))
    assert records == []
    assert "ValueError" in warnings[0]
    assert "treating as cold start" in warnings[0]


def test_existing_readers_accept_a_record_with_test_time(tmp_path: Path) -> None:
    junit = tmp_path / "unit-report.xml"
    tests = {"tests.m::fast": 0.5, "tests.m::slow": 140.0}
    write_junit(junit, tests, suite_time=118.0)
    code, data = written_record(tmp_path, 132.0, "--junit", str(junit))
    assert code == 0
    assert set(data) == {*ORIGINAL_KEYS, "test_seconds", "test_seconds_source"}
    # The gate on a branch without this change reads the seven keys it knows and ignores the rest.
    assert original_reader(data) == record(132.0, tests)
    # The flow's slowest-tests lookup reads the per-test times from the downloaded artefact directory.
    assert durations_from_records(tmp_path, "unit (ubuntu-latest)") == tests


def test_record_ceiling_applies_to_test_time_not_to_wall_clock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, {"tests.m::slow": 140.0}, suite_time=400.0)
    assert main(gate_args("record", "--junit", str(junit)), clock=fixed_clock(700.0), fetcher=failing_fetcher) == 0
    out = capsys.readouterr().out
    assert out == "Recorded ci-timing-test-unit-unit-ubuntu-latest: 700 s, 1 tests. The suite test time was 400 s.\n"

    write_junit(junit, {"tests.m::slow": 140.0}, suite_time=640.0)
    assert main(gate_args("record", "--junit", str(junit)), clock=fixed_clock(700.0), fetcher=failing_fetcher) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert lines[1].startswith("::error::Suite test time was 640 s, above the 600 s ceiling. ")
    assert "ceiling cannot be overridden" in lines[1]
    assert "--ceiling" in lines[1]


def test_record_ceiling_message_on_wall_clock_is_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(gate_args("record"), clock=fixed_clock(682.0), fetcher=failing_fetcher) == 1
    assert capsys.readouterr().out.splitlines()[1] == (
        "::error::Job took 682 s, above the 600 s ceiling. The ceiling cannot be overridden by the "
        "'timing-regression-accepted' label; it changes only through a committed edit to the --ceiling flag in the "
        "workflow file."
    )
