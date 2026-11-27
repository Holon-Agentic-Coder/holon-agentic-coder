from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.ci_timing_noise import (
    Breach,
    Offender,
    Verdict,
    baseline_medians,
    describe_evidence,
    describe_offenders,
    fallback_note,
    format_report,
    gather_evidence,
    insufficient_reason,
    name_offenders,
    net_new_seconds,
    per_test_report,
    rejudge,
    took,
    top_offenders,
    without_command,
)

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
GATE_SCRIPT = SCRIPTS / "ci_timing_gate.py"
NOISE_SCRIPT = SCRIPTS / "ci_timing_noise.py"


def test_top_offenders_needs_both_growth_thresholds() -> None:
    baseline = [{"a": 10.0, "b": 2.0, "c": 20.0, "d": 10.0}]
    current = {"a": 16.0, "b": 6.0, "c": 26.0, "d": 15.0}
    # why: a clears both bars; b grows +200 % but 4 s, c grows 6 s but 30 %, d sits exactly at +50 %
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


def test_per_test_report_skipped_without_baseline_times() -> None:
    assert per_test_report({"m::slow": 30.0}, []) == [
        "Per-test report: skipped, no baseline records with per-test times yet."
    ]
    assert "skipped" in per_test_report({"m::slow": 30.0}, [{}, {}])[0]
    assert "no test grew" in per_test_report({"m::slow": 10.0}, [{"m::slow": 10.0}])[0]


def script_environment() -> dict[str, str]:
    dropped = {"PYTHONPATH", "GITHUB_STEP_SUMMARY", "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_TOKEN"}
    return {name: value for name, value in os.environ.items() if name not in dropped}


