"""The executor's test-measurement helper and the summary-row fields it contributes."""

import subprocess
from unittest.mock import MagicMock

from sandbox_executor.entrypoint import executor_test_run
from sandbox_executor.entrypoint.executor_test_run import measure_executor_tests, measurement_fields
from sandbox_executor.entrypoint.test_measurement import run_test_task


def _repo() -> MagicMock:
    repo = MagicMock()
    repo.run.return_value = MagicMock(returncode=0, stdout="")
    return repo


def _fake(monkeypatch, stdout: str, returncode: int, raises: BaseException | None = None) -> None:
    def fake(workspace, runner=None, timeout=None):
        def run(argv, cwd, limit):
            if raises:
                raise raises
            return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

        return run_test_task(workspace, run, timeout=1)

    monkeypatch.setattr(executor_test_run, "run_test_task", fake)


def test_passing_run_fields(monkeypatch) -> None:
    _fake(monkeypatch, "5 passed in 1s", 0)
    fields = measure_executor_tests("/w", _repo())
    assert fields["test_pass_rate"] == 1.0
    assert (fields["tests_passed"], fields["tests_failed"], fields["tests_errored"], fields["test_exit_code"]) == (
        5,
        0,
        0,
        0,
    )
    assert fields["test_source"] == "stage_3"
    assert "test_run_error" not in fields


def test_failing_run_fields(monkeypatch) -> None:
    _fake(monkeypatch, "1 failed, 3 passed in 1s", 1)
    fields = measure_executor_tests("/w", _repo())
    assert fields["test_pass_rate"] == 0.75
    assert fields["test_exit_code"] == 1


def test_unrunnable_run_records_null_and_error(monkeypatch) -> None:
    _fake(monkeypatch, "", 0, raises=FileNotFoundError(2, "missing", "uv"))
    fields = measure_executor_tests("/w", _repo())
    assert fields["test_pass_rate"] is None
    assert fields["test_run_error"]


def test_fields_are_json_serialisable_keys() -> None:
    from sandbox_executor.entrypoint.test_measurement import TestMeasurement

    fields = measurement_fields(TestMeasurement(None, None, None, None, 124, "timed out", ""))
    assert fields["test_pass_rate"] is None and fields["test_run_error"] == "timed out"
