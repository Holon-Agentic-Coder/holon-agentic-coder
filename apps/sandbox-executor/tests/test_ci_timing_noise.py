"""Unit tests for scripts/ci_timing_noise.py: the per-test report and the runner-noise evidence of the timing gate."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
from pathlib import Path

from scripts.ci_timing_noise import format_report, per_test_report, top_offenders

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
