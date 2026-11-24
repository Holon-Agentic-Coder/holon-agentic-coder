"""Unit tests for scripts/ci_timing_noise.py: the per-test report and the runner-noise evidence of the timing gate."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.ci_timing_noise import (
    Offender,
    baseline_medians,
    describe_evidence,
    describe_offenders,
    format_report,
    gather_evidence,
    insufficient_reason,
    per_test_report,
    top_offenders,
)

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
GATE_SCRIPT = SCRIPTS / "ci_timing_gate.py"
NOISE_SCRIPT = SCRIPTS / "ci_timing_noise.py"


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


def test_per_test_report_skipped_without_baseline_times() -> None:
    assert per_test_report({"m::slow": 30.0}, []) == [
        "Per-test report: skipped, no baseline records with per-test times yet."
    ]
    assert "skipped" in per_test_report({"m::slow": 30.0}, [{}, {}])[0]
    assert "no test grew" in per_test_report({"m::slow": 10.0}, [{"m::slow": 10.0}])[0]


# The gate as the workflows run it: a script file, started outside the repository, with Python 3.9 syntax


def script_environment() -> dict[str, str]:
    """Return the process environment without ``PYTHONPATH`` and without the variables GitHub Actions sets."""
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
    """Without the repository root on ``sys.path`` the package form fails and the gate falls back to the sibling."""
    code = "import ci_timing_gate; print(ci_timing_gate.per_test_report.__module__)"
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
    assert result.stdout.strip() == "ci_timing_noise"


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


# Runner-noise evidence: is a slowdown spread across the suite or concentrated in a few tests?


def suite() -> dict[str, float]:
    """Return a deterministic 60-test suite of 413.6 s: 5 tests of 0.02 s, 25 of 0.1 to 2.5 s and 30 of 4 to 21.4 s."""
    times = {f"tests.tiny::test_{i}": 0.02 for i in range(5)}
    times.update({f"tests.quick::test_{i}": round(0.1 * (i + 1), 1) for i in range(25)})
    times.update({f"tests.slow::test_{i}": round(4.0 + 0.6 * i, 1) for i in range(30)})
    return times


def slowed(times: dict[str, float], factor: float) -> dict[str, float]:
    """Return ``times`` with every test multiplied by ``factor``, as a slower runner would produce them."""
    return {test_id: seconds * factor for test_id, seconds in times.items()}


# The ten tests the per-test report of the failed ubuntu run of 2026-11-24 listed: (baseline median, current) seconds.
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
    """Ten tests hold 300 s of 317.5 s; their raw growth is 9 s each, their growth beyond the scale is zero."""
    times = {f"tests.heavy::test_{i}": 30.0 for i in range(10)}
    times.update({f"tests.light::test_{i}": 0.5 for i in range(35)})
    evidence = gather_evidence(slowed(times, 1.3), [times] * 5)
    assert evidence is not None
    assert evidence.median_ratio == pytest.approx(1.3)
    assert len(top_offenders(slowed(times, 1.3), [times])) == 0  # +30 % stays under the report's own +50 % bar
    assert evidence.offenders == ()
    assert not evidence.concentrated
    # The same suite at 1.6 times: every heavy test passes the raw report thresholds, none passes them beyond the scale.
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
    """A contended runner: every test 1.6 times slower, and ten unrelated sub-second tests at 6 to 13 s on top."""
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
    # 93.9 s after, less 5.2 s before scaled by 1.6; over the 0.6 * 413.6 s of the suite plus the 88.7 s of the ten.
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


def test_new_fast_tests_are_not_offenders() -> None:
    """A renamed module: forty quick tests look new, none passes the 5 s bar, so nothing is attributed to them."""
    current = slowed(suite(), 1.4)
    current.update({f"tests.renamed::test_{i}": 2.0 for i in range(40)})
    evidence = gather_evidence(current, [suite()] * 5)
    assert evidence is not None
    assert evidence.offenders == ()
    assert evidence.excess == pytest.approx(413.6 * 0.4 + 80.0)
    assert not evidence.concentrated


def test_at_most_ten_offenders_largest_first() -> None:
    current = suite()
    for index in range(12):
        current[f"tests.quick::test_{index}"] += 20.0 + index
    evidence = gather_evidence(current, [suite()] * 5)
    assert evidence is not None
    assert [row.test_id for row in evidence.offenders] == [f"tests.quick::test_{index}" for index in range(11, 1, -1)]
    assert evidence.attributed == pytest.approx(sum(20.0 + index for index in range(2, 12)))
    assert evidence.share == pytest.approx(evidence.attributed / (evidence.attributed + 41.0))
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
        "median per-test ratio 1.00 over 55 comparable tests, 2 offenders, offender share 100.0% of the 103.2 s "
        "per-test excess (concentrated from 50%)"
    )
    offenders = describe_offenders(evidence.offenders)
    assert offenders == "`tests.odd::test_a b c'd` new -> 70.0 s, `tests.quick::test_0` 0.1 s -> 33.3 s"
    for text in (fragment, offenders):
        assert "\n" not in text
        assert "\t" not in text
    assert offenders.count("`") == 4
