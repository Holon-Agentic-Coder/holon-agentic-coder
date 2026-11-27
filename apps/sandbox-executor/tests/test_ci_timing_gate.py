"""Unit tests for scripts/ci_timing_gate.py, using fake clocks, fetchers and openers only (never the network).

The record format has its own module (``test_ci_timing_record.py``) and so has compare mode on test time
(``test_ci_timing_compare.py``); the fakes they share are in ``ci_timing_fixtures.py``.
"""

from __future__ import annotations

import http.client
import json
import re
import time
import urllib.request
import zlib
from pathlib import Path

import pytest

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
from tests.ci_timing_fixtures import (
    artefact,
    clear_github_environment,
    failing_fetcher,
    fake_fetcher,
    fixed_clock,
    gate_args,
    record,
    write_junit,
    zipped,
)

DEFAULTS = Thresholds()


@pytest.fixture(autouse=True)
def no_step_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the job summary and run metadata out of every test unless a test sets them."""
    clear_github_environment(monkeypatch)


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


def test_parser_accepts_the_unit_workflow_command_with_every_flag_unchanged() -> None:
    """Every flag of the gate command in test-unit.yml keeps its name and its meaning."""
    repo_root = Path(__file__).resolve().parents[3]
    content = (repo_root / ".github" / "workflows" / "test-unit.yml").read_text(encoding="utf-8")
    start = content.index("python3 scripts/ci_timing_gate.py")
    command = content[start : content.index("- name:", start)]
    flags = ["--workflow", "--job", "--os", "--start", "--runs", "--tolerance", "--min-delta", "--ceiling"]
    flags += ["--junit", "--repo", "--output", "--override-label-present"]
    assert re.findall(r"--[a-z-]+", command) == flags

    values = ["test-unit", "unit-shard-1", "macos-latest", "1795524851", "5", "0.5", "150", "600"]
    values += ["unit-report.xml", "owner/repo", "ci-timing/record.json", "true"]
    argv = ["compare", *(part for pair in zip(flags, values, strict=True) for part in pair)]
    assert vars(build_parser().parse_args(argv)) == {
        "mode": "compare",
        "workflow": "test-unit",
        "job": "unit-shard-1",
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
    assert "--ceiling 900" in content
    assert "timing-regression-accepted" in content
    assert '--job "${{ matrix.timing-job }}"' in content
    assert "name: ci-timing-test-unit-${{ matrix.timing-job }}-${{ matrix.os }}" in content
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
