from __future__ import annotations

from typing import Any

from sandbox_executor.entrypoint.test_measurement import TestMeasurement, run_test_task

TEST_SOURCE_STAGE_3 = "stage_3"
_MAX_LOGGED_STRAY_PATHS = 5


def measurement_fields(measurement: TestMeasurement) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "test_pass_rate": measurement.rate,
        "tests_passed": measurement.passed,
        "tests_failed": measurement.failed,
        "tests_errored": measurement.errored,
        "test_exit_code": measurement.exit_code,
        "test_source": TEST_SOURCE_STAGE_3,
    }
    if measurement.run_error:
        fields["test_run_error"] = measurement.run_error
    return fields


def _changed_paths(repo: Any) -> set[str]:
    result = repo.run(["status", "--porcelain"], check=False)
    return set(result.stdout.splitlines()) if result.returncode == 0 else set()


def measure_executor_tests(workspace: str, repo: Any, exec_status: str) -> dict[str, Any]:
    if exec_status != "success":
        return {}
    before = _changed_paths(repo)
    measurement = run_test_task(workspace)
    stray = sorted(_changed_paths(repo) - before)
    if measurement.rate is None:
        print(f"Test measurement: not measured ({measurement.run_error}); exit code {measurement.exit_code}")
    else:
        print(
            f"Test measurement: rate {measurement.rate:.3f} ({measurement.passed} passed, {measurement.failed} failed, "
            f"{measurement.errored} errored); exit code {measurement.exit_code}"
        )
    if stray:
        logged_paths = "; ".join(stray[:_MAX_LOGGED_STRAY_PATHS])
        print(f"Warning: the test run changed {len(stray)} path(s) in the worktree: {logged_paths}")
    return measurement_fields(measurement)
