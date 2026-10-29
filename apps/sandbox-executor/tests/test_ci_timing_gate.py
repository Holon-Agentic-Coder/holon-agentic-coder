"""Unit tests for scripts/ci_timing_gate.py, using fake clocks, fetchers and openers only (never the network)."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from scripts.ci_timing_gate import (
    OVERRIDE_LABEL,
    Thresholds,
    TimingRecord,
    artefact_name,
    evaluate,
    exceeds_relative,
    fetch_baseline,
    format_report,
    main,
    parse_junit,
    rolling_median,
    safe_fetch,
    top_offenders,
)

DEFAULTS = Thresholds()
START = 1_000_000.0


def record(duration: float, tests: dict[str, float] | None = None) -> TimingRecord:
    """Build a baseline record for the unit job."""
    return TimingRecord(job="unit", os="ubuntu-latest", workflow="test-unit", duration=duration, tests=tests or {})


def reconstruct(median: float) -> list[float]:
    """Reconstruct five baseline durations with ``median``; the intent cites the median, not the raw series."""
    return [median - 20, median - 10, median, median + 10, median + 20]


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


def test_top_offenders_needs_both_growth_thresholds() -> None:
    baseline = [{"a": 10.0, "b": 2.0, "c": 20.0, "d": 10.0}]
    current = {"a": 16.0, "b": 6.0, "c": 26.0, "d": 15.0}
    # a: +60 % and +6 s; b: +200 % but only +4 s; c: +6 s but only +30 %; d: +50 % exactly, not more.
    assert [row.test_id for row in top_offenders(current, baseline)] == ["a"]


def test_top_offenders_sorted_by_growth_limited_and_ignores_new_tests() -> None:
    baseline = [{f"t{i}": 10.0 for i in range(12)}, {f"t{i}": 12.0 for i in range(12)}]
    current = {f"t{i}": 30.0 + i for i in range(12)}
    current["new_test"] = 500.0
    rows = top_offenders(current, baseline)
    assert len(rows) == 10
    assert rows[0].test_id == "t11"
    assert rows[0].baseline == 11.0
    assert rows[0].growth == 30.0
    assert "new_test" not in {row.test_id for row in rows}
    assert [row.growth for row in rows] == sorted((row.growth for row in rows), reverse=True)


def test_format_report_with_and_without_rows() -> None:
    assert "no test grew" in format_report([])[0]
    lines = format_report(top_offenders({"m::slow": 30.0}, [{"m::slow": 10.0}]))
    assert "| `m::slow` | 10.0 | 30.0 | +20.0 |" in lines


def write_junit(path: Path, cases: dict[str, float]) -> None:
    """Write a minimal JUnit report with ``classname::name`` cases."""
    body = "".join(
        f'<testcase classname="{test_id.split("::")[0]}" name="{test_id.split("::")[1]}" time="{t}"/>'
        for test_id, t in cases.items()
    )
    path.write_text(f'<testsuites><testsuite name="pytest">{body}</testsuite></testsuites>')


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


# Baseline fetch through a fake opener


def zipped(rec: TimingRecord) -> bytes:
    """Return an in-memory artefact archive holding ``rec`` as record.json."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("record.json", json.dumps(rec.to_json()))
    return buffer.getvalue()


def artefact(index: int, branch: str = "main", expired: bool = False) -> dict[str, object]:
    """Return one artefact listing entry."""
    return {
        "id": index,
        "expired": expired,
        "created_at": f"2026-10-{index:02d}T00:00:00Z",
        "archive_download_url": f"https://api.github.com/download/{index}",
        "workflow_run": {"head_branch": branch},
    }


def test_fetch_baseline_filters_limits_and_keeps_token_unredirected() -> None:
    entries = [artefact(i) for i in range(1, 8)]
    entries += [artefact(20, branch="feature"), artefact(21, expired=True)]
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


# Replay of the real main durations cited in the intent. Baselines given only as a median are reconstructions.


@pytest.mark.parametrize(
    ("label", "duration", "median"),
    [
        ("unit ubuntu at e0dfd24", 432.0, 256.0),
        ("unit macOS at e0dfd24", 609.0, 218.0),
        ("stress spike at fbd3b35", 430.0, 137.0),
        ("stress step at acb5dc0", 278.0, 156.0),
    ],
)
def test_replay_historical_regressions_fail(label: str, duration: float, median: float) -> None:
    assert not evaluate(duration, reconstruct(median), DEFAULTS, override=False).passed, label


@pytest.mark.parametrize(
    ("label", "duration", "median"),
    [
        ("unit ubuntu", 132.0, 150.0),
        ("unit macOS", 131.0, 180.0),
        ("integration", 153.0, 150.0),
        ("stress", 263.0, 265.0),
        ("hygiene", 28.0, 30.0),
        ("make ubuntu", 5.0, 6.0),
        ("make macOS", 10.0, 9.0),
    ],
)
def test_replay_latest_main_cc4bf1b_passes_with_baseline_and_cold_start(
    label: str, duration: float, median: float
) -> None:
    assert evaluate(duration, reconstruct(median), DEFAULTS, override=False).passed, label
    assert evaluate(duration, [], DEFAULTS, override=False).passed, label


def test_replay_682_second_unit_run_fails_ceiling() -> None:
    verdict = evaluate(682.0, [], DEFAULTS, override=True)
    assert not verdict.passed
    assert "600 s ceiling" in verdict.lines[0]


@pytest.mark.parametrize("duration", [132.0, 131.0, 153.0, 263.0, 278.0, 28.0, 46.0, 5.0, 10.0, 12.0])
def test_replay_recent_main_durations_pass_ceiling(duration: float) -> None:
    assert evaluate(duration, [], DEFAULTS, override=False).passed
