import subprocess
from pathlib import Path
from unittest.mock import patch

from sandbox_executor.entrypoint import resolver_test_failure, review_loop
from sandbox_executor.entrypoint.test_failure_classify import RerunResult
from sandbox_executor.entrypoint.test_failure_loop import LoopPorts, TestFailureLoop
from sandbox_executor.entrypoint.test_failure_record import STAGE_4, TestFailureEvidence

ALPHA = "tests/test_a.py::test_alpha"
FAILING_OUTPUT = f"E       assert 1 == 2\nFAILED {ALPHA} - assert 1 == 2\n1 failed, 2 passed in 0.1s"


def _fake_run(args, cwd=None, env=None, check=True):
    if args[0] == "uv" and args[-1] == "test":
        return subprocess.CompletedProcess(args, 1, stdout=FAILING_OUTPUT, stderr="")
    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


def _install_loop(monkeypatch, recorded: list[TestFailureEvidence]) -> None:
    def record(evidence: TestFailureEvidence) -> dict:
        recorded.append(evidence)
        return {"action_id": f"ACT-{len(recorded)}"}

    def loop(_repo_dir: str) -> TestFailureLoop:
        return TestFailureLoop(
            LoopPorts(
                run_selection=lambda: (_ for _ in ()).throw(AssertionError("no full re-run expected")),
                rerun_ids=lambda ids, env: RerunResult(passed=False, failing_ids=(ALPHA,), exit_code=1),
                base_rerun=lambda ids: RerunResult(passed=True),
                record=record,
                stage=STAGE_4,
            )
        )

    monkeypatch.setattr(resolver_test_failure, "resolver_failure_loop", loop)


def test_failing_pre_push_tests_are_recorded_and_classified(tmp_path: Path, monkeypatch) -> None:
    recorded: list[TestFailureEvidence] = []
    _install_loop(monkeypatch, recorded)

    with patch.object(review_loop, "run_cmd", _fake_run):
        lint_ok, tests_ok, message = review_loop._verify_before_push(str(tmp_path))

    assert lint_ok and not tests_ok and message is not None
    assert message.startswith("Resolver pre-push test failed (exit code 1)")
    assert "Test failure classification: introduced; ledger rows: ACT-1." in message
    assert recorded and recorded[0].stage == STAGE_4 and recorded[0].failing_ids == (ALPHA,)


def test_a_broken_diagnosis_keeps_the_verification_error(tmp_path: Path, monkeypatch) -> None:
    def broken(_repo_dir: str) -> TestFailureLoop:
        raise RuntimeError("boom")

    monkeypatch.setattr(resolver_test_failure, "resolver_failure_loop", broken)

    with patch.object(review_loop, "run_cmd", _fake_run):
        _, tests_ok, message = review_loop._verify_before_push(str(tmp_path))

    assert not tests_ok and message is not None
    assert "Test failure classification" not in message


def test_the_default_loop_records_stage_4_rows_without_a_fix_turn(tmp_path: Path) -> None:
    loop = resolver_test_failure._default_resolver_failure_loop(str(tmp_path))

    assert loop.ports.stage == STAGE_4
    assert loop.ports.fix_turn is None
    assert loop.ports.record.args[0] == str(tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl")
