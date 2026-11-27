from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import pytest

from scripts.ci_timing_gate import Thresholds, TimingRecord, evaluate, main
from scripts.ci_timing_noise import Breach
from tests.ci_timing_fixtures import clear_github_environment, fake_fetcher, fixed_clock, gate_args, record, write_junit

DEFAULTS = Thresholds()


@pytest.fixture(autouse=True)
def no_step_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_github_environment(monkeypatch)


LABEL_SENTENCE = (
    "The ceiling cannot be overridden by the 'timing-regression-accepted' label; it changes only through a committed "
    "edit to the --ceiling flag in the workflow file."
)
OVERRIDE = "timing-regression-accepted"
ADVICE = f"Speed the job up, or add the '{OVERRIDE}' label to accept a deliberate slow-down."
THRESHOLDS = "(tolerance 50%, minimum delta 120 s)"
BASIS = "Evidence against each test's median over the main baseline records (5 with per-test times): "
CONCENTRATED_WORDS = "The slowdown is concentrated in specific tests."
NO_SUITE_TIME = (
    "5 of 5 baseline records carry no suite test time, so only the job's wall clock can be compared with them"
)

# why: 60 tests from 0.2 s to 6.1 s sum to 189 s and 40 tests of 10 s to 400 s, the totals the verdicts quote.
UNIT_SUITE = {f"tests.unit.test_mod_{i:02d}::test_case": round(0.2 + 0.1 * i, 1) for i in range(60)}
EVEN_SUITE = {f"tests.even.test_mod_{i:02d}::test_case": 10.0 for i in range(40)}


def slowed(tests: dict[str, float], factor: float) -> dict[str, float]:
    return {test_id: seconds * factor for test_id, seconds in tests.items()}


def measured(wall: float, test_seconds: float, tests: dict[str, float], kind: str = "suite") -> TimingRecord:
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
    args = gate_args("compare", "--runs", "5", "--tolerance", "0.5", "--ceiling", "600", "--junit", str(junit), *extra)
    code = main(args, clock=fixed_clock(wall), fetcher=fake_fetcher(baselines))
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].startswith("Per-test report:")
    assert not [line for line in lines[1:] if line.startswith("::") or "Timing" in line or "Runner variance" in line]
    return code, lines[0]


def spread(judged: float, factor: float, limit: float) -> str:
    return (
        "The breach is forgiven and the gate passes, because the slowdown is spread across the suite and not "
        f"concentrated in specific tests: divided by the suite-wide scale of {factor:.2f} the judged value is "
        f"{judged / factor:.1f} s, inside its limit of {limit:.1f} s."
    )


def named_tests(line: str) -> list[str]:
    return re.findall(r"`([^`]+)`", line)


class NoisyRun(NamedTuple):
    # why: replays the failures of 2026-11-24 on a head whose code had not changed since a passing run;
    # why: test time is wall clock less the overhead of the real runs, 18 s on macOS and 9 s on Ubuntu.

    os_name: str
    min_delta: str
    wall: float
    test_seconds: float
    baseline_wall: float
    baseline_test_seconds: float
    factor: float
    breach: str
    limit: float
    old_breach: str
    old_judged: float
    old_limit: float


def macos_run(wall: float, factor: float) -> NoisyRun:
    breach = f"suite test time was {wall - 18:.0f} s, above the 600 s ceiling"
    return NoisyRun(
        "macos-latest", "150", wall, wall - 18.0, 565.0, 547.0, factor, breach, 600.0, breach, wall - 18.0, 600.0
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
        340.0,
        f"job took 360 s against a baseline median of 229 s over 5 main runs {THRESHOLDS}; {NO_SUITE_TIME}",
        360.0,
        349.0,
    ),
]
NOISY_RUN_IDS = [f"{run.os_name}-{run.wall:.0f}s" for run in NOISY_RUNS]


