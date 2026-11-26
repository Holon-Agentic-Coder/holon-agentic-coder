"""The executor's test-measurement helper and the summary-row fields it contributes."""

import subprocess
from unittest.mock import MagicMock

from sandbox_executor.entrypoint import executor_test_run
from sandbox_executor.entrypoint.executor_test_run import measure_executor_tests, measurement_fields
from sandbox_executor.entrypoint.test_failure_loop import LoopOutcome
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


class _UnchangedLoop:
    def run(self, measurement, output):
        return LoopOutcome("blocked", measurement)


def test_passing_run_fields(monkeypatch) -> None:
    _fake(monkeypatch, "5 passed in 1s", 0)
    fields = measure_executor_tests("/w", _repo(), "success")
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
    fields = measure_executor_tests("/w", _repo(), "success", failure_loop=lambda *_: _UnchangedLoop())
    assert fields["test_pass_rate"] == 0.75
    assert fields["test_exit_code"] == 1


def test_unrunnable_run_records_null_and_error(monkeypatch) -> None:
    _fake(monkeypatch, "", 0, raises=FileNotFoundError(2, "missing", "uv"))
    fields = measure_executor_tests("/w", _repo(), "success", failure_loop=lambda *_: _UnchangedLoop())
    assert fields["test_pass_rate"] is None
    assert fields["test_run_error"]


def test_unmeasured_fields_keep_null_rate_and_error() -> None:
    from sandbox_executor.entrypoint.test_measurement import TestMeasurement

    fields = measurement_fields(TestMeasurement(None, None, None, None, 124, "timed out", ""))
    assert fields["test_pass_rate"] is None and fields["test_run_error"] == "timed out"


def test_non_success_status_is_not_measured(monkeypatch) -> None:
    _fake(monkeypatch, "", 0)
    assert measure_executor_tests("/w", _repo(), "failure") == {}


def test_failing_run_maps_the_failure_loop_outcome(monkeypatch, capsys) -> None:
    _fake(monkeypatch, "1 failed, 3 passed in 1s", 1)

    class _EnvironmentLoop:
        def run(self, measurement, output):
            rows = [{"action_id": "ACT-1"}]
            return LoopOutcome("blocked", measurement, "environment", rows, "environment failure", "opus-x")

    fields = measure_executor_tests("/w", _repo(), "success", failure_loop=lambda *_: _EnvironmentLoop())

    assert fields["test_pass_rate"] == 0.75
    assert fields["test_failure_classification"] == "environment"
    assert fields["test_failure_status"] == "blocked"
    assert fields["test_failure_rows"] == ["ACT-1"]
    assert fields["test_fix_tier"] == "opus-x"
    assert "Test failure blocked" in capsys.readouterr().err


def _request():
    from sandbox_executor.entrypoint.test_failure_loop import FixRequest

    return FixRequest("introduced", ("tests/test_a.py::test_x",), {}, "sig", "tier-x", 1)


def test_a_failed_fix_turn_verdict_stops_the_fix(monkeypatch) -> None:
    import pytest
    from sandbox_executor.entrypoint.conflict_resolver_verify import Verdict

    failed = Verdict("agent_failed", reason="agent turn exited with code 1")
    monkeypatch.setattr(executor_test_run, "run_agent_turn", lambda *_: failed)
    clock = executor_test_run._StageClock(None)
    fix = executor_test_run._fix_turn("/w", _repo(), executor_test_run.FixRunner("claude", "opus"), "b", clock)

    with pytest.raises(RuntimeError, match="exited with code 1"):
        fix(_request())


def test_the_fix_runner_reaches_the_fix_turn_and_an_alias_starts_at_its_tier(monkeypatch) -> None:
    turns: list[tuple[str, str]] = []
    monkeypatch.setattr(
        executor_test_run, "run_agent_turn", lambda _w, runner, model, _p: turns.append((runner, model))
    )
    repo = MagicMock()
    repo.run.return_value = MagicMock(returncode=0, stdout="E-1/branch")

    loop = executor_test_run.default_failure_loop("/w", repo, executor_test_run.FixRunner("claude", "opus"))
    assert loop.ports.fix_turn is not None
    loop.ports.fix_turn(_request())

    assert loop.ports.start_tier == loop.ports.ladder[-1] != "opus"
    assert turns == [("claude", "tier-x")]


def test_the_stage_clock_reads_the_stage_timer_and_reserves_one_run_and_one_fix() -> None:
    from sandbox_executor.budgets import StageTimer

    now = [0.0]
    timer = StageTimer("execute", 1000.0, clock=lambda: now[0])
    now[0] = 400.0
    clock = executor_test_run._StageClock(timer)
    clock.longest_run, clock.longest_fix = 150.0, 200.0

    assert clock.remaining() == 250.0