def test_gate_runs_as_a_script_file_from_another_directory(tmp_path: Path) -> None:
    command = [sys.executable, str(GATE_SCRIPT), "record", "--workflow", "w", "--job", "j", "--os", "o"]
    result = subprocess.run(
        [*command, "--start", str(time.time())],
        cwd=tmp_path,
        env=script_environment(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("Recorded ci-timing-w-j-o: ")


def test_script_mode_imports_the_noise_module_by_its_bare_name(tmp_path: Path) -> None:
    # why: without the repository root on sys.path the package import fails and the gate falls back to the sibling
    code = "import ci_timing_gate; print(ci_timing_gate.noise.__name__, ci_timing_gate.Verdict.__module__)"
    result = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(SCRIPTS)!r}); {code}"],
        cwd=tmp_path,
        env=script_environment(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["ci_timing_noise", "ci_timing_noise"]


def test_both_scripts_keep_python_39_syntax() -> None:
    for script in (GATE_SCRIPT, NOISE_SCRIPT):
        ast.parse(script.read_text(encoding="utf-8"), filename=str(script), feature_version=(3, 9))


def test_noise_module_never_imports_the_gate() -> None:
    tree = ast.parse(NOISE_SCRIPT.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported.extend(alias.name for alias in node.names)
    assert imported
    assert not [name for name in imported if "ci_timing_gate" in name]


def suite() -> dict[str, float]:
    # why: 60 tests of 413.6 s: 5 of 0.02 s, 25 of 0.1 to 2.5 s, 30 of 4 to 21.4 s; later asserts rely on these sums
    times = {f"tests.tiny::test_{i}": 0.02 for i in range(5)}
    times.update({f"tests.quick::test_{i}": round(0.1 * (i + 1), 1) for i in range(25)})
    times.update({f"tests.slow::test_{i}": round(4.0 + 0.6 * i, 1) for i in range(30)})
    return times


def slowed(times: dict[str, float], factor: float) -> dict[str, float]:
    return {test_id: seconds * factor for test_id, seconds in times.items()}


# why: rows of the per-test report of the failed ubuntu run of 2026-11-24: (baseline median, current) seconds
UBUNTU_REPORT_ROWS = (
    (0.8, 13.4),
    (0.1, 11.1),
    (0.8, 10.9),
    (1.3, 10.9),
    (0.8, 10.1),
    (0.1, 8.5),
    (0.7, 8.6),
    (0.6, 7.4),
    (0.0, 6.8),
    (0.0, 6.2),
)


def test_suite_builder_matches_its_description() -> None:
    times = suite()
    assert len(times) == 60
    assert sum(times.values()) == pytest.approx(413.6)
    assert sum(1 for seconds in times.values() if seconds >= 0.05) == 55
    assert min(times.values()) == 0.02
    assert max(times.values()) <= 30.0


def test_uniform_slowdown_has_no_offender_and_is_spread() -> None:
    evidence = gather_evidence(slowed(suite(), 1.6), [suite()] * 5)
    assert evidence is not None
    assert evidence.comparable == 55
    assert evidence.median_ratio == pytest.approx(1.6)
    assert evidence.excess == pytest.approx(413.6 * 0.6)
    assert evidence.offenders == ()
    assert evidence.share == 0.0
    assert not evidence.concentrated


def test_uniform_slowdown_of_a_top_heavy_suite_is_still_spread() -> None:
    # why: ten tests hold 300 s of 317.5 s; raw growth is 9 s each, growth beyond the scale is zero
    times = {f"tests.heavy::test_{i}": 30.0 for i in range(10)}
    times.update({f"tests.light::test_{i}": 0.5 for i in range(35)})
    evidence = gather_evidence(slowed(times, 1.3), [times] * 5)
    assert evidence is not None
    assert evidence.median_ratio == pytest.approx(1.3)
    # why: +30 % stays under the report's own +50 % bar
    assert len(top_offenders(slowed(times, 1.3), [times])) == 0
    assert evidence.offenders == ()
    assert not evidence.concentrated
    # why: at 1.6 times every heavy test passes the raw report bars, none passes them beyond the scale
    assert len(top_offenders(slowed(times, 1.6), [times])) == 10
    evidence = gather_evidence(slowed(times, 1.6), [times] * 5)
    assert evidence is not None
    assert evidence.offenders == ()
    assert not evidence.concentrated


def test_concentrated_slowdown_names_exactly_the_slowed_tests_in_order_of_growth() -> None:
    current = suite()
    grown = {"tests.quick::test_1": 40.0, "tests.quick::test_3": 60.0, "tests.quick::test_5": 47.0}
    grown["tests.quick::test_7"] = 53.0
    current.update(grown)
    evidence = gather_evidence(current, [suite()] * 5)
    assert evidence is not None
    assert evidence.median_ratio == 1.0
    assert evidence.concentrated
    assert evidence.share == pytest.approx(1.0)
    assert [row.test_id for row in evidence.offenders] == [
        "tests.quick::test_3",
        "tests.quick::test_7",
        "tests.quick::test_5",
        "tests.quick::test_1",
    ]
    assert [(row.baseline, row.current) for row in evidence.offenders] == [
        (0.4, 60.0),
        (0.8, 53.0),
        (0.6, 47.0),
        (0.2, 40.0),
    ]
    assert not any(row.is_new for row in evidence.offenders)


def test_failed_ubuntu_run_is_spread_although_ten_tests_grew() -> None:
    # why: a contended runner: every test 1.6 times slower plus ten unrelated sub-second tests at 6 to 13 s
    baseline = suite()
    current = slowed(baseline, 1.6)
    for index, (before, after) in enumerate(UBUNTU_REPORT_ROWS):
        baseline[f"tests.contended::test_{index}"] = before
        current[f"tests.contended::test_{index}"] = after
    evidence = gather_evidence(current, [baseline] * 5)
    assert evidence is not None
    assert evidence.median_ratio == pytest.approx(1.6)
    assert len(evidence.offenders) == 10
    assert {row.test_id.split("::")[0] for row in evidence.offenders} == {"tests.contended"}
    # why: 93.9 s after less 5.2 s before scaled by 1.6; over 0.6 * 413.6 s of the suite plus 88.7 s of the ten
    assert evidence.attributed == pytest.approx(85.58)
    assert evidence.excess == pytest.approx(336.86)
    assert evidence.share == pytest.approx(0.25, abs=0.01)
    assert not evidence.concentrated


def test_new_slow_test_is_concentrated_and_worded_as_new() -> None:
    current = suite()
    current["tests.added::test_slow"] = 60.0
    evidence = gather_evidence(current, [suite()] * 5)
    assert evidence is not None
    assert evidence.concentrated
    assert evidence.offenders == (Offender("tests.added::test_slow", 0.0, 60.0, is_new=True),)
    assert describe_offenders(evidence.offenders) == "`tests.added::test_slow` new -> 60.0 s"


def renamed(times: dict[str, float]) -> dict[str, float]:
    return {test_id.replace("tests.slow::", "tests.moved::"): seconds for test_id, seconds in times.items()}


def test_renamed_tests_attribute_nothing_and_are_not_named() -> None:
    # why: a renamed module on a slower runner: its thirty tests of up to 21.4 s look new, as many vanished
    current = renamed(slowed(suite(), 1.4))
    evidence = gather_evidence(current, [suite()] * 5)
    assert evidence is not None
    assert evidence.comparable == 25
    assert evidence.median_ratio == pytest.approx(1.4)
    assert evidence.new_seconds == 0.0
    assert evidence.offenders == ()
    assert evidence.attributed == 0.0
    # why: the renamed tests weigh 381 s: their 40 % growth is in the excess, their baseline time is not
    assert evidence.excess == pytest.approx(413.6 * 0.4)
    assert not evidence.concentrated
    assert "new test time" not in describe_evidence(evidence)


def test_rename_with_a_net_of_a_few_seconds_names_no_renamed_test() -> None:
    # why: thirty renamed tests of 5.6 to 30 s net to 3.8 s, under the report minimum, so none is an offender
    current = renamed(slowed(suite(), 1.4))
    current["tests.moved::test_0"] += 3.8
    evidence = gather_evidence(current, [suite()] * 5)
    assert evidence is not None
    assert evidence.new_seconds == pytest.approx(3.8)
    assert evidence.attributed == pytest.approx(3.8)
    assert evidence.offenders == ()
    assert not evidence.concentrated
    assert "0 offenders, 3.8 s of new test time net of removed tests, " in describe_evidence(evidence)


def test_rename_with_jitter_names_the_slowed_tests_and_not_the_renamed_ones() -> None:
    # why: six tests 12 s slower hold 72 s of the 73 s excess; the renamed module's 1 s of jitter names nobody
    current = renamed(suite())
    current["tests.moved::test_29"] += 1.0
    grown = [f"tests.quick::test_{index}" for index in range(6)]
    for test_id in grown:
        current[test_id] += 12.0
    evidence = gather_evidence(current, [suite()] * 5)
    assert evidence is not None
    assert evidence.new_seconds == pytest.approx(1.0)
    assert evidence.excess == pytest.approx(73.0)
    assert evidence.attributed == pytest.approx(73.0)
    assert sorted(row.test_id for row in evidence.offenders) == grown
    assert not any(row.is_new for row in evidence.offenders)
    assert evidence.concentrated


def test_new_tests_are_named_only_to_the_extent_of_the_net_new_time() -> None:
    # why: thirty renamed tests each 1 s slower net to 30 s: the two largest tests take all of it
    current = {test_id: seconds + 1.0 for test_id, seconds in renamed(suite()).items() if "moved" in test_id}
    current.update({test_id: seconds for test_id, seconds in suite().items() if "slow" not in test_id})
    evidence = gather_evidence(current, [suite()] * 5)
    assert evidence is not None
    assert evidence.new_seconds == pytest.approx(30.0)
    assert evidence.attributed == pytest.approx(30.0)
    assert evidence.offenders == (
        Offender("tests.moved::test_29", 0.0, pytest.approx(22.4), is_new=True),
        Offender("tests.moved::test_28", 0.0, pytest.approx(21.8), is_new=True),
    )


def test_new_test_ranks_by_its_part_of_the_net_new_time_beside_slowed_tests() -> None:
    # why: a 60 s test replacing one of 21.4 s explains 38.6 s, less than a test slowed by 45 s
    current = {test_id: seconds for test_id, seconds in suite().items() if test_id != "tests.slow::test_29"}
    current["tests.added::test_slow"] = 60.0
    current["tests.quick::test_0"] = 45.1
    evidence = gather_evidence(current, [suite()] * 5)
    assert evidence is not None
    assert [row.test_id for row in evidence.offenders] == ["tests.quick::test_0", "tests.added::test_slow"]
    assert evidence.attributed == pytest.approx(45.0 + 38.6)


def test_net_new_seconds_subtracts_the_scaled_vanished_tests_and_never_goes_negative() -> None:
    medians = {"tests.m::kept": 5.0, "tests.m::gone": 20.0}
    assert net_new_seconds({"tests.m::kept": 9.0, "tests.m::added": 50.0}, medians) == 30.0
    assert net_new_seconds({"tests.m::kept": 9.0, "tests.m::added": 50.0}, medians, scale=1.5) == 20.0
    assert net_new_seconds({"tests.m::kept": 9.0, "tests.m::added": 8.0}, medians) == 0.0
    assert net_new_seconds({"tests.m::kept": 9.0, "tests.m::gone": 90.0}, medians) == 0.0


@pytest.mark.parametrize(("count", "seconds", "named"), [(200, 4.0, 0), (25, 20.0, 25)])
def test_new_tests_are_attributed_together_whatever_their_size_and_number(
    count: int, seconds: float, named: int
) -> None:
    # why: 200 new tests of 4 s pass no 5 s bar; 25 of 20 s outnumber the ten named: both must count
    current = suite()
    current.update({f"tests.added::test_{i}": seconds for i in range(count)})
    evidence = gather_evidence(current, [suite()] * 5)
    assert evidence is not None
    assert evidence.median_ratio == 1.0
    assert evidence.new_seconds == pytest.approx(count * seconds)
    assert evidence.attributed == pytest.approx(count * seconds)
    assert evidence.share == pytest.approx(1.0)
    assert evidence.concentrated
    assert len(evidence.offenders) == named
    assert all(row.is_new for row in evidence.offenders)


def test_new_tests_beyond_the_vanished_ones_count_for_their_net_time_only() -> None:
    current = {test_id: seconds for test_id, seconds in suite().items() if test_id != "tests.slow::test_29"}
    current["tests.added::test_slow"] = 60.0
    evidence = gather_evidence(current, [suite()] * 5)
    assert evidence is not None
    assert evidence.new_seconds == pytest.approx(60.0 - 21.4)
    assert evidence.excess == pytest.approx(60.0 - 21.4)
    assert evidence.offenders == (Offender("tests.added::test_slow", 0.0, 60.0, is_new=True),)
    assert describe_evidence(evidence) == (
        "median per-test ratio 1.00 over 54 comparable tests, 1 offenders, 38.6 s of new test time net of removed "
        "tests, offender and new-test share 100.0% of the 38.6 s per-test excess (concentrated from 50%)"
    )


def test_every_offender_is_attributed_and_only_the_ten_largest_are_named() -> None:
    current = suite()
    for index in range(12):
        current[f"tests.quick::test_{index}"] += 20.0 + index
    evidence = gather_evidence(current, [suite()] * 5)
    assert evidence is not None
    assert [row.test_id for row in evidence.offenders] == [f"tests.quick::test_{index}" for index in range(11, -1, -1)]
    assert evidence.attributed == pytest.approx(sum(20.0 + index for index in range(12)))
    assert evidence.share == pytest.approx(1.0)
    assert evidence.concentrated
    named = name_offenders(evidence.offenders)
    assert named.startswith(" Offenders (top 10 of 12): `tests.quick::test_11` 1.2 s -> 32.2 s, ")
    assert named.endswith(", `tests.quick::test_2` 0.3 s -> 22.3 s.")
    assert named.count("`") == 20
    assert name_offenders(evidence.offenders[:10]).startswith(" Offenders: `tests.quick::test_11` ")
    assert name_offenders(()) == ""


def big_suite() -> dict[str, float]:
    return {f"tests.big::test_{i:03d}": 8.0 for i in range(300)}


def test_slowdown_of_thirty_tests_in_a_large_suite_is_concentrated() -> None:
    # why: 30 tests each 6 s slower at an unchanged median: the ten largest are a third of the excess
    current = big_suite()
    for index in range(30):
        current[f"tests.big::test_{index:03d}"] = 14.0
    evidence = gather_evidence(current, [big_suite()] * 5)
    assert evidence is not None
    assert evidence.median_ratio == 1.0
    assert len(evidence.offenders) == 30
    assert evidence.attributed == pytest.approx(180.0)
    assert evidence.share == pytest.approx(1.0)
    assert evidence.concentrated


def test_a_test_with_a_zero_baseline_can_be_an_offender_and_is_not_worded_as_new() -> None:
    baseline = suite()
    baseline["tests.zero::test_instant"] = 0.0
    current = dict(baseline)
    current["tests.zero::test_instant"] = 9.0
    evidence = gather_evidence(current, [baseline] * 5)
    assert evidence is not None
    assert evidence.comparable == 55
    assert describe_offenders(evidence.offenders) == "`tests.zero::test_instant` 0.0 s -> 9.0 s"


def test_zero_baselines_are_left_out_of_the_ratio_without_raising() -> None:
    baseline = {f"tests.zero::test_{i}": 0.0 for i in range(30)}
    current = dict.fromkeys(baseline, 1.0)
    assert gather_evidence(current, [baseline] * 5) is None
    assert insufficient_reason(current, [baseline] * 5) == "only 0 comparable tests, fewer than 20"


@pytest.mark.parametrize("baselines", [[], [{}, {}]])
def test_no_baseline_per_test_times_is_insufficient(baselines: list[dict[str, float]]) -> None:
    assert gather_evidence(slowed(suite(), 2.0), baselines) is None
    assert insufficient_reason(slowed(suite(), 2.0), baselines) == "no baseline per-test times"


def test_fewer_than_twenty_comparable_tests_is_insufficient() -> None:
    baseline = {f"tests.few::test_{i}": 1.0 for i in range(19)}
    baseline.update({f"tests.tiny::test_{i}": 0.04 for i in range(10)})
    current = slowed(baseline, 3.0)
    assert gather_evidence(current, [baseline] * 5) is None
    assert insufficient_reason(current, [baseline] * 5) == "only 19 comparable tests, fewer than 20"
    baseline["tests.few::test_19"] = 1.0
    assert gather_evidence(slowed(baseline, 3.0), [baseline] * 5) is not None


def test_identical_times_have_no_excess_and_are_insufficient() -> None:
    assert gather_evidence(suite(), [suite()] * 5) is None
    assert insufficient_reason(suite(), [suite()] * 5) == "no per-test excess"


def test_baseline_is_the_median_of_the_records_that_contain_the_test() -> None:
    present = [{"tests.m::partial": 1.0}, {"tests.m::partial": 9.0}, {"tests.m::partial": 2.0}]
    records = [{**suite(), **extra} for extra in (*present, {}, {})]
    assert baseline_medians(records)["tests.m::partial"] == 2.0
    current = suite()
    current["tests.m::partial"] = 30.0
    evidence = gather_evidence(current, records)
    assert evidence is not None
    assert evidence.offenders == (Offender("tests.m::partial", 2.0, 30.0),)


def test_wording_fragments_stay_on_one_line() -> None:
    current = suite()
    current["tests.odd::test_a\nb\tc`d"] = 70.0
    current["tests.quick::test_0"] = 33.3
    evidence = gather_evidence(current, [suite()] * 5)
    assert evidence is not None
    fragment = describe_evidence(evidence)
    assert fragment == (
        "median per-test ratio 1.00 over 55 comparable tests, 2 offenders, 70.0 s of new test time net of removed "
        "tests, offender and new-test share 100.0% of the 103.2 s per-test excess (concentrated from 50%)"
    )
    offenders = describe_offenders(evidence.offenders)
    assert offenders == "`tests.odd::test_a b c'd` new -> 70.0 s, `tests.quick::test_0` 0.1 s -> 33.3 s"
    for text in (fragment, offenders):
        assert "\n" not in text
        assert "\t" not in text
    assert offenders.count("`") == 4


WORDS = "suite test time was 351 s against a baseline median of 220 s over 5 main runs"
BREACH = Breach(WORDS, 351.0, 340.0)
PLAIN = f"::error::Timing regression: {WORDS}. Speed the job up."
BASIS = "Evidence against each test's median over the main baseline records (5 with per-test times): "


def test_took_names_the_measure_judged() -> None:
    assert took(682.4) == "job took 682 s"
    assert took(645.2, "suite") == "suite test time was 645 s"
    assert took(640.0, "sum") == "summed test time was 640 s"


def test_fallback_note_counts_the_records_without_the_test_time() -> None:
    assert fallback_note(2, 5, "suite") == (
        "; 2 of 5 baseline records carry no suite test time, so only the job's wall clock can be compared with them"
    )
    assert "carry no summed test time" in fallback_note(5, 5, "sum")


def test_without_command_strips_only_a_leading_workflow_command() -> None:
    assert without_command("::error::Timing regression: `tests.m::slow` 0.1 s -> 9.0 s.") == (
        "Timing regression: `tests.m::slow` 0.1 s -> 9.0 s."
    )
    assert without_command("::notice::Cold start") == "Cold start"
    assert without_command("Timing gate passed: job took 10 s.") == "Timing gate passed: job took 10 s."
    assert without_command("| `tests.m::slow` | 5.0 | 60.0 | +55.0 |") == "| `tests.m::slow` | 5.0 | 60.0 | +55.0 |"
    assert without_command("::unfinished") == "::unfinished"


def test_rejudge_returns_a_verdict_without_a_breach_unchanged() -> None:
    verdict = Verdict(True, ("Timing gate passed: job took 10 s.",))
    assert rejudge(verdict, slowed(suite(), 3.0), [suite()] * 5) is verdict


def test_rejudge_forgives_a_spread_slowdown_with_one_runner_variance_warning() -> None:
    verdict = rejudge(Verdict(False, (PLAIN,), BREACH), slowed(suite(), 1.6), [suite()] * 5)
    assert verdict == Verdict(
        True,
        (
            f"::warning::Runner variance: {WORDS}. The breach is forgiven and the gate passes, because the slowdown "
            "is spread across the suite and not concentrated in specific tests: divided by the suite-wide scale of "
            f"1.60 the judged value is 219.4 s, inside its limit of 340.0 s. {BASIS}median per-test ratio 1.60 over "
            "55 comparable tests, 0 offenders, offender share 0.0% of the 248.2 s per-test excess (concentrated from "
            "50%).",
        ),
        BREACH,
    )


@pytest.mark.parametrize("passed", [False, True])
def test_rejudge_keeps_the_outcome_of_a_concentrated_slowdown_and_names_the_offenders(passed: bool) -> None:
    current = {**suite(), "tests.quick::test_0": 33.3, "tests.quick::test_1": 44.4}
    verdict = rejudge(Verdict(passed, (PLAIN,), BREACH), current, [suite()] * 5)
    assert verdict.passed is passed
    assert verdict.breach == BREACH
    assert verdict.lines == (
        f"{PLAIN} The slowdown is concentrated in specific tests. {BASIS}"
        "median per-test ratio 1.00 over 55 comparable tests, 2 offenders, offender share 100.0% of the 77.4 s "
        "per-test excess (concentrated from 50%). Offenders: `tests.quick::test_1` 0.2 s -> 44.4 s, "
        "`tests.quick::test_0` 0.1 s -> 33.3 s.",
    )


def test_rejudge_keeps_a_concentrated_slowdown_that_the_scale_would_account_for() -> None:
    # why: a 1.6 times slower runner must not forgive two tests that explain most of the excess
    current = {**slowed(suite(), 1.6), "tests.quick::test_0": 200.0, "tests.quick::test_1": 300.0}
    verdict = rejudge(Verdict(False, (PLAIN,), BREACH), current, [suite()] * 5)
    assert not verdict.passed
    assert (
        f"{PLAIN} The slowdown is concentrated in specific tests. {BASIS}median per-test ratio 1.60" in verdict.lines[0]
    )


@pytest.mark.parametrize("passed", [False, True])
def test_rejudge_keeps_a_spread_slowdown_that_the_scale_does_not_account_for(passed: bool) -> None:
    # why: 1.02 times slower explains 7 s of a 131 s breach, so the runner does not account for it
    verdict = rejudge(Verdict(passed, (PLAIN,), BREACH), slowed(suite(), 1.02), [suite()] * 5)
    assert verdict == Verdict(
        passed,
        (
            f"{PLAIN} Runner variance does not account for the breach: divided by the suite-wide scale of 1.02 the "
            f"judged value is 344.1 s, still above its limit of 340.0 s. {BASIS}median per-test ratio 1.02 over 55 "
            "comparable tests, 0 offenders, offender share 0.0% of the 8.3 s per-test excess (concentrated from 50%).",
        ),
        BREACH,
    )


@pytest.mark.parametrize(("limit", "forgiven"), [(219.3, False), (219.375, True), (219.4, True)])
def test_rejudge_forgives_only_when_the_rescaled_value_is_back_inside_the_limit(limit: float, forgiven: bool) -> None:
    # why: 351 s at a scale of 1.6 is 219.375 s: forgiven at exactly that limit, kept at any limit under it
    breach = Breach(WORDS, 351.0, limit)
    verdict = rejudge(Verdict(False, (PLAIN,), breach), slowed(suite(), 1.6), [suite()] * 5)
    assert verdict.passed is forgiven
    assert verdict.lines[0].startswith("::warning::Runner variance: " if forgiven else f"{PLAIN} Runner variance does")


CEILING = Breach("suite test time was 625 s, above the 600 s ceiling", 625.0, 600.0)
CEILING_LINE = "::error::Suite test time was 625 s, above the 600 s ceiling."
HIDDEN_WORDS = "suite test time was 625 s against a baseline median of 220 s over 5 main runs"


def test_rejudge_forgives_a_ceiling_breach_with_a_relative_breach_beneath_only_when_the_scale_explains_both() -> None:
    # why: 625 s at a scale of 1.25 is 500 s: inside the 600 s ceiling and inside a relative limit of 500 s
    inside = Verdict(False, (f"::error::Timing regression: {HIDDEN_WORDS}.",), Breach(HIDDEN_WORDS, 625.0, 500.0))
    verdict = rejudge(Verdict(False, (CEILING_LINE,), CEILING, inside), slowed(suite(), 1.25), [suite()] * 5)
    assert verdict.passed
    assert verdict.breach == CEILING
    assert verdict.lines[0].startswith(f"::warning::Runner variance: {CEILING.words}. The breach is forgiven ")
    assert (
        "inside its limit of 600.0 s. The relative breach is forgiven with it: "
        f"{HIDDEN_WORDS}, and divided by the suite-wide scale of 1.25 the judged value is 500.0 s, inside its limit of "
        f"500.0 s. {BASIS}median per-test ratio 1.25 over 55 comparable tests, " in verdict.lines[0]
    )


@pytest.mark.parametrize("accepted", [False, True])
def test_rejudge_returns_the_relative_verdict_beneath_a_ceiling_breach_that_alone_is_forgiven(accepted: bool) -> None:
    # why: the label decides the relative verdict; the scale leaves 500 s above the limit of 340 s
    relative = Breach(HIDDEN_WORDS, 625.0, 340.0)
    beneath = Verdict(accepted, (PLAIN,), relative)
    verdict = rejudge(Verdict(False, (CEILING_LINE,), CEILING, beneath), slowed(suite(), 1.25), [suite()] * 5)
    assert verdict == Verdict(
        accepted,
        (
            f"{PLAIN} Runner variance does not account for the breach: divided by the suite-wide scale of 1.25 the "
            "judged value is 500.0 s, still above its limit of 340.0 s. It accounts only for the ceiling breach, which "
            f"is forgiven: {CEILING.words}, and divided by the suite-wide scale of 1.25 the judged value is 500.0 s, "
            f"inside its limit of 600.0 s. {BASIS}median per-test ratio 1.25 over 55 comparable tests, 0 offenders, "
            "offender share 0.0% of the 103.4 s per-test excess (concentrated from 50%).",
        ),
        relative,
    )


def test_rejudge_keeps_a_ceiling_breach_that_the_scale_does_not_account_for_whatever_lies_beneath() -> None:
    beneath = Verdict(True, (PLAIN,), Breach(HIDDEN_WORDS, 625.0, 340.0))
    failed = Verdict(False, (CEILING_LINE,), CEILING, beneath)
    verdict = rejudge(failed, slowed(suite(), 1.02), [suite()] * 5)
    assert not verdict.passed
    assert verdict.breach == CEILING
    assert verdict.lines[0].startswith(
        f"{CEILING_LINE} Runner variance does not account for the breach: divided by the suite-wide scale of 1.02 the "
        f"judged value is 612.7 s, still above its limit of 600.0 s. {BASIS}"
    )
    assert "ceiling breach, which is forgiven" not in verdict.lines[0]


def test_rejudge_never_scales_a_faster_suite_up() -> None:
    # why: most tests ran faster and ten ran 3 s slower: the scale stays 1, so the judged value is unchanged
    current = slowed(suite(), 0.9)
    for index in range(10):
        current[f"tests.slow::test_{index}"] = suite()[f"tests.slow::test_{index}"] + 3.0
    verdict = rejudge(Verdict(False, (PLAIN,), BREACH), current, [suite()] * 5)
    assert not verdict.passed
    assert verdict.lines[0].startswith(
        f"{PLAIN} Runner variance does not account for the breach: divided by the suite-wide scale of 1.00 the judged "
        f"value is 351.0 s, still above its limit of 340.0 s. {BASIS}median per-test ratio 0.90 over 55 comparable "
    )


def test_rejudge_keeps_one_slow_test_that_a_floor_of_small_growth_dilutes() -> None:
    # why: one test 150 s slower is under half the excess beside 140 tests each 1.2 s slower; median unchanged
    current = big_suite()
    for index in range(140):
        current[f"tests.big::test_{index:03d}"] = 9.2
    current["tests.big::test_299"] = 158.0
    breach = Breach("suite test time was 918 s, above the 600 s ceiling", 918.0, 600.0)
    plain = "::error::Suite test time was 918 s, above the 600 s ceiling."
    verdict = rejudge(Verdict(False, (plain,), breach), current, [big_suite()] * 5)
    assert verdict == Verdict(
        False,
        (
            f"{plain} Runner variance does not account for the breach: divided by the suite-wide scale of 1.00 the "
            f"judged value is 918.0 s, still above its limit of 600.0 s. {BASIS}median per-test ratio 1.00 over 300 "
            "comparable tests, 1 offenders, offender share 47.2% of the 318.0 s per-test excess (concentrated from "
            "50%). Offenders: `tests.big::test_299` 8.0 s -> 158.0 s.",
        ),
        breach,
    )


@pytest.mark.parametrize("passed", [False, True])
def test_rejudge_without_evidence_keeps_the_verdict_and_says_why(passed: bool) -> None:
    plain = Verdict(passed, (PLAIN,), BREACH)
    verdict = rejudge(plain, slowed(suite(), 1.6), [{}] * 5)
    assert verdict == Verdict(
        passed,
        (f"{PLAIN} Runner variance could not be assessed: no baseline per-test times.",),
        BREACH,
    )
    few = rejudge(plain, {"tests.m::slow": 160.0}, [{"tests.m::slow": 5.0}] * 5)
    assert few.passed is passed
    assert few.lines[0].endswith(" Runner variance could not be assessed: only 1 comparable tests, fewer than 20.")


def test_rejudge_counts_only_the_baseline_records_with_per_test_times() -> None:
    breach = Breach("suite test time was 645 s, above the 600 s ceiling", 645.0, 600.0)
    failed = Verdict(False, ("::error::Suite test time was 645 s, above the 600 s ceiling.",), breach)
    verdict = rejudge(failed, slowed(suite(), 1.2), [suite(), {}, suite(), {}, suite()])
    assert verdict.passed
    assert "over the main baseline records (3 with per-test times): median per-test ratio 1.20" in verdict.lines[0]
