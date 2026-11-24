"""Unit tests for scripts/ci_timing_gate.py, using fake clocks, fetchers and openers only (never the network)."""

from __future__ import annotations

import http.client
import io
import json
import re
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path
from typing import NamedTuple

import pytest
from sandbox_executor.flow.github.ci_slowest_tests import durations_from_records

from scripts.check_ci_runtime_budget import main as budget_main
from scripts.ci_timing_gate import (
    OVERRIDE_LABEL,
    Thresholds,
    TimingRecord,
    artefact_name,
    build_parser,
    evaluate,
    exceeds_relative,
    fetch_baseline,
    main,
    parse_junit,
    read_junit,
    rolling_median,
    safe_fetch,
)

DEFAULTS = Thresholds()
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


@pytest.fixture(autouse=True)
def no_step_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the job summary and run metadata out of every test unless a test sets them."""
    for name in ("GITHUB_STEP_SUMMARY", "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)


# Rolling median and the relative check


def test_rolling_median_odd_even_and_empty() -> None:
    assert rolling_median([3.0, 1.0, 2.0]) == 2.0
    assert rolling_median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert rolling_median([]) is None


def test_artefact_name_includes_workflow_job_and_os() -> None:
    assert artefact_name("make", "build", "macos-latest") == "ci-timing-make-build-macos-latest"


def test_pass_inside_tolerance() -> None:
    verdict = evaluate(140.0, [100.0] * 5, DEFAULTS, override=False)
    assert verdict.passed
    assert "Timing gate passed" in verdict.lines[0]


def test_tolerance_exceeded_but_not_minimum_delta_passes() -> None:
    assert not exceeds_relative(200.0, 100.0, DEFAULTS)
    assert evaluate(200.0, [100.0] * 5, DEFAULTS, override=False).passed


def test_minimum_delta_exceeded_but_not_tolerance_passes() -> None:
    assert not exceeds_relative(450.0, 320.0, DEFAULTS)
    assert evaluate(450.0, [320.0] * 5, DEFAULTS, override=False).passed


def test_fails_only_when_tolerance_and_delta_both_exceeded() -> None:
    assert exceeds_relative(260.0, 100.0, DEFAULTS)
    verdict = evaluate(260.0, [100.0] * 5, DEFAULTS, override=False)
    assert not verdict.passed
    assert verdict.lines[0].startswith("::error::Timing regression")
    assert OVERRIDE_LABEL in verdict.lines[0]


def test_only_the_last_n_records_form_the_median() -> None:
    assert evaluate(260.0, [100.0] * 5 + [1000.0], DEFAULTS, override=False).passed is False


# Ceiling


@pytest.mark.parametrize("baselines", [[590.0] * 5, [], [100.0]])
@pytest.mark.parametrize("override", [False, True])
def test_ceiling_fails_whatever_the_baseline_or_label(baselines: list[float], override: bool) -> None:
    verdict = evaluate(601.0, baselines, DEFAULTS, override=override)
    assert not verdict.passed
    assert "ceiling cannot be overridden" in verdict.lines[0]
    assert "--ceiling" in verdict.lines[0]


def test_exactly_at_ceiling_passes() -> None:
    assert evaluate(600.0, [], DEFAULTS, override=False).passed


# Cold start


@pytest.mark.parametrize("count", [0, 1, 4])
def test_cold_start_skips_relative_check_with_notice(count: int) -> None:
    verdict = evaluate(599.0, [10.0] * count, DEFAULTS, override=False)
    assert verdict.passed
    assert verdict.lines[0].startswith("::notice::Cold start")
    assert f"{count} of 5" in verdict.lines[0]


# Label override


def test_override_turns_relative_failure_into_warning_naming_label() -> None:
    verdict = evaluate(260.0, [100.0] * 5, DEFAULTS, override=True)
    assert verdict.passed
    assert verdict.lines[0].startswith("::warning::")
    assert OVERRIDE_LABEL in verdict.lines[0]


def test_main_override_flag_passes_relative_regression(capsys: pytest.CaptureFixture[str]) -> None:
    fetch = fake_fetcher([record(100.0)] * 5)
    args = gate_args("compare", "--override-label-present", "true")
    assert main(args, clock=fixed_clock(260.0), fetcher=fetch) == 0
    assert f"::warning::Timing regression accepted by the '{OVERRIDE_LABEL}' label" in capsys.readouterr().out


# Per-test top-offender report


def write_junit(path: Path, cases: dict[str, float], suite_time: float | None = None) -> None:
    """Write a minimal JUnit report with ``classname::name`` cases, and the suite's ``time`` when one is given."""
    body = "".join(
        f'<testcase classname="{test_id.split("::")[0]}" name="{test_id.split("::")[1]}" time="{t}"/>'
        for test_id, t in cases.items()
    )
    time_attribute = "" if suite_time is None else f' time="{suite_time}"'
    path.write_text(f'<testsuites><testsuite name="pytest"{time_attribute}>{body}</testsuite></testsuites>')


def test_report_does_not_fail_on_its_own(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, {"tests.m::slow": 60.0})
    fetch = fake_fetcher([record(100.0, {"tests.m::slow": 5.0})] * 5)
    assert main(gate_args("compare", "--junit", str(junit)), clock=fixed_clock(110.0), fetcher=fetch) == 0
    out = capsys.readouterr().out
    assert "| `tests.m::slow` | 5.0 | 60.0 | +55.0 |" in out
    assert "Timing gate passed" in out


