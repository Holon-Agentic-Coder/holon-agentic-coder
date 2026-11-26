"""The review loop measures tests itself when no CI registers, attributes the source and never reads a measured
result as unmeasured."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.entrypoint.test_failure_loop import LoopOutcome, TestFailureBlocked
from sandbox_executor.entrypoint.test_measurement import TestMeasurement
from sandbox_executor.flow import FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _test_evidence
from sandbox_executor.flow.review import local_tests
from sandbox_executor.flow.review.ci_evidence import apply_local_test_evidence
from sandbox_executor.flow.review.ci_outcome import gate_ci_flags
from sandbox_executor.review_report import _ci_summary

from tests.flow.github.test_ci import TestUnmeasuredTestsStopEarly as _Harness

HARNESS = _Harness()


@pytest.fixture(autouse=True)
def no_failure_loop(monkeypatch):
    monkeypatch.setattr(local_tests, "_review_failure_loop", lambda *_: None)


def _measurement(rate: float | None, exit_code: int = 0, error: str | None = None) -> TestMeasurement:
    passed = None if rate is None else int(rate * 10)
    failed = None if rate is None else 10 - passed
    return TestMeasurement(rate, passed, failed, 0 if rate is not None else None, exit_code, error, "")


def _run_without_ci(tmp_path, monkeypatch, measurement: TestMeasurement, payload: dict[str, Any] | None = None):
    calls = MagicMock(return_value=measurement)
    monkeypatch.setattr(local_tests, "run_measurement", calls)
    ctx = HARNESS._ctx(tmp_path, payload if payload is not None else {"exit_code": 0, "test_pass_rate": None})
    res, _probe, _sleep = HARNESS._loop(ctx, dict(HARNESS.LOCAL), gh=None, gh_available=True, ci_registration_timeout=0)
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
    payload = {
        "head_sha": "stage3-head",
        "exit_code": 0,
        "test_pass_rate": 1.0,
        "tests_passed": 4,
        "tests_failed": 0,
        "tests_errored": 0,
    }
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


def test_current_head_passes_after_stage_3_failed(tmp_path, monkeypatch):
    run = MagicMock(return_value=_measurement(1.0, 0))
    monkeypatch.setattr(local_tests, "run_measurement", run)
    ctx = HARNESS._ctx(tmp_path, {"test_pass_rate": 0.5, "tests_failed": 1, "head_sha": "stage3sha"})
    with patch("sandbox_executor.flow.review.local_tests.run_git") as mock_git:
        mock_git.return_value = MagicMock(returncode=0, stdout="currentsha123\n")
        gate = local_tests._ReviewTestGate(ctx, str(tmp_path))
        probe = {"source": "local_tests"}
        rate, failed = gate.evidence(probe)

    run.assert_called_once()
    assert rate == 1.0
    assert failed is False
    assert probe["test_source"] == "review_local"
    assert probe["test_head_sha"] == "currentsha123"
    assert any("[Review Loop] Test evidence for current: review_local" in log for log in ctx.logs)


def test_current_head_still_fails(tmp_path, monkeypatch):
    run = MagicMock(return_value=_measurement(0.5, 1))
    monkeypatch.setattr(local_tests, "run_measurement", run)
    ctx = HARNESS._ctx(tmp_path, {"test_pass_rate": 0.5, "tests_failed": 1, "head_sha": "stage3sha"})
    with patch("sandbox_executor.flow.review.local_tests.run_git") as mock_git:
        mock_git.return_value = MagicMock(returncode=0, stdout="currentsha123\n")
        gate = local_tests._ReviewTestGate(ctx, str(tmp_path))
        probe = {"source": "local_tests"}
        rate, failed = gate.evidence(probe)

    assert rate == 0.5
    assert failed is True
    assert probe["test_source"] == "review_local"


def test_green_ci_on_head(tmp_path, monkeypatch):
    run = MagicMock()
    monkeypatch.setattr(local_tests, "run_measurement", run)
    ctx = HARNESS._ctx(tmp_path, {"test_pass_rate": 0.5, "tests_failed": 1, "head_sha": "stage3sha"})
    with patch("sandbox_executor.flow.review.local_tests.run_git") as mock_git:
        mock_git.return_value = MagicMock(returncode=0, stdout="headsha123456\n")
        gate = local_tests._ReviewTestGate(ctx, str(tmp_path), pr_head=lambda _t: "headsha123456")
        probe = {"source": "github_cli", "all_passed": True, "pending": 0}
        rate, failed = gate.evidence(probe)

    run.assert_not_called()
    assert rate == 1.0
    assert failed is False
    assert probe["test_source"] == "ci_head"
    assert any("[Review Loop] Test evidence for headsha: ci_head" in log for log in ctx.logs)


def test_green_ci_on_older_head_falls_back_to_local_run(tmp_path, monkeypatch):
    run = MagicMock(return_value=_measurement(1.0, 0))
    monkeypatch.setattr(local_tests, "run_measurement", run)
    ctx = HARNESS._ctx(tmp_path, {"test_pass_rate": 0.5, "tests_failed": 1, "head_sha": "stage3sha"})
    with patch("sandbox_executor.flow.review.local_tests.run_git") as mock_git:
        mock_git.return_value = MagicMock(returncode=0, stdout="currenthead999\n")
        gate = local_tests._ReviewTestGate(ctx, str(tmp_path), pr_head=lambda _t: "olderhead111")
        probe = {"source": "github_cli", "all_passed": True, "pending": 0}
        rate, failed = gate.evidence(probe)

    run.assert_called_once()
    assert rate == 1.0
    assert failed is False
    assert probe["test_source"] == "review_local"


def test_resolver_evidence_accepted_and_cleared_on_head_change(tmp_path, monkeypatch):
    run = MagicMock(return_value=_measurement(1.0, 0))
    monkeypatch.setattr(local_tests, "run_measurement", run)
    ctx = HARNESS._ctx(tmp_path, {"test_pass_rate": 0.5, "tests_failed": 1, "head_sha": "stage3sha"})
    with patch("sandbox_executor.flow.review.local_tests.run_git") as mock_git:
        mock_git.return_value = MagicMock(returncode=0, stdout="head1\n")
        gate = local_tests._ReviewTestGate(ctx, str(tmp_path))
        disp = [{"id": "FIND-CI-001", "disposition": "rejected", "evidence": "tests/test_app.py::test_ok passed"}]
        gate.accept_resolver_evidence("head1", disp)

        probe = {"source": "local_tests"}
        rate, failed = gate.evidence(probe)
        assert rate == 1.0
        assert failed is False
        assert probe["test_source"] == "resolver_evidence"
        assert any("[Review Loop] Test evidence for head1: resolver_evidence" in log for log in ctx.logs)

        # Head moves to head2 where tests fail
        run.return_value = _measurement(0.5, 1)
        mock_git.return_value = MagicMock(returncode=0, stdout="head2\n")
        probe2 = {"source": "local_tests"}
        rate2, failed2 = gate.evidence(probe2)
        assert rate2 == 0.5
        assert failed2 is True
        assert probe2["test_source"] == "review_local"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3 passed", True),
        ("0 failed", True),
        ("10 passed, 0 failed", True),
        ("10 failed", False),
        ("20 failed, 1 error", False),
        ("1 passed, 10 failed", False),
        ("1 passed, 1 error", False),
        ("2 passed, 3 errors", False),
        ("5 passed, 1 errored", False),
        ("0 passed", False),
        ("0 passed, 0 failed", False),
        ("not passed", False),
        ("0 failed, 1 error", False),
    ],
)
def test_pass_marker_reads_only_a_zero_failure_count(text, expected):
    assert local_tests.has_pass_marker(text) is expected


def test_resolver_evidence_is_skipped_when_the_checkout_moved(tmp_path, monkeypatch):
    run = MagicMock(return_value=_measurement(1.0, 0))
    monkeypatch.setattr(local_tests, "run_measurement", run)
    ctx = HARNESS._ctx(tmp_path, {"test_pass_rate": 0.5, "tests_failed": 1, "head_sha": "stage3sha"})
    with patch("sandbox_executor.flow.review.local_tests.run_git") as mock_git:
        mock_git.return_value = MagicMock(returncode=0, stdout="pushedhead\n")
        gate = local_tests._ReviewTestGate(ctx, str(tmp_path))
        disp = [{"id": "FIND-CI-001", "disposition": "rejected", "evidence": "tests/test_app.py::test_ok passed"}]
        gate.accept_resolver_evidence("prepushhead", disp)

    run.assert_not_called()
    assert gate._resolver_heads == set()


def test_missing_stage_3_head_measures_locally(tmp_path, monkeypatch):
    run = MagicMock(return_value=_measurement(1.0, 0))
    monkeypatch.setattr(local_tests, "run_measurement", run)
    ctx = HARNESS._ctx(tmp_path, {"exit_code": 0, "test_pass_rate": 1.0})
    gate = local_tests._ReviewTestGate(ctx, str(tmp_path))
    probe = {"source": "local_tests"}
    gate.evidence(probe)

    run.assert_called_once()
    assert probe["test_source"] == "review_local"


def test_pr_head_is_not_queried_for_non_github_or_resolver_covered_heads(tmp_path, monkeypatch):
    run = MagicMock(return_value=_measurement(1.0, 0))
    monkeypatch.setattr(local_tests, "run_measurement", run)
    pr_head = MagicMock(return_value="head1")
    ctx = HARNESS._ctx(tmp_path, {"test_pass_rate": 0.5, "tests_failed": 1, "head_sha": "stage3sha"})
    with patch("sandbox_executor.flow.review.local_tests.run_git") as mock_git:
        mock_git.return_value = MagicMock(returncode=0, stdout="head1\n")
        gate = local_tests._ReviewTestGate(ctx, str(tmp_path), pr_head=pr_head)

        # Non-github probe and failing github probe skip pr_head lookup.
        gate.evidence({"source": "local_tests"})
        gate.evidence({"source": "github_cli", "all_passed": False, "pending": 0})
        pr_head.assert_not_called()

        # Accepted resolver head also skips pr_head even when github_cli is green, and attaches test_counts.
        disp = [{"id": "FIND-CI-001", "disposition": "rejected", "evidence": "tests/test_app.py::test_ok passed"}]
        gate.accept_resolver_evidence("head1", disp)
        probe = {"source": "github_cli", "all_passed": True, "pending": 0}
        rate, failed = gate.evidence(probe)

    pr_head.assert_not_called()
    assert (rate, failed) == (1.0, False)
    assert probe["test_source"] == "resolver_evidence"
    assert probe["test_counts"] == {"passed": 10, "failed": 0, "errored": 0}


def test_advance_ledger_head_preserves_resolver_evidence_and_test_counts(tmp_path, monkeypatch):
    run = MagicMock(return_value=_measurement(1.0, 0))
    monkeypatch.setattr(local_tests, "run_measurement", run)
    ctx = HARNESS._ctx(tmp_path, {"test_pass_rate": 0.5, "tests_failed": 1, "head_sha": "stage3sha"})
    with patch("sandbox_executor.flow.review.local_tests.run_git") as mock_git:
        mock_git.return_value = MagicMock(returncode=0, stdout="code-sha\n")
        gate = local_tests._ReviewTestGate(ctx, str(tmp_path))
        disp = [{"id": "FIND-CI-001", "disposition": "rejected", "evidence": "tests/test_app.py::test_ok passed"}]
        gate.accept_resolver_evidence("code-sha", disp)

        gate.advance_ledger_head("code-sha", "ledger-sha")
        mock_git.return_value = MagicMock(returncode=0, stdout="ledger-sha\n")
        probe = {"source": "local_tests"}
        rate, failed = gate.evidence(probe)

    run.assert_called_once()
    assert (rate, failed) == (1.0, False)
    assert probe["test_source"] == "resolver_evidence"
    assert probe["test_head_sha"] == "ledger-sha"
    assert probe["test_counts"] == {"passed": 10, "failed": 0, "errored": 0}


def _green_github_probe(total: int = 4) -> dict[str, Any]:
    return {
        "source": "github_cli",
        "total": total,
        "passed": total,
        "failed": 0,
        "pending": 0,
        "skipped": 0,
        "all_passed": True,
        "checks": [{"name": f"check-{i}", "bucket": "pass", "state": "SUCCESS"} for i in range(total)],
    }


def test_stale_github_pass_does_not_mask_unmeasured_review_local(tmp_path, monkeypatch):
    run = MagicMock(return_value=_measurement(None, 127, "uv missing"))
    monkeypatch.setattr(local_tests, "run_measurement", run)
    ctx = HARNESS._ctx(tmp_path, {"test_pass_rate": 1.0, "exit_code": 0, "head_sha": "stage3sha"})
    with patch("sandbox_executor.flow.review.local_tests.run_git") as mock_git:
        mock_git.return_value = MagicMock(returncode=0, stdout="currenthead999\n")
        gate = local_tests._ReviewTestGate(ctx, str(tmp_path), pr_head=lambda _t: "olderhead111")
        probe = _green_github_probe()
        rate, failed = gate.evidence(probe)

    assert (rate, failed) == (None, False)
    assert probe["source"] == "local_tests"
    assert probe["test_source"] == "review_local"
    assert probe["test_run_error"] == "uv missing"
    assert apply_local_test_evidence(probe, failed, rate, dry_run=False, unmeasured_blocks=True) is True
    assert probe["all_passed"] is False
    assert probe["measured"] is False
    assert gate_ci_flags(probe, failed) == (None, True)
    assert _ci_summary(probe) == "CI unmeasured (source: review_local)"


@pytest.mark.parametrize(
    ("stage_3_payload", "local_outcome", "expected_source"),
    [
        (
            {"test_pass_rate": 1.0, "exit_code": 0, "head_sha": "stage3sha"},
            _measurement(0.5, 1),
            "review_local",
        ),
        (
            {"test_pass_rate": 0.5, "tests_failed": 1, "exit_code": 1, "head_sha": "currenthead999"},
            _measurement(1.0, 0),
            "stage_3",
        ),
    ],
)
def test_stale_github_pass_does_not_mask_local_or_stage3_failure(
    tmp_path, monkeypatch, stage_3_payload, local_outcome, expected_source
):
    run = MagicMock(return_value=local_outcome)
    monkeypatch.setattr(local_tests, "run_measurement", run)
    ctx = HARNESS._ctx(tmp_path, stage_3_payload)
    with patch("sandbox_executor.flow.review.local_tests.run_git") as mock_git:
        mock_git.return_value = MagicMock(returncode=0, stdout="currenthead999\n")
        gate = local_tests._ReviewTestGate(ctx, str(tmp_path), pr_head=lambda _t: "olderhead111")
        probe = _green_github_probe()
        rate, failed = gate.evidence(probe)

    assert rate == 0.5
    assert failed is True
    assert probe["source"] == "local_tests"
    assert probe["test_source"] == expected_source
    assert apply_local_test_evidence(probe, failed, rate, dry_run=False, unmeasured_blocks=False) is False
    assert probe["all_passed"] is False
    assert probe["failed"] == 1
    assert gate_ci_flags(probe, failed) == (False, True)
    assert _ci_summary(probe) == f"CI 0/1 passed, 1 failing, 0 pending (source: {expected_source})"


def test_non_passing_github_probe_is_preserved_when_review_local_runs(tmp_path, monkeypatch):
    run = MagicMock(return_value=_measurement(1.0, 0))
    monkeypatch.setattr(local_tests, "run_measurement", run)
    ctx = HARNESS._ctx(tmp_path, {"test_pass_rate": None, "exit_code": 0, "head_sha": "stage3sha"})
    pending = {
        "source": "github_cli",
        "total": 1,
        "passed": 0,
        "failed": 0,
        "pending": 1,
        "skipped": 0,
        "all_passed": False,
        "checks": [{"name": "ci", "bucket": "pending", "state": "IN_PROGRESS"}],
    }
    failing = {
        "source": "github_cli",
        "total": 1,
        "passed": 0,
        "failed": 1,
        "pending": 0,
        "skipped": 0,
        "all_passed": False,
        "checks": [{"name": "lint", "bucket": "fail", "state": "FAILURE"}],
    }
    with patch("sandbox_executor.flow.review.local_tests.run_git") as mock_git:
        mock_git.return_value = MagicMock(returncode=0, stdout="currenthead999\n")
        gate = local_tests._ReviewTestGate(ctx, str(tmp_path), pr_head=lambda _t: "olderhead111")
        gate.evidence(pending)
        gate.evidence(failing)

    assert pending["source"] == "github_cli"
    assert pending["pending"] == 1
    assert pending["test_source"] == "review_local"
    assert failing["source"] == "github_cli"
    assert failing["failed"] == 1
    assert failing["test_source"] == "review_local"


def test_resolver_evidence_preserves_green_github_all_passed(tmp_path, monkeypatch):
    run = MagicMock(return_value=_measurement(1.0, 0))
    monkeypatch.setattr(local_tests, "run_measurement", run)
    ctx = HARNESS._ctx(tmp_path, {"test_pass_rate": 0.5, "tests_failed": 1, "head_sha": "stage3sha"})
    with patch("sandbox_executor.flow.review.local_tests.run_git") as mock_git:
        mock_git.return_value = MagicMock(returncode=0, stdout="head1\n")
        gate = local_tests._ReviewTestGate(ctx, str(tmp_path), pr_head=lambda _t: "olderhead111")
        disp = [{"id": "FIND-CI-001", "disposition": "rejected", "evidence": "tests/test_app.py::test_ok passed"}]
        gate.accept_resolver_evidence("head1", disp)
        probe = _green_github_probe(total=3)
        rate, failed = gate.evidence(probe)

    assert (rate, failed) == (1.0, False)
    assert probe["all_passed"] is True
    assert probe["test_source"] == "resolver_evidence"
    assert apply_local_test_evidence(probe, failed, rate, dry_run=False, unmeasured_blocks=True) is False
    assert gate_ci_flags(probe, failed) == (True, False)


@pytest.mark.parametrize("first_remote", [None, "olderhead111"])
def test_pr_head_retries_stale_or_missing_lookup_until_matched(tmp_path, monkeypatch, first_remote):
    run = MagicMock(return_value=_measurement(0.5, 1))
    monkeypatch.setattr(local_tests, "run_measurement", run)
    pr_head = MagicMock(side_effect=[first_remote, "head1", "unexpected-third-call"])
    ctx = HARNESS._ctx(tmp_path, {"test_pass_rate": 0.5, "tests_failed": 1, "head_sha": "stage3sha"})
    with patch("sandbox_executor.flow.review.local_tests.run_git") as mock_git:
        mock_git.return_value = MagicMock(returncode=0, stdout="head1\n")
        gate = local_tests._ReviewTestGate(ctx, str(tmp_path), pr_head=pr_head)

        rate1, failed1 = gate.evidence(_green_github_probe())
        assert (rate1, failed1) == (0.5, True)
        assert pr_head.call_count == 1
        run.assert_called_once()

        # On unchanged local_head, once GitHub catches up to head1, ci_head succeeds and caches head1.
        probe2 = _green_github_probe()
        rate2, failed2 = gate.evidence(probe2)
        assert (rate2, failed2) == (1.0, False)
        assert probe2["test_source"] == "ci_head"
        assert pr_head.call_count == 2

        # Once matched to local_head, subsequent lookups reuse the cached PR head without re-querying.
        probe3 = _green_github_probe()
        rate3, failed3 = gate.evidence(probe3)
        assert (rate3, failed3) == (1.0, False)
        assert probe3["test_source"] == "ci_head"
        assert pr_head.call_count == 2


class _ScriptedLoop:
    def __init__(self, status: str, classification: str, measurement: TestMeasurement | None = None) -> None:
        self.status, self.classification, self.measurement = status, classification, measurement
        self.runs: list[TestMeasurement] = []
        self.outputs: list[str] = []

    def run(self, measurement: TestMeasurement, output: str) -> LoopOutcome:
        self.runs.append(measurement)
        self.outputs.append(output)
        return LoopOutcome(
            self.status,
            self.measurement or measurement,
            self.classification,
            [{"action_id": "ACT-1"}],
            f"{self.classification} failure",
            None,
            ("tests/test_a.py::test_alpha",),
            {"tests/test_a.py::test_alpha": "E assert 1 == 2"},
            "sig",
        )


def test_an_environment_failure_stops_the_gate_with_the_evidence(tmp_path, monkeypatch):
    loop = _ScriptedLoop("blocked", "environment")
    monkeypatch.setattr(local_tests, "_review_failure_loop", lambda *_: loop)

    with pytest.raises(TestFailureBlocked, match=r"tests/test_a\.py::test_alpha"):
        _run_without_ci(tmp_path, monkeypatch, _measurement(0.8, exit_code=1))

    assert len(loop.runs) == 1


def test_a_flaky_failure_that_passes_on_rerun_is_green(tmp_path, monkeypatch):
    loop = _ScriptedLoop("flaky_passed", "flaky", _measurement(1.0))
    monkeypatch.setattr(local_tests, "_review_failure_loop", lambda *_: loop)

    res, _calls = _run_without_ci(tmp_path, monkeypatch, _measurement(0.8, exit_code=1))

    assert res["tests_failed"] is False
    assert res["consensus"]["approved"] is True


def test_an_introduced_failure_reaches_the_resolver_with_ids_and_excerpts(monkeypatch):
    loop = _ScriptedLoop("blocked", "introduced")
    monkeypatch.setattr(local_tests, "run_measurement", MagicMock(return_value=_measurement(0.8, exit_code=1)))
    tester = local_tests._ReviewLocalTests("/w", failure_loop=lambda: loop)
    ci_status: dict[str, Any] = {}

    outcome = tester.measure()
    local_tests._attach_test_failure(ci_status, tester.loop_outcomes["stage3-head"])

    assert outcome.failed is True
    assert ci_status["test_failure"]["classification"] == "introduced"
    assert ci_status["test_failure"]["failing_ids"] == ["tests/test_a.py::test_alpha"]
    assert ci_status["test_failure"]["excerpts"] == {"tests/test_a.py::test_alpha": "E assert 1 == 2"}
    assert ci_status["test_failure"]["rows"] == ["ACT-1"]


def test_an_unmeasured_head_run_starts_no_failure_loop(monkeypatch):
    loop = _ScriptedLoop("blocked", "environment")
    monkeypatch.setattr(local_tests, "run_measurement", MagicMock(return_value=_measurement(None, 127, "uv missing")))

    local_tests._ReviewLocalTests("/w", failure_loop=lambda: loop).measure()

    assert loop.runs == []


def test_the_failure_loop_reads_the_full_output_not_the_excerpt_tail(monkeypatch):
    full = "tests/test_a.py::test_alpha FAILED\n" + "x" * 5000 + "\n1 failed, 9 passed in 1s"

    def measure(workspace, runner=None):
        return _measurement(0.9, exit_code=1), full

    monkeypatch.setattr(local_tests, "measure_selection", measure)
    monkeypatch.setattr(local_tests, "run_measurement", local_tests._measure_with_short_summary)
    loop = _ScriptedLoop("blocked", "introduced")

    local_tests._ReviewLocalTests("/w", failure_loop=lambda: loop).measure()

    assert loop.outputs == [full]
    assert loop.runs[0].rate == 0.9