def spread_evidence(factor: float) -> str:
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
    forgiven = spread(run.test_seconds, run.factor, run.limit)
    assert line.startswith(f"::warning::Runner variance: {run.breach}. {forgiven} {spread_evidence(run.factor)}")
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
        "spread across the suite and not concentrated in specific tests: divided by the suite-wide scale of 1.60 the "
        "judged value is 219.4 s, inside its limit of 340.0 s. Evidence against each test's median over the main "
        "baseline records (5 with per-test times): median per-test ratio 1.60 over 60 comparable tests, 0 offenders, "
        "offender share 0.0% of the 113.4 s per-test excess (concentrated from 50%)."
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
    forgiven = spread(run.old_judged, run.factor, run.old_limit)
    assert line.startswith(f"::warning::Runner variance: {run.old_breach}. {forgiven} {spread_evidence(run.factor)}")
    assert "::error::" not in line


SLOWED_FOUR = dict.fromkeys(list(UNIT_SUITE)[:4], 45.0)
CONCENTRATED = (
    f"suite test time was 398 s against a baseline median of 220 s over 5 main runs {THRESHOLDS}",
    f"{CONCENTRATED_WORDS} {BASIS}median per-test ratio 1.00 over 60 comparable tests, "
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
        f"::error::Summed test time was 640 s, above the 600 s ceiling. {LABEL_SENTENCE} {CONCENTRATED_WORDS} "
        f"{BASIS}median per-test ratio 1.00 over 40 comparable tests, 3 offenders, offender share "
        "100.0% of the 240.0 s per-test excess (concentrated from 50%). Offenders: "
        "`tests.even.test_mod_00::test_case` 10.0 s -> 90.0 s, `tests.even.test_mod_01::test_case` 10.0 s -> 90.0 s, "
        "`tests.even.test_mod_02::test_case` 10.0 s -> 90.0 s."
    )
    assert named_tests(line) == list(grown)


BIG_SUITE = {f"tests.big.test_mod_{i:03d}::test_case": 1.0 for i in range(300)}


def test_slowdown_of_thirty_tests_at_an_unchanged_median_fails_and_names_the_ten_largest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # why: thirty tests each 6 s slower: the ten largest are a third of the excess, all thirty the whole.
    grown = {test_id: 7.0 + index / 100 for index, test_id in enumerate(list(BIG_SUITE)[:30])}
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, {**BIG_SUITE, **grown}, suite_time=484.35)
    code, line = compared(capsys, junit, 500.0, [measured(310.0, 300.0, BIG_SUITE)] * 5)
    assert code == 1
    assert line.startswith(
        "::error::Timing regression: suite test time was 484 s against a baseline median of 300 s over 5 main runs "
        f"{THRESHOLDS}. {ADVICE} {CONCENTRATED_WORDS} {BASIS}median per-test ratio 1.00 over 300 comparable tests, "
        "30 offenders, offender share 100.0% of the 184.3 s per-test excess (concentrated from 50%). Offenders (top 10 "
        "of 30): `tests.big.test_mod_029::test_case` 1.0 s -> 7.3 s, "
    )
    assert named_tests(line) == list(grown)[:19:-1]


@pytest.mark.parametrize(("count", "seconds", "named"), [(60, 4.0, 0), (12, 20.0, 10)])
def test_new_tests_that_lift_the_test_time_above_the_ceiling_fail_whatever_their_size(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], count: int, seconds: float, named: int
) -> None:
    # why: new tests are attributed together, so sixty of 4 s count like twelve of 20 s below the 5 s bar.
    added = {f"tests.added.test_mod_{i:02d}::test_case": seconds for i in range(count)}
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, {**EVEN_SUITE, **added})
    code, line = compared(capsys, junit, 660.0, [measured(420.0, 400.0, EVEN_SUITE, "sum")] * 5)
    assert code == 1
    offenders = count if named else 0
    assert line.startswith(
        f"::error::Summed test time was 640 s, above the 600 s ceiling. {LABEL_SENTENCE} {CONCENTRATED_WORDS} {BASIS}"
        f"median per-test ratio 1.00 over 40 comparable tests, {offenders} offenders, 240.0 s of new test time net of "
        "removed tests, offender and new-test share 100.0% of the 240.0 s per-test excess (concentrated from 50%)."
    )
    assert named_tests(line) == list(added)[:named]
    assert ("Offenders (top 10 of 12): " in line) is bool(named)