def test_report_appended_to_failure_and_step_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, {"tests.m::slow": 160.0})
    fetch = fake_fetcher([record(100.0, {"tests.m::slow": 5.0})] * 5)
    assert main(gate_args("compare", "--junit", str(junit)), clock=fixed_clock(400.0), fetcher=fetch) == 1
    out = capsys.readouterr().out
    error_at = out.index("::error::Timing regression")
    assert out.index("| `tests.m::slow`", error_at) > error_at
    assert out.count("| `tests.m::slow`") == 1
    text = summary.read_text()
    assert "### Timing gate: ci-timing-test-unit-unit-ubuntu-latest" in text
    assert "`tests.m::slow`" in text


# JUnit parsing


def test_parse_junit_nested_missing_time_and_duplicate_names(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    report.write_text(
        "<testsuites><testsuite name='outer'><testsuite name='inner'>"
        "<testcase classname='pkg.a' name='test_x' time='1.5'/>"
        "<testcase classname='pkg.b' name='test_x' time='2.25'/>"
        "<testcase classname='pkg.c' name='test_y'/>"
        "<testcase classname='pkg.d' name='test_z' time=''/>"
        "</testsuite></testsuite></testsuites>"
    )
    assert parse_junit(str(report)) == {
        "pkg.a::test_x": 1.5,
        "pkg.b::test_x": 2.25,
        "pkg.c::test_y": 0.0,
        "pkg.d::test_z": 0.0,
    }


# Test time: the suite time of the JUnit report, or the sum of its test case times


def test_read_junit_prefers_the_suite_time_over_the_sum(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    write_junit(report, {"tests.m::a": 30.0, "tests.m::b": 50.0}, suite_time=41.5)
    assert read_junit(str(report)) == ({"tests.m::a": 30.0, "tests.m::b": 50.0}, 41.5, "suite")
    assert parse_junit(str(report)) == {"tests.m::a": 30.0, "tests.m::b": 50.0}


def test_read_junit_sums_the_test_cases_without_a_suite_time(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    write_junit(report, {"tests.m::a": 30.0, "tests.m::b": 50.0})
    assert read_junit(str(report)) == ({"tests.m::a": 30.0, "tests.m::b": 50.0}, 80.0, "sum")


@pytest.mark.parametrize("suite_time", ["0", "0.000", "", "-3", "nan", "inf", "fast", "1e3"])
def test_read_junit_ignores_a_suite_time_that_is_not_a_positive_decimal(tmp_path: Path, suite_time: str) -> None:
    report = tmp_path / "report.xml"
    report.write_text(f"<testsuite time='{suite_time}'><testcase classname='m' name='a' time='2.5'/></testsuite>")
    assert read_junit(str(report)) == ({"m::a": 2.5}, 2.5, "sum")


def test_read_junit_sums_top_level_suites_and_never_counts_nested_ones_twice(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    report.write_text(
        "<testsuites>"
        "<testsuite name='first' time='10.5'><testsuite name='inner' time='9.0'>"
        "<testcase classname='m' name='a' time='4.0'/></testsuite></testsuite>"
        "<testsuite name='second' time='20.25'><testcase classname='m' name='b' time='7.0'/></testsuite>"
        "</testsuites>"
    )
    assert read_junit(str(report)) == ({"m::a": 4.0, "m::b": 7.0}, 30.75, "suite")
    # One top-level suite without a time: the suite time is incomplete, so the test cases are summed instead.
    report.write_text(report.read_text().replace(" time='20.25'", ""))
    assert read_junit(str(report)) == ({"m::a": 4.0, "m::b": 7.0}, 11.0, "sum")


def test_read_junit_takes_a_testsuite_root_as_the_top_level_suite(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    report.write_text(
        "<testsuite time='12.0'><testsuite time='99.0'><testcase classname='m' name='a'/></testsuite></testsuite>"
    )
    assert read_junit(str(report)) == ({"m::a": 0.0}, 12.0, "suite")


def test_read_junit_without_test_cases_or_suite_time_has_no_test_time(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    report.write_text("<testsuites><testsuite name='pytest'/></testsuites>")
    assert read_junit(str(report)) == ({}, None, "")
    report.write_text("<testsuites/>")
    assert read_junit(str(report)) == ({}, None, "")


# Baseline fetch through a fake opener


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


def test_fetch_baseline_filters_limits_and_keeps_token_unredirected() -> None:
    entries = [artefact(i) for i in range(1, 8)]
    entries += [artefact(20, branch="feature"), artefact(21, expired=True), artefact(22, head_repo=2)]
    requests: list[urllib.request.Request] = []

    def opener(request: urllib.request.Request) -> bytes:
        requests.append(request)
        if "artifacts?name=" in request.full_url:
            return json.dumps({"artifacts": entries}).encode()
        index = int(request.full_url.rsplit("/", 1)[1])
        return zipped(record(float(index)))

    records = fetch_baseline("owner/repo", "ci-timing-test-unit-unit-ubuntu-latest", 5, "secret-value", opener)
    assert [r.duration for r in records] == [7.0, 6.0, 5.0, 4.0, 3.0]
    assert "name=ci-timing-test-unit-unit-ubuntu-latest" in requests[0].full_url
    for request in requests:
        assert request.unredirected_hdrs["Authorization"] == "Bearer secret-value"
        assert "Authorization" not in request.headers


def test_fetch_error_degrades_to_cold_start_with_warning(capsys: pytest.CaptureFixture[str]) -> None:
    records, warnings = safe_fetch(lambda: failing_fetcher("owner/repo", "name", 5))
    assert records == []
    assert warnings[0].startswith("::warning::Could not fetch the timing baseline")
    assert main(gate_args("compare"), clock=fixed_clock(500.0), fetcher=failing_fetcher) == 0
    out = capsys.readouterr().out
    assert "::warning::Could not fetch" in out
    assert "::notice::Cold start" in out


def test_bad_archive_degrades_to_cold_start() -> None:
    def opener(request: urllib.request.Request) -> bytes:
        if "artifacts?name=" in request.full_url:
            return json.dumps({"artifacts": [artefact(1)]}).encode()
        return b"not a zip"

    records, warnings = safe_fetch(lambda: fetch_baseline("o/r", "n", 5, "", opener))
    assert records == []
    assert "BadZipFile" in warnings[0]


def test_non_object_listing_degrades_to_cold_start() -> None:
    def opener(request: urllib.request.Request) -> bytes:
        return json.dumps([]).encode()

    records, warnings = safe_fetch(lambda: fetch_baseline("o/r", "n", 5, "", opener))
    assert records == []
    assert "AttributeError" in warnings[0]


@pytest.mark.parametrize(
    "error",
    [
        http.client.IncompleteRead(b"partial"),
        http.client.BadStatusLine("garbage"),
        zlib.error("invalid stored block lengths"),
        EOFError(),
        RuntimeError("File is encrypted"),
        TimeoutError("timed out"),
    ],
)
def test_download_error_degrades_to_cold_start(error: BaseException) -> None:
    def opener(request: urllib.request.Request) -> bytes:
        if "artifacts?name=" in request.full_url:
            return json.dumps({"artifacts": [artefact(1)]}).encode()
        raise error

    records, warnings = safe_fetch(lambda: fetch_baseline("o/r", "n", 5, "", opener))
    assert records == []
    assert type(error).__name__ in warnings[0]
    assert "treating as cold start" in warnings[0]


def test_cold_start_compare_reports_per_test_skipped(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, {"tests.m::slow": 60.0})
    assert main(gate_args("compare", "--junit", str(junit)), clock=fixed_clock(110.0), fetcher=fake_fetcher([])) == 0
    out = capsys.readouterr().out
    assert "Per-test report: skipped" in out
    assert "no test grew" not in out


# Command line


def test_main_record_writes_record_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_SHA", "cc4bf1b")
    monkeypatch.setenv("GITHUB_RUN_ID", "40812897144")
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, {"tests.m::fast": 0.5})
    output = tmp_path / "ci-timing" / "record.json"
    args = gate_args("record", "--junit", str(junit), "--output", str(output))
    assert main(args, clock=fixed_clock(132.0), fetcher=failing_fetcher) == 0
    data = json.loads(output.read_text())
    assert data["duration_seconds"] == 132.0
    assert data["commit"] == "cc4bf1b"
    assert data["run_id"] == "40812897144"
    assert data["tests"] == {"tests.m::fast": 0.5}
    assert TimingRecord.from_json(data).job == "unit"
    assert "Recorded ci-timing-test-unit-unit-ubuntu-latest: 132 s, 1 tests." in capsys.readouterr().out


def test_main_record_enforces_ceiling() -> None:
    assert main(gate_args("record"), clock=fixed_clock(682.0), fetcher=failing_fetcher) == 1


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


def test_main_missing_or_bad_start_exits_two() -> None:
    args = ["record", "--workflow", "w", "--job", "j", "--os", "o"]
    assert main(args, clock=fixed_clock(1.0), fetcher=failing_fetcher) == 2
    assert main([*args, "--start", ""], clock=fixed_clock(1.0), fetcher=failing_fetcher) == 2


def test_compare_without_repo_is_cold_start(capsys: pytest.CaptureFixture[str]) -> None:
    args = gate_args("compare")
    args = args[: args.index("--repo")]
    fetch = fake_fetcher([record(1.0)] * 5)
    assert main(args, clock=fixed_clock(300.0), fetcher=fetch) == 0
    assert fetch.calls == []  # type: ignore[attr-defined]
    assert "::notice::Cold start" in capsys.readouterr().out


# Compare mode judges test time, and tells runner noise from a regression with the per-test times

LABEL_SENTENCE = (
    "The ceiling cannot be overridden by the 'timing-regression-accepted' label; it changes only through a committed "
    "edit to the --ceiling flag in the workflow file."
)
ADVICE = "Speed the job up, or add the 'timing-regression-accepted' label to accept a deliberate slow-down."
THRESHOLDS = "(tolerance 50%, minimum delta 120 s)"
BASIS = "Evidence against each test's median over the main baseline records (5 with per-test times): "
SPREAD = (
    "The breach is forgiven and the gate passes, because the slowdown is spread across the suite and not concentrated "
    "in a few tests."
)
NO_SUITE_TIME = (
    "5 of 5 baseline records carry no suite test time, so only the job's wall clock can be compared with them"
)

# A deterministic suite of 60 tests from 0.2 s to 6.1 s (189 s summed), and one of 40 tests of 10 s each (400 s).
UNIT_SUITE = {f"tests.unit.test_mod_{i:02d}::test_case": round(0.2 + 0.1 * i, 1) for i in range(60)}
EVEN_SUITE = {f"tests.even.test_mod_{i:02d}::test_case": 10.0 for i in range(40)}


def slowed(tests: dict[str, float], factor: float) -> dict[str, float]:
    """Return ``tests`` with every time multiplied by ``factor``, as a contended runner produces them."""
    return {test_id: seconds * factor for test_id, seconds in tests.items()}


def measured(wall: float, test_seconds: float, tests: dict[str, float], kind: str = "suite") -> TimingRecord:
    """Build a baseline record in the new shape: wall clock, test time of ``kind`` and per-test times."""
    return TimingRecord(
        job="unit",
        os="ubuntu-latest",
        workflow="test-unit",
        duration=wall,
        tests=tests,
        test_seconds=test_seconds,
        test_seconds_source=kind,
    )


def compared(
    capsys: pytest.CaptureFixture[str], junit: Path, wall: float, baselines: list[TimingRecord], *extra: str
) -> tuple[int, str]:
    """Run compare mode as the unit workflow does, ``wall`` seconds into the job; return the exit code and the verdict.

    Asserts the one-line contract on the way: the verdict is the first line, and only the per-test report follows it.
    """
    args = gate_args("compare", "--runs", "5", "--tolerance", "0.5", "--ceiling", "600", "--junit", str(junit), *extra)
    code = main(args, clock=fixed_clock(wall), fetcher=fake_fetcher(baselines))
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].startswith("Per-test report:")
    assert not [line for line in lines[1:] if line.startswith("::") or "Timing" in line or "Runner variance" in line]
    return code, lines[0]


def named_tests(line: str) -> list[str]:
    """Return the test ids a verdict line names, in order."""
    return re.findall(r"`([^`]+)`", line)


class NoisyRun(NamedTuple):
    """One failure of 2026-11-24 on a head whose code had not changed since a passing run.

    Test time is the job's wall clock less the overhead seen in the real runs (18 s on macOS, 9 s on Ubuntu); the macOS
    baseline is the 565 s of ``main``. ``breach`` words the limit the run breached on test time, ``old_breach`` the one
    it breaches while the baseline records still carry no test time.
    """

    os_name: str
    min_delta: str
    wall: float
    test_seconds: float
    baseline_wall: float
    baseline_test_seconds: float
    factor: float
    breach: str
    old_breach: str


def macos_run(wall: float, factor: float) -> NoisyRun:
    """A macOS unit job above the 600 s ceiling."""
    breach = f"suite test time was {wall - 18:.0f} s, above the 600 s ceiling"
    return NoisyRun(
        "macos-latest", "150", wall, wall - 18.0, 565.0, 547.0, factor, breach, f"{breach}; {NO_SUITE_TIME}"
    )


NOISY_RUNS = [
    macos_run(663.0, 1.18),
    macos_run(648.0, 1.15),
    macos_run(632.0, 1.12),
    macos_run(744.0, 1.33),
    NoisyRun(
        "ubuntu-latest",
        "120",
        360.0,
        351.0,
        229.0,
        220.0,
        1.60,
        f"suite test time was 351 s against a baseline median of 220 s over 5 main runs {THRESHOLDS}",
        f"job took 360 s against a baseline median of 229 s over 5 main runs {THRESHOLDS}; {NO_SUITE_TIME}",
    ),
]
NOISY_RUN_IDS = [f"{run.os_name}-{run.wall:.0f}s" for run in NOISY_RUNS]


def spread_evidence(factor: float) -> str:
    """Word the start of the evidence of a uniform slowdown of ``UNIT_SUITE`` by ``factor``."""
    return (
        f"{BASIS}median per-test ratio {factor:.2f} over 60 comparable tests, 0 offenders, offender share 0.0% of the "
    )


@pytest.mark.parametrize("run", NOISY_RUNS, ids=NOISY_RUN_IDS)
def test_replayed_failure_passes_with_a_runner_variance_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], run: NoisyRun
) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, slowed(UNIT_SUITE, run.factor), suite_time=run.test_seconds)
    baselines = [measured(run.baseline_wall, run.baseline_test_seconds, UNIT_SUITE)] * 5
    code, line = compared(capsys, junit, run.wall, baselines, "--os", run.os_name, "--min-delta", run.min_delta)
    assert code == 0
    assert line.startswith(f"::warning::Runner variance: {run.breach}. {SPREAD} {spread_evidence(run.factor)}")
    assert line.endswith(" s per-test excess (concentrated from 50%).")
    assert "::error::" not in line


def test_replayed_ubuntu_failure_prints_the_whole_evidence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, slowed(UNIT_SUITE, 1.6), suite_time=351.0)
    code, line = compared(capsys, junit, 360.0, [measured(229.0, 220.0, UNIT_SUITE)] * 5, "--min-delta", "120")
    assert code == 0
    assert line == (
        "::warning::Runner variance: suite test time was 351 s against a baseline median of 220 s over 5 main runs "
        "(tolerance 50%, minimum delta 120 s). The breach is forgiven and the gate passes, because the slowdown is "
        "spread across the suite and not concentrated in a few tests. Evidence against each test's median over the "
        "main baseline records (5 with per-test times): median per-test ratio 1.60 over 60 comparable tests, "
        "0 offenders, offender share 0.0% of the 113.4 s per-test excess (concentrated from 50%)."
    )


