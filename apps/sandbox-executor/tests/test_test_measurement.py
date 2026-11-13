"""Hermetic tests for the test-measurement module: a fake runner stands in for ``uv run task test``."""

import subprocess

import pytest

from sandbox_executor.entrypoint.test_measurement import parse_pytest_counts, run_test_task
from tests.leak_assertions import assert_no_leak, make_gh_probe


def _runner(stdout: str = "", returncode: int = 0, stderr: str = ""):
    def run(argv, cwd, timeout):
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    return run


def _raising(exc: BaseException):
    def run(argv, cwd, timeout):
        raise exc

    return run


def test_passing_run_records_one() -> None:
    m = run_test_task("/w", _runner("====== 10 passed in 1.20s ======\n"))
    assert (m.rate, m.passed, m.failed, m.errored, m.exit_code, m.run_error) == (1.0, 10, 0, 0, 0, None)


def test_failing_run_records_real_fraction_and_exit_code() -> None:
    m = run_test_task("/w", _runner("===== 2 failed, 8 passed, 3 skipped in 4.00s =====\n", returncode=1))
    assert m.rate == pytest.approx(0.8)
    assert (m.passed, m.failed, m.errored, m.exit_code) == (8, 2, 0, 1)


def test_errors_count_against_the_rate() -> None:
    m = run_test_task("/w", _runner("1 failed, 7 passed, 2 errors, 1 warning in 2.0s (0:00:02)\n", returncode=1))
    assert m.rate == pytest.approx(0.7)
    assert m.errored == 2


def test_quiet_and_xdist_output_parse() -> None:
    out = "[gw0] PASSED tests/a.py::t\n8 passed, 1 xfailed in 0.5s\n"
    assert parse_pytest_counts(out) == (8, 0, 0)


@pytest.mark.parametrize("output", ["no tests ran in 0.01s\n", "", "collected 0 items\n", "garbage"])
def test_unmeasurable_output_is_null_with_error(output: str) -> None:
    m = run_test_task("/w", _runner(output))
    assert m.rate is None
    assert m.run_error


def test_missing_uv_is_null_with_error() -> None:
    m = run_test_task("/w", _raising(FileNotFoundError(2, "No such file", "uv")))
    assert m.rate is None
    assert m.exit_code == 127
    assert "uv" in (m.run_error or "")


def test_timeout_is_null_with_error() -> None:
    m = run_test_task("/w", _raising(subprocess.TimeoutExpired(["uv"], 5)), timeout=5)
    assert m.rate is None
    assert m.exit_code != 0
    assert "timed out" in (m.run_error or "")


def test_output_is_redacted() -> None:
    probe = make_gh_probe()
    m = run_test_task("/w", _runner(f"token: {probe}\n1 failed in 1s\n", returncode=1, stderr=f"auth {probe}"))
    assert_no_leak(probe, m.output_excerpt, m.run_error or "")


def test_null_rate_never_pairs_with_clean_exit_and_no_error() -> None:
    for out, code in (("", 0), ("no tests ran in 0.1s", 5), ("3 passed in 1s", 0), ("1 failed in 1s", 1)):
        m = run_test_task("/w", _runner(out, returncode=code))
        assert (m.rate is None) == bool(m.run_error)