@pytest.mark.parametrize(
    ("label", "code", "plain"),
    [
        ("false", 1, f"::error::Timing regression: {NOISY_RUNS[4].breach}. {ADVICE}"),
        ("true", 0, f"::warning::Timing regression accepted by the '{OVERRIDE}' label: {NOISY_RUNS[4].breach}."),
    ],
)
def test_breach_that_no_test_and_no_suite_wide_scale_explains_keeps_its_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], label: str, code: int, plain: str
) -> None:
    # why: the suite grows by 131 s while every test stays within 1 % of its baseline: no runner to blame.
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, slowed(UNIT_SUITE, 1.01), suite_time=351.0)
    baselines = [measured(229.0, 220.0, UNIT_SUITE)] * 5
    result, line = compared(capsys, junit, 360.0, baselines, "--override-label-present", label)
    assert result == code
    assert line == (
        f"{plain} Runner variance does not account for the breach: divided by the suite-wide scale of 1.01 the judged "
        f"value is 347.5 s, still above its limit of 340.0 s. {BASIS}median per-test ratio 1.01 over 60 comparable "
        "tests, 0 offenders, offender share 0.0% of the 1.9 s per-test excess (concentrated from 50%)."
    )


REGRESSED = 500.0


def not_accounted(judged: float, factor: float, limit: float) -> str:
    return (
        f"Runner variance does not account for the breach: divided by the suite-wide scale of {factor:.2f} the judged "
        f"value is {judged / factor:.1f} s, still above its limit of {limit:.1f} s."
    )


def ceiling_only(judged: float, factor: float) -> str:
    return (
        f"It accounts only for the ceiling breach, which is forgiven: suite test time was {judged:.0f} s, above the "
        f"600 s ceiling, and divided by the suite-wide scale of {factor:.2f} the judged value is {judged / factor:.1f} "
        "s, inside its limit of 600.0 s."
    )


@pytest.mark.parametrize("factor", [1.15, 1.21, 1.25])
def test_spread_regression_fails_on_every_runner_whichever_side_of_the_ceiling_it_lands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], factor: float
) -> None:
    # why: the suite goes from 220 s to 500 s with no test to blame; runners make that 575 s, 605 s or 625 s.
    seconds = REGRESSED * factor
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, slowed(UNIT_SUITE, factor), suite_time=seconds)
    code, line = compared(capsys, junit, seconds + 9, [measured(229.0, 220.0, UNIT_SUITE)] * 5)
    assert code == 1
    limits = f"suite test time was {seconds:.0f} s against a baseline median of 220 s over 5 main runs {THRESHOLDS}"
    reason = not_accounted(seconds, factor, 340.0)
    if seconds > 600:
        reason += f" {ceiling_only(seconds, factor)}"
    assert line.startswith(f"::error::Timing regression: {limits}. {ADVICE} {reason} {spread_evidence(factor)}")
    assert "::warning::" not in line


def test_label_accepts_the_relative_breach_beneath_a_forgiven_ceiling_breach(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, slowed(UNIT_SUITE, 1.25), suite_time=625.0)
    baselines = [measured(229.0, 220.0, UNIT_SUITE)] * 5
    code, line = compared(capsys, junit, 634.0, baselines, "--override-label-present", "true")
    assert code == 0
    limits = f"suite test time was 625 s against a baseline median of 220 s over 5 main runs {THRESHOLDS}"
    assert line.startswith(
        f"::warning::Timing regression accepted by the '{OVERRIDE}' label: {limits}. "
        f"{not_accounted(625.0, 1.25, 340.0)} {ceiling_only(625.0, 1.25)} {spread_evidence(1.25)}"
    )


def test_label_never_lifts_a_ceiling_breach_that_runner_variance_does_not_account_for(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, slowed(UNIT_SUITE, 1.01), suite_time=625.0)
    baselines = [measured(229.0, 220.0, UNIT_SUITE)] * 5
    code, line = compared(capsys, junit, 634.0, baselines, "--override-label-present", "true")
    assert code == 1
    assert line.startswith(
        f"::error::Suite test time was 625 s, above the 600 s ceiling. {LABEL_SENTENCE} "
        f"{not_accounted(625.0, 1.01, 600.0)} {spread_evidence(1.01)}"
    )