@pytest.mark.parametrize("run", NOISY_RUNS, ids=NOISY_RUN_IDS)
def test_replayed_failure_passes_while_the_baseline_records_carry_no_test_time(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], run: NoisyRun
) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, slowed(UNIT_SUITE, run.factor), suite_time=run.test_seconds)
    baselines = [record(run.baseline_wall, UNIT_SUITE)] * 5
    code, line = compared(capsys, junit, run.wall, baselines, "--os", run.os_name, "--min-delta", run.min_delta)
    assert code == 0
    assert line.startswith(f"::warning::Runner variance: {run.old_breach}. {SPREAD} {spread_evidence(run.factor)}")
    assert "::error::" not in line


SLOWED_FOUR = dict.fromkeys(list(UNIT_SUITE)[:4], 45.0)
CONCENTRATED = (
    f"suite test time was 398 s against a baseline median of 220 s over 5 main runs {THRESHOLDS}",
    f"The slowdown is concentrated in a few tests. {BASIS}median per-test ratio 1.00 over 60 comparable tests, "
    "4 offenders, offender share 100.0% of the 178.6 s per-test excess (concentrated from 50%). Offenders: "
    "`tests.unit.test_mod_00::test_case` 0.2 s -> 45.0 s, `tests.unit.test_mod_01::test_case` 0.3 s -> 45.0 s, "
    "`tests.unit.test_mod_02::test_case` 0.4 s -> 45.0 s, `tests.unit.test_mod_03::test_case` 0.5 s -> 45.0 s.",
)


