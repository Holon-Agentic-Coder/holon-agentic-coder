"""The review loop measures tests itself when no CI registers, attributes the source and never reads a measured
result as unmeasured."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.entrypoint.test_measurement import TestMeasurement
from sandbox_executor.flow import FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _test_evidence
from sandbox_executor.flow.review import local_tests
from sandbox_executor.flow.review.ci_evidence import apply_local_test_evidence

from tests.flow.github.test_ci import TestUnmeasuredTestsStopEarly as _Harness

HARNESS = _Harness()


def _measurement(rate: float | None, exit_code: int = 0, error: str | None = None) -> TestMeasurement:
    passed = None if rate is None else int(rate * 10)
    failed = None if rate is None else 10 - passed
    return TestMeasurement(rate, passed, failed, 0 if rate is not None else None, exit_code, error, "")


def _run_without_ci(tmp_path, monkeypatch, measurement: TestMeasurement, payload: dict[str, Any] | None = None):
    calls = MagicMock(return_value=measurement)
    monkeypatch.setattr(local_tests, "run_measurement", calls)
    ctx = HARNESS._ctx(tmp_path, payload if payload is not None else {"exit_code": 0, "test_pass_rate": None})
    res, _probe, _sleep = HARNESS._loop(ctx, lambda *a, **k: dict(HARNESS.LOCAL), gh=None)
    return res, calls


def test_no_ci_and_no_stage_3_rate_measures_locally_and_approves(tmp_path, monkeypatch):
    res, calls = _run_without_ci(tmp_path, monkeypatch, _measurement(1.0))

    calls.assert_called_once()
    assert res["consensus"]["approved"] is True
    assert res["ci_checks"]["test_source"] == "review_local"
    assert res["ci_checks"]["test_counts"] == {"passed": 10, "failed": 0, "errored": 0}
    assert res["tests_failed"] is False


def test_local_failure_is_a_failed_result_not_unmeasured(tmp_path, monkeypatch):
    res, _calls = _run_without_ci(tmp_path, monkeypatch, _measurement(0.8, exit_code=1))

    assert res["consensus"]["approved"] is False
    assert res["consensus"]["status"] != "not_measured"
    assert res["tests_failed"] is True
    assert res["ci_checks"]["test_source"] == "review_local"


def test_unrunnable_local_task_stays_not_measured_with_its_error(tmp_path, monkeypatch):
    res, _calls = _run_without_ci(tmp_path, monkeypatch, _measurement(None, 127, "uv was not found on PATH"))

    assert res["consensus"]["status"] == "not_measured"
    assert res["ci_checks"]["test_run_error"] == "uv was not found on PATH"
    assert res["tests_failed"] is False


def test_a_stage_3_rate_skips_the_local_run_and_is_attributed(tmp_path, monkeypatch):
    payload = {"exit_code": 0, "test_pass_rate": 1.0, "tests_passed": 4, "tests_failed": 0, "tests_errored": 0}
    res, calls = _run_without_ci(tmp_path, monkeypatch, _measurement(0.0, 1), payload)

    calls.assert_not_called()
    assert res["consensus"]["approved"] is True
    assert res["ci_checks"]["test_source"] == "stage_3"
    assert res["ci_checks"]["test_counts"]["passed"] == 4


def test_a_github_probe_is_attributed_to_ci():
    probe = {"source": "github_cli", "total": 1, "passed": 1, "failed": 0, "pending": 0, "all_passed": True}
    local_tests._attribute_test_source(probe, None, None)
    assert probe["test_source"] == "ci"


def test_local_result_is_cached_per_head_and_remeasured_after_a_push(monkeypatch):
    run = MagicMock(return_value=_measurement(1.0))
    monkeypatch.setattr(local_tests, "run_measurement", run)
    heads = iter(["aaa", "aaa", "bbb"])
    git = MagicMock(side_effect=lambda *a, **k: MagicMock(returncode=0, stdout=next(heads)))
    with patch.object(local_tests, "run_git", git):
        tester = local_tests._ReviewLocalTests("/w")
        tester.measure()
        tester.measure()
        tester.measure()
    assert run.call_count == 2


@pytest.mark.parametrize("rate", [0.01, 0.5, 0.99, 1.0])
@pytest.mark.parametrize("dry_run", [False, True])
def test_a_measured_rate_is_never_marked_unmeasured(rate, dry_run):
    probe = dict(HARNESS.LOCAL)
    blocked = apply_local_test_evidence(probe, rate < 1.0, rate, dry_run, True)
    assert blocked is False
    assert probe.get("measured") is not False
    assert all(check["state"] != "NOT_MEASURED" for check in probe["checks"])


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"test_pass_rate": 1.0, "test_exit_code": 0}, (1.0, 0)),
        ({"test_pass_rate": 1.0, "test_exit_code": 1}, (1.0, 1)),
        ({"test_pass_rate": 0.5, "test_exit_code": 1}, (0.5, 1)),
        ({"test_pass_rate": None, "test_exit_code": 127, "test_run_error": "no uv"}, (None, 0)),
        ({"test_pass_rate": None}, (None, 0)),
        ({"test_pass_rate": 1.0, "test_exit_code": 0, "exit_code": 2}, (1.0, 2)),
    ],
)
def test_stage_3_evidence_matrix(payload, expected):
    result = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload=payload)
    assert _test_evidence(result) == expected