def test_ceiling_and_relative_breach_are_both_forgiven_when_the_scale_accounts_for_both(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, slowed(UNIT_SUITE, 1.6), suite_time=640.0)
    code, line = compared(capsys, junit, 649.0, [measured(389.0, 380.0, UNIT_SUITE)] * 5)
    assert code == 0
    assert line.startswith(
        f"::warning::Runner variance: suite test time was 640 s, above the 600 s ceiling. {spread(640.0, 1.6, 600.0)} "
        "The relative breach is forgiven with it: suite test time was 640 s against a baseline median of 380 s over 5 "
        f"main runs {THRESHOLDS}, and divided by the suite-wide scale of 1.60 the judged value is 400.0 s, inside its "
        f"limit of 570.0 s. {spread_evidence(1.6)}"
    )


def test_relative_breach_beneath_a_forgiven_ceiling_breach_is_judged_on_wall_clock_for_old_baseline_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # why: the ceiling is held on the 625 s of test time and the relative check on the 634 s of wall clock.
    junit = tmp_path / "unit-report.xml"
    write_junit(junit, slowed(UNIT_SUITE, 1.25), suite_time=625.0)
    code, line = compared(capsys, junit, 634.0, [record(229.0, UNIT_SUITE)] * 5)
    assert code == 1
    assert line.startswith(
        f"::error::Timing regression: job took 634 s against a baseline median of 229 s over 5 main runs {THRESHOLDS}; "
        f"{NO_SUITE_TIME}. {ADVICE} {not_accounted(634.0, 1.25, 349.0)} {ceiling_only(625.0, 1.25)} "
        f"{spread_evidence(1.25)}"
    )


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
    # why: the median of all five wall clocks is 229 s; the three records with test time alone give 250 s.
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
    # why: the baseline suite time of 20 s would fail the run's 189 s of summed test time; wall clocks are close.
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
    # why: the ceiling is held against no baseline record, so its line never says old records lack test time.
    assert line == (
        f"::error::Suite test time was 645 s, above the 600 s ceiling. {LABEL_SENTENCE} Runner variance could not be "
        "assessed: no baseline per-test times."
    )


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


def test_evaluate_records_the_judged_value_and_the_limit_it_breached() -> None:
    assert evaluate(140.0, [100.0] * 5, DEFAULTS, override=False).breach is None
    assert evaluate(601.0, [], DEFAULTS, override=True).breach == Breach(
        "job took 601 s, above the 600 s ceiling", 601.0, 600.0
    )
    limits = f"job took 260 s against a baseline median of 100 s over 5 main runs {THRESHOLDS}"
    for override in (False, True):
        assert evaluate(260.0, [100.0] * 5, DEFAULTS, override=override).breach == Breach(limits, 260.0, 220.0)
    slow = evaluate(700.0, [400.0] * 5, Thresholds(ceiling=900.0), override=False).breach
    assert slow is not None
    assert (slow.seconds, slow.limit) == (700.0, 600.0)


@pytest.mark.parametrize("override", [False, True])
def test_evaluate_keeps_a_breached_relative_verdict_beneath_a_ceiling_breach(override: bool) -> None:
    verdict = evaluate(625.0, [220.0] * 5, DEFAULTS, override=override)
    assert not verdict.passed
    assert verdict.lines == (f"::error::Job took 625 s, above the 600 s ceiling. {LABEL_SENTENCE}",)
    assert verdict.breach == Breach("job took 625 s, above the 600 s ceiling", 625.0, 600.0)
    limits = f"job took 625 s against a baseline median of 220 s over 5 main runs {THRESHOLDS}"
    assert verdict.beneath is not None
    assert verdict.beneath.passed is override
    assert verdict.beneath.breach == Breach(limits, 625.0, 340.0)
    assert verdict.beneath.lines == evaluate(625.0, [220.0] * 5, Thresholds(ceiling=900.0), override=override).lines
    assert evaluate(625.0, [547.0] * 5, DEFAULTS, override=override).beneath is None
    assert evaluate(625.0, [220.0] * 4, DEFAULTS, override=override).beneath is None