def test_concentrated_slowdown_fails_and_names_exactly_the_slowed_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, {**UNIT_SUITE, **SLOWED_FOUR}, suite_time=398.0)
    code, line = compared(capsys, junit, 407.0, [measured(229.0, 220.0, UNIT_SUITE)] * 5, "--min-delta", "120")
    assert code == 1
    limits, reason = CONCENTRATED
    assert line == f"::error::Timing regression: {limits}. {ADVICE} {reason}"
    assert named_tests(line) == list(SLOWED_FOUR)
    # The job summary shows the same line without the workflow command, test ids intact.
    assert f"\n\nTiming regression: {limits}. {ADVICE} {reason}\n\nPer-test report:" in summary.read_text()


def test_label_accepts_a_concentrated_relative_slowdown_and_still_names_the_tests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, {**UNIT_SUITE, **SLOWED_FOUR}, suite_time=398.0)
    baselines = [measured(229.0, 220.0, UNIT_SUITE)] * 5
    code, line = compared(capsys, junit, 407.0, baselines, "--min-delta", "120", "--override-label-present", "true")
    assert code == 0
    limits, reason = CONCENTRATED
    assert (
        line == f"::warning::Timing regression accepted by the 'timing-regression-accepted' label: {limits}. {reason}"
    )
    assert named_tests(line) == list(SLOWED_FOUR)


