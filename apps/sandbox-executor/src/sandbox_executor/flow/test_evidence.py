from __future__ import annotations

import math
from typing import Any

TEST_MEASUREMENT_KEYS = (
    "test_pass_rate",
    "tests_passed",
    "tests_failed",
    "tests_errored",
    "test_exit_code",
    "test_run_error",
    "test_source",
)


def recorded_test_pass_rate(value: Any) -> float | None:
    # why: None means not measured and is never a pass, so an invalid rate is reported as unmeasured
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    rate = float(value)
    if math.isnan(rate) or rate < 0.0 or rate > 1.0:
        return None
    return rate


def _int(value: Any, minimum: int | None = None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or (minimum is not None and value < minimum):
        return None
    return value


def _recorded_test_fields(summary_row: dict[str, Any]) -> dict[str, Any]:
    error, source = summary_row.get("test_run_error"), summary_row.get("test_source")
    return {
        "test_pass_rate": recorded_test_pass_rate(summary_row.get("test_pass_rate")),
        "tests_passed": _int(summary_row.get("tests_passed"), 0),
        "tests_failed": _int(summary_row.get("tests_failed"), 0),
        "tests_errored": _int(summary_row.get("tests_errored"), 0),
        "test_exit_code": _int(summary_row.get("test_exit_code")),
        "test_run_error": error if isinstance(error, str) and error else None,
        "test_source": source if isinstance(source, str) else None,
    }


def _effective_exit_code(payload: dict[str, Any]) -> int:
    exit_code = int(payload.get("exit_code", 0) or 0)
    test_exit_code = payload.get("test_exit_code")
    # why: a run that could not execute (test_run_error) is unmeasured, not failed
    if exit_code == 0 and isinstance(test_exit_code, int) and not payload.get("test_run_error"):
        return test_exit_code
    return exit_code


def _test_results_package(payload: dict[str, Any], pass_rate: float | None, exit_code: int) -> dict[str, Any]:
    return {
        "exit_code": exit_code,
        "pass_rate": pass_rate,
        "measured": pass_rate is not None,
        "passed": False if exit_code != 0 else (None if pass_rate is None else pass_rate == 1.0),
        "source": payload.get("test_source"),
        "passed_count": payload.get("tests_passed"),
        "failed_count": payload.get("tests_failed"),
        "errored_count": payload.get("tests_errored"),
        "run_error": payload.get("test_run_error"),
    }
