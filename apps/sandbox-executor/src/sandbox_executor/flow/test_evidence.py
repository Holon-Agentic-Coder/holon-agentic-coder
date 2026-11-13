"""Stage 3 test evidence as the flow reads it from the executor's summary row, and how Stage 4 packages it.

A null rate means "not measured" and is never a pass; a measured rate below 1.0, or a completed run that exited
non-zero, is a failed test result. A run that could not execute (``test_run_error``) is unmeasured, not failed.
"""

from __future__ import annotations

import math
from typing import Any

# The optional executions.jsonl summary-row keys that describe a Stage 3 test measurement.
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
    """Return a recorded test pass rate in [0, 1], or None when no usable measurement was recorded.

    ``None`` means "not measured": callers must never read it as a pass.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    rate = float(value)
    if math.isnan(rate) or rate < 0.0 or rate > 1.0:
        return None
    return rate


def _int(value: Any, minimum: int | None = None) -> int | None:
    """A recorded integer at or above ``minimum``, or None when it is absent, a boolean or out of range."""
    if isinstance(value, bool) or not isinstance(value, int) or (minimum is not None and value < minimum):
        return None
    return value


def _recorded_test_fields(summary_row: dict[str, Any]) -> dict[str, Any]:
    """The validated test measurement of a summary row; any malformed value is dropped as not measured."""
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
    """The agent's exit code, or the test run's when the agent succeeded and a completed run exited non-zero."""
    exit_code = int(payload.get("exit_code", 0) or 0)
    test_exit_code = payload.get("test_exit_code")
    if exit_code == 0 and isinstance(test_exit_code, int) and not payload.get("test_run_error"):
        return test_exit_code
    return exit_code


def _test_results_package(payload: dict[str, Any], pass_rate: float | None, exit_code: int) -> dict[str, Any]:
    """The review package's ``test_results``: evidence, source and counts; None for anything not measured."""
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