@pytest.mark.parametrize("label", ["false", "true"])
def test_summed_test_time_above_the_ceiling_fails_a_concentrated_slowdown_whatever_the_label(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], label: str
) -> None:
    grown = dict.fromkeys(list(EVEN_SUITE)[:3], 90.0)
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, {**EVEN_SUITE, **grown})
    baselines = [measured(420.0, 400.0, EVEN_SUITE, "sum")] * 5
    code, line = compared(capsys, junit, 660.0, baselines, "--override-label-present", label)
    assert code == 1
    assert line == (
        f"::error::Summed test time was 640 s, above the 600 s ceiling. {LABEL_SENTENCE} The slowdown is concentrated "
        f"in a few tests. {BASIS}median per-test ratio 1.00 over 40 comparable tests, 3 offenders, offender share "
        "100.0% of the 240.0 s per-test excess (concentrated from 50%). Offenders: "
        "`tests.even.test_mod_00::test_case` 10.0 s -> 90.0 s, `tests.even.test_mod_01::test_case` 10.0 s -> 90.0 s, "
        "`tests.even.test_mod_02::test_case` 10.0 s -> 90.0 s."
    )
    assert named_tests(line) == list(grown)


def test_wall_clock_above_the_ceiling_passes_when_the_summed_test_time_is_under_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, EVEN_SUITE)
    code, line = compared(capsys, junit, 700.0, [measured(420.0, 400.0, EVEN_SUITE, "sum")] * 5)
    assert code == 0
    assert line == (
        "Timing gate passed: summed test time was 400 s against a baseline median of 400 s over 5 main runs "
        "(tolerance 50%, minimum delta 120 s)."
    )


def mixed_baselines(tests: dict[str, float]) -> list[TimingRecord]:
    """Five baseline records, newest first, of which the second and the fourth are in the shape without test time."""
    walls = (229.0, 200.0, 250.0, 210.0, 260.0)
    return [
        record(wall, tests) if index in (1, 3) else measured(wall, wall - 9, tests) for index, wall in enumerate(walls)
    ]


def test_old_baseline_records_are_read_and_wall_clock_is_compared_inside_tolerance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, UNIT_SUITE, suite_time=231.0)
    code, line = compared(capsys, junit, 240.0, mixed_baselines(UNIT_SUITE))
    assert code == 0
    # The median of all five wall clocks is 229 s; the three records with test time alone would give 250 s.
    assert line == (
        f"Timing gate passed: job took 240 s against a baseline median of 229 s over 5 main runs {THRESHOLDS}; 2 of 5 "
        "baseline records carry no suite test time, so only the job's wall clock can be compared with them."
    )


def test_old_baseline_records_outside_tolerance_fail_when_too_few_tests_are_comparable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, slowed(UNIT_SUITE, 1.7), suite_time=391.0)
    three_tests = dict(list(UNIT_SUITE.items())[:3])
    code, line = compared(capsys, junit, 400.0, mixed_baselines(three_tests))
    assert code == 1
    assert line == (
        f"::error::Timing regression: job took 400 s against a baseline median of 229 s over 5 main runs {THRESHOLDS}; "
        "2 of 5 baseline records carry no suite test time, so only the job's wall clock can be compared with them. "
        f"{ADVICE} Runner variance could not be assessed: only 3 comparable tests, fewer than 20."
    )


def test_suite_time_and_summed_time_are_never_compared_with_each_other(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, UNIT_SUITE)
    # The baseline suite time of 20 s would fail the run's 189 s of summed test time; the wall clocks are close.
    code, line = compared(capsys, junit, 240.0, [measured(229.0, 20.0, UNIT_SUITE)] * 5)
    assert code == 0
    assert line == (
        f"Timing gate passed: job took 240 s against a baseline median of 229 s over 5 main runs {THRESHOLDS}; 5 of 5 "
        "baseline records carry no summed test time, so only the job's wall clock can be compared with them."
    )


@pytest.mark.parametrize(
    ("wall", "test_seconds", "plain"),
    [
        (360.0, 351.0, f"::error::Timing regression: {NOISY_RUNS[4].breach}. {ADVICE}"),
        (663.0, 645.0, f"::error::Suite test time was 645 s, above the 600 s ceiling. {LABEL_SENTENCE}"),
    ],
)
def test_breach_without_baseline_per_test_times_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], wall: float, test_seconds: float, plain: str
) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, slowed(UNIT_SUITE, 1.6), suite_time=test_seconds)
    code, line = compared(capsys, junit, wall, [measured(229.0, 220.0, {})] * 5)
    assert code == 1
    assert line == f"{plain} Runner variance could not be assessed: no baseline per-test times."


def test_cold_start_with_test_time_under_the_ceiling_passes_whatever_the_wall_clock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, slowed(UNIT_SUITE, 1.6), suite_time=351.0)
    code, line = compared(capsys, junit, 650.0, [measured(229.0, 220.0, UNIT_SUITE)] * 4)
    assert code == 0
    assert line == (
        "::notice::Cold start: 4 of 5 main baseline records found, so the relative check is skipped; only the 600 s "
        "ceiling applies (suite test time was 351 s)."
    )


@pytest.mark.parametrize("baselines", [[], [measured(565.0, 547.0, {})] * 3, [record(565.0)] * 3])
def test_cold_start_with_test_time_above_the_ceiling_and_no_per_test_baseline_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], baselines: list[TimingRecord]
) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, slowed(UNIT_SUITE, 1.18), suite_time=645.0)
    code, line = compared(capsys, junit, 663.0, baselines)
    assert code == 1
    assert line.startswith("::error::Suite test time was 645 s, above the 600 s ceiling")
    assert line.endswith(f". {LABEL_SENTENCE} Runner variance could not be assessed: no baseline per-test times.")


def test_compare_without_a_junit_report_keeps_the_wall_clock_wording(capsys: pytest.CaptureFixture[str]) -> None:
    fetch = fake_fetcher([measured(229.0, 220.0, UNIT_SUITE)] * 5)
    assert main(gate_args("compare"), clock=fixed_clock(240.0), fetcher=fetch) == 0
    assert capsys.readouterr().out == (
        f"Timing gate passed: job took 240 s against a baseline median of 229 s over 5 main runs {THRESHOLDS}.\n"
    )
    assert main(gate_args("compare"), clock=fixed_clock(400.0), fetcher=fetch) == 1
    assert capsys.readouterr().out == (
        f"::error::Timing regression: job took 400 s against a baseline median of 229 s over 5 main runs {THRESHOLDS}. "
        f"{ADVICE}\n"
    )


def test_four_parameter_evaluate_keeps_the_wall_clock_wording() -> None:
    assert evaluate(601.0, [], DEFAULTS, override=True).lines == (
        f"::error::Job took 601 s, above the 600 s ceiling. {LABEL_SENTENCE}",
    )
    assert evaluate(599.0, [10.0], DEFAULTS, override=False).lines == (
        "::notice::Cold start: 1 of 5 main baseline records found, so the relative check is skipped; only the 600 s "
        "ceiling applies (job took 599 s).",
    )
    limits = f"job took 260 s against a baseline median of 100 s over 5 main runs {THRESHOLDS}"
    assert evaluate(140.0, [100.0] * 5, DEFAULTS, override=False).lines == (
        f"Timing gate passed: job took 140 s against a baseline median of 100 s over 5 main runs {THRESHOLDS}.",
    )
    assert evaluate(260.0, [100.0] * 5, DEFAULTS, override=False).lines == (
        f"::error::Timing regression: {limits}. {ADVICE}",
    )
    assert evaluate(260.0, [100.0] * 5, DEFAULTS, override=True).lines == (
        f"::warning::Timing regression accepted by the 'timing-regression-accepted' label: {limits}.",
    )


def test_parser_accepts_the_unit_workflow_command_with_every_flag_unchanged() -> None:
    """Every flag of the gate command in test-unit.yml keeps its name and its meaning."""
    repo_root = Path(__file__).resolve().parents[3]
    content = (repo_root / ".github" / "workflows" / "test-unit.yml").read_text(encoding="utf-8")
    start = content.index("python3 scripts/ci_timing_gate.py")
    command = content[start : content.index("- name:", start)]
    flags = ["--workflow", "--job", "--os", "--start", "--runs", "--tolerance", "--min-delta", "--ceiling"]
    flags += ["--junit", "--repo", "--output", "--override-label-present"]
    assert re.findall(r"--[a-z-]+", command) == flags

    values = ["test-unit", "unit", "macos-latest", "1795524851", "5", "0.5", "150", "600"]
    values += ["unit-report.xml", "owner/repo", "ci-timing/record.json", "true"]
    argv = ["compare", *(part for pair in zip(flags, values, strict=True) for part in pair)]
    assert vars(build_parser().parse_args(argv)) == {
        "mode": "compare",
        "workflow": "test-unit",
        "job": "unit",
        "os_name": "macos-latest",
        "start": 1795524851.0,
        "runs": 5,
        "tolerance": 0.5,
        "min_delta": 150.0,
        "ceiling": 600.0,
        "junit": "unit-report.xml",
        "repo": "owner/repo",
        "output": "ci-timing/record.json",
        "override_label_present": "true",
    }


# Replay of the real main durations cited in the intent: job wall clock (started_at to completed_at, from the GitHub
# Actions jobs API) for the 13 successful pushes to main of 2026-10-24 to 2026-10-29, oldest first. Run ids, in the same
# commit order:
# test-unit:        40074382122 40074799021 40089243594 40172880759 40259484748 40468502785 40501662438
#                   40668530637 40714173679 40773976148 40785247773 40804868369 40812897144
# test-integration: 40074382146 40074798862 40089243595 40172880807 40259484955 40468502885 40501662750
#                   40668530635 40714173408 40773976083 40785247937 40804868400 40812897170
# test-stress:      40074382144 40074798926 40089243573 40172880809 40259484863 40468502754 40501662572
#                   40668530666 40714173246 40773976097 40785247777 40804868491 40812897147
# test-hygiene:     40074382120 40074798860 40089243562 40172880778 40259484736 40468502753 40501662233
#                   40668530629 40714173221 40773976064 40785247727 40804868418 40812897162
# make:             40074382209 40074798873 40089243584 40172880793 40259484862 40468502800 40501662676
#                   40668530627 40714173303 40773976061 40785247853 40804868398 40812897238
MAIN_COMMITS = (
    "a3f1f79",
    "21411eb",
    "fbabb3a",
    "b3e2727",
    "e3bbd67",
    "e0dfd24",
    "fbd3b35",
    "0e54457",
    "acb5dc0",
    "b1b5a1b",
    "f09ca5e",
    "e187393",
    "cc4bf1b",
)
MAIN_DURATIONS: dict[str, tuple[float, ...]] = {
    "unit ubuntu": (178, 107, 256, 286, 429, 432, 251, 136, 139, 129, 86, 123, 132),
    "unit macOS": (175, 160, 218, 395, 682, 609, 404, 133, 223, 202, 138, 177, 131),
    "integration": (162, 136, 170, 141, 158, 156, 131, 178, 138, 174, 134, 153, 153),
    "stress": (85, 89, 100, 156, 142, 137, 430, 163, 278, 262, 215, 265, 263),
    "hygiene": (32, 29, 28, 25, 21, 25, 27, 24, 46, 28, 23, 22, 28),
    "make ubuntu": (6, 4, 7, 6, 4, 6, 5, 7, 7, 5, 4, 5, 5),
    "make macOS": (10, 6, 9, 11, 11, 12, 11, 12, 10, 8, 7, 10, 10),
}


def previous_runs(series: tuple[float, ...], index: int) -> list[float]:
    """Return the up to five durations before ``index``, newest first, as the gate receives its baseline."""
    return list(reversed(series[max(0, index - DEFAULTS.runs) : index]))


def replay_relative_failures() -> dict[tuple[str, str], float]:
    """Return the baseline median of every replayed run that a full five-run baseline flags as a regression."""
    flagged: dict[tuple[str, str], float] = {}
    for label, series in MAIN_DURATIONS.items():
        for index in range(DEFAULTS.runs, len(series)):
            median = rolling_median(previous_runs(series, index))
            assert median is not None
            if exceeds_relative(series[index], median, DEFAULTS):
                flagged[(label, MAIN_COMMITS[index])] = median
    return flagged


def test_replay_relative_check_flags_exactly_the_cited_regressions() -> None:
    assert replay_relative_failures() == {
        ("unit ubuntu", "e0dfd24"): 256.0,
        ("unit macOS", "e0dfd24"): 218.0,
        ("stress", "fbd3b35"): 137.0,
        ("stress", "acb5dc0"): 156.0,
    }


def test_replay_ceiling_fails_only_the_slow_macos_unit_runs() -> None:
    failing = {
        (label, MAIN_COMMITS[index])
        for label, series in MAIN_DURATIONS.items()
        for index, duration in enumerate(series)
        if not evaluate(duration, [], DEFAULTS, override=True).passed
    }
    assert failing == {("unit macOS", "e3bbd67"), ("unit macOS", "e0dfd24")}


@pytest.mark.parametrize("label", sorted(MAIN_DURATIONS))
def test_replay_four_most_recent_main_runs_pass(label: str) -> None:
    series = MAIN_DURATIONS[label]
    for index in range(len(series) - 4, len(series)):
        verdict = evaluate(series[index], previous_runs(series, index), DEFAULTS, override=False)
        assert verdict.passed, (label, MAIN_COMMITS[index], verdict.lines)
        assert verdict.lines[0].startswith("Timing gate passed"), (label, MAIN_COMMITS[index])


def test_replay_682_second_unit_run_fails_ceiling() -> None:
    verdict = evaluate(682.0, [], DEFAULTS, override=True)
    assert not verdict.passed
    assert "600 s ceiling" in verdict.lines[0]


@pytest.mark.parametrize("duration", [132.0, 131.0, 153.0, 263.0, 278.0, 28.0, 46.0, 5.0, 10.0, 12.0])
def test_replay_recent_main_durations_pass_ceiling(duration: float) -> None:
    assert evaluate(duration, [], DEFAULTS, override=False).passed


# The macOS unit job's thresholds: the observed same-code swing is 370.2 - 235 = 135.2 s, so the minimum delta is 150.
MACOS_THRESHOLDS = Thresholds(min_delta=150.0)
MACOS_NOISE_DURATIONS = (312.7, 332.0, 370.2)
MACOS_BASELINE_MEDIANS = (235.0, 280.0, 306.0)


@pytest.mark.parametrize("duration", MACOS_NOISE_DURATIONS)
@pytest.mark.parametrize("median", MACOS_BASELINE_MEDIANS)
def test_macos_min_delta_passes_the_recorded_noise_cases(duration: float, median: float) -> None:
    verdict = evaluate(duration, [median] * 5, MACOS_THRESHOLDS, override=False)
    assert verdict.passed, verdict.lines


def test_macos_min_delta_still_fails_a_real_slowdown() -> None:
    assert exceeds_relative(480.0, 280.0, MACOS_THRESHOLDS)
    verdict = evaluate(480.0, [280.0] * 5, MACOS_THRESHOLDS, override=False)
    assert not verdict.passed
    assert verdict.lines[0].startswith("::error::Timing regression")


@pytest.mark.parametrize("baselines", [[], [280.0] * 5, [235.0] * 5])
@pytest.mark.parametrize("override", [False, True])
def test_macos_ceiling_fails_601_seconds_whatever_the_baseline_or_label(baselines: list[float], override: bool) -> None:
    verdict = evaluate(601.0, baselines, MACOS_THRESHOLDS, override=override)
    assert not verdict.passed
    assert "ceiling cannot be overridden" in verdict.lines[0]


def test_macos_cold_start_passes_noise_but_not_the_ceiling() -> None:
    assert evaluate(370.2, [], MACOS_THRESHOLDS, override=False).passed
    assert not evaluate(601.0, [], MACOS_THRESHOLDS, override=False).passed


def test_unit_workflow_compares_with_main_per_runner_os() -> None:
    """Pin test-unit.yml's wiring of ci_timing_gate.py: step-name prefix, per-OS deltas, ceiling and artefact name."""
    repo_root = Path(__file__).resolve().parents[3]
    content = (repo_root / ".github" / "workflows" / "test-unit.yml").read_text(encoding="utf-8")

    # The failed-step name must keep the "CI Timing Gate" prefix, which ci_failure_detail tags as timing_gate.
    assert "- name: CI Timing Gate (compare with main)" in content
    assert "scripts/ci_timing_gate.py" in content
    assert "./.github/actions/ci-timing-gate" not in content

    # The whole job is measured from before checkout, compare mode runs on pull requests only.
    assert content.index("Record job start time") < content.index("actions/checkout")
    assert "github.event_name == 'pull_request' && 'compare' || 'record'" in content

    # Per-OS minimum delta: 120 s on ubuntu, 150 s on macOS (above the observed 135.2 s same-code swing).
    assert re.search(r"- os: ubuntu-latest\n\s+timing-min-delta: 120\b", content)
    assert re.search(r"- os: macos-latest\n\s+timing-min-delta: 150\b", content)
    assert '--min-delta "${{ matrix.timing-min-delta }}"' in content

    # Label-proof absolute ceiling, the override label forwarded, and baselines keyed per workflow/job/OS.
    assert "--ceiling 600" in content
    assert "timing-regression-accepted" in content
    assert "name: ci-timing-test-unit-unit-${{ matrix.os }}" in content
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in content


def test_every_workflow_wires_start_and_check_steps() -> None:
    """Assert the 4 fixed-budget workflows wire the local ci-timing-gate composite action correctly."""
    repo_root = Path(__file__).resolve().parents[3]
    workflows_dir = repo_root / ".github" / "workflows"
    expected_workflows = [
        "docker-build.yml",
        "make.yml",
        "test-hygiene.yml",
        "test-integration.yml",
    ]

    for wf_name in expected_workflows:
        wf_path = workflows_dir / wf_name
        assert wf_path.is_file(), f"Workflow file {wf_name} missing"
        content = wf_path.read_text(encoding="utf-8")

        # Must reference local composite action
        assert "./.github/actions/ci-timing-gate" in content, f"{wf_name} missing action reference"
        assert "phase: start" in content, f"{wf_name} missing start phase"
        assert "phase: check" in content, f"{wf_name} missing check phase"
        assert "if: always()" in content, f"{wf_name} missing if: always() on check step"

        # Checkout must appear before ci-timing-gate start
        checkout_pos = content.find("actions/checkout@v7")
        if checkout_pos == -1:
            checkout_pos = content.find("actions/checkout")
        assert checkout_pos != -1, f"{wf_name} missing checkout step"
        start_pos = content.find("phase: start")
        assert checkout_pos < start_pos, f"{wf_name} must checkout repo before running ci-timing-gate start"

    # Verify action definition
    action_path = repo_root / ".github" / "actions" / "ci-timing-gate" / "action.yml"
    assert action_path.is_file(), "Composite action definition missing"
    action_content = action_path.read_text(encoding="utf-8")
    assert "CI_JOB_START_EPOCH" in action_content, "action.yml must define CI_JOB_START_EPOCH"
    assert "scripts/check_ci_runtime_budget.py" in action_content, (
        "action.yml must invoke scripts/check_ci_runtime_budget.py"
    )

    # Deprecated docker-pull composite action must be completely removed
    assert not (repo_root / ".github" / "actions" / "docker-pull").exists()


def test_check_ci_runtime_budget_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    summary = tmp_path / "summary.md"
    now = time.time()
    ret = budget_main(["--start-epoch", str(now - 10.0), "--max-seconds", "300.0", "--step-summary", str(summary)])
    assert ret == 0
    out = capsys.readouterr().out
    assert "CI runtime budget check passed" in out
    summary_text = summary.read_text(encoding="utf-8")
    assert "### CI Runtime Budget Check: PASSED" in summary_text
    record_data = json.loads((tmp_path / "ci-timing" / "record.json").read_text(encoding="utf-8"))
    assert record_data["passed"] is True
    assert record_data["max_seconds"] == 300.0


def test_check_ci_runtime_budget_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    summary = tmp_path / "summary.md"
    now = time.time()
    ret = budget_main(["--start-epoch", str(now - 400.0), "--max-seconds", "300.0", "--step-summary", str(summary)])
    assert ret == 1
    out = capsys.readouterr().out
    assert "::error::CI runtime budget exceeded" in out
    summary_text = summary.read_text(encoding="utf-8")
    assert "### CI Runtime Budget Check: FAILED" in summary_text
    record_data = json.loads((tmp_path / "ci-timing" / "record.json").read_text(encoding="utf-8"))
    assert record_data["passed"] is False
