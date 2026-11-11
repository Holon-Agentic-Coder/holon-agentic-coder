"""The post-review CI wait: its own budget and back-off, three distinct outcomes, and the environment override.

Fixtures only: ``probe_ci_checks`` is scripted, the clock and sleep are injected, and no test calls a real ``gh``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.budgets import (
    DEFAULT_REVIEW_CI_WAIT_SECONDS,
    ENV_REVIEW_CI_WAIT,
    ENV_STAGE_BUDGET_MINUTES,
    resolve_review_ci_wait_seconds,
)
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus, run_review_loop
from sandbox_executor.flow.github.ci_wait import (
    CI_WAIT_POLL_CEILING_SECONDS,
    CiWaitPolicy,
    next_poll_interval,
    settle_pending_ci,
    wait_for_pending_ci,
)
from sandbox_executor.flow.review.ci_outcome import NO_BLOCKER_RECORDED, describe_ci_outcome
from sandbox_executor.review_target import DiffVerdict

from tests.review_fakes import FakeEnsemble, clean_reviewer, install_fake_reviewers, objecting_reviewer

EXEC_TARGET = "I-100-intent/P-200-plan/E-300-exec/_"
CLEAN_MERGE = {"is_dirty": False, "merge_state_status": "CLEAN", "mergeable": "MERGEABLE"}
PENDING = {
    "source": "github_cli",
    "pending": 1,
    "failed": 0,
    "passed": 0,
    "all_passed": False,
    "total": 1,
    "checks": [{"name": "macos-tests", "bucket": "pending", "state": "QUEUED"}],
}
PASSED = {"source": "github_cli", "pending": 0, "failed": 0, "passed": 1, "all_passed": True, "total": 1}


class FakeTime:
    """A clock advanced only by its sleep, so a wait never really sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


FAILED_CHECK = {
    "source": "github_cli",
    "pending": 0,
    "failed": 1,
    "passed": 0,
    "all_passed": False,
    "total": 1,
    "checks": [{"name": "lint", "bucket": "fail", "state": "FAILURE"}],
}


def _context(tmp_path: Path, payload: dict | None = None) -> FlowContext:
    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True, exist_ok=True)
    ctx = FlowContext(
        repo_dir=str(tmp_path), execution_branch=EXEC_TARGET, agent="fake-agent", model="fake-model", token_reduce=False
    )
    ctx.stage_results["execute"] = StageResult(
        stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload=payload or {"test_pass_rate": 1.0, "exit_code": 0}
    )
    return ctx


def _run_loop(tmp_path: Path, monkeypatch, probes: list[dict], fake: FakeTime, payload: dict | None = None, **kwargs):
    """Run the loop with scripted probes (the last one repeats) and a fake clock."""
    install_fake_reviewers(monkeypatch)
    script = list(probes)
    probe = MagicMock(side_effect=lambda *a, **k: dict(script.pop(0) if len(script) > 1 else script[0]))
    with (
        patch("sandbox_executor.flow.review.loop.shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop.check_pr_mergeability", return_value=dict(CLEAN_MERGE)),
        patch("sandbox_executor.flow.review.loop.probe_ci_checks", probe),
        patch(
            "sandbox_executor.flow.review.loop.post_pr_review_consensus",
            return_value={"dual_posted": True, "official_review": {"success": True}},
        ),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc1234"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
    ):
        return run_review_loop(
            _context(tmp_path, payload),
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=1,
            auto_pr=False,
            run_ensemble_fn=FakeEnsemble([clean_reviewer(1)]),
            clock=fake.clock,
            sleep=fake.sleep,
            **kwargs,
        )


def test_pending_checks_that_settle_reach_consensus_without_charging_the_iteration(tmp_path, monkeypatch):
    # A one-minute iteration budget: the 15 + 30 + 60 s wait would breach it under the old behaviour.
    monkeypatch.setenv(ENV_STAGE_BUDGET_MINUTES["review_iteration"], "1")
    fake = FakeTime()
    res = _run_loop(tmp_path, monkeypatch, [PENDING, PENDING, PENDING, PENDING, PASSED], fake, ci_wait_budget=600.0)

    assert fake.sleeps == [15.0, 30.0, 60.0, 120.0]
    assert res["consensus"]["approved"] is True
    assert res["consensus"]["iterations"] == 1
    assert res["budget_breach"] is None
    assert "failed" not in res["iteration_summaries"][0]
    assert res["ci_wait"] is None


def test_exhausted_wait_reports_pending_never_failed_nor_all_passed(tmp_path, monkeypatch):
    fake = FakeTime()
    res = _run_loop(tmp_path, monkeypatch, [PENDING], fake, ci_wait_budget=100.0)

    assert res["consensus"]["approved"] is False
    assert res["ci_checks"]["all_passed"] is False
    assert res["tests_failed"] is False
    assert not any(finding.startswith("FIND-CI") for finding in res["findings"])
    assert res["ci_wait"] == {
        "state": "pending",
        "budget_seconds": 100.0,
        "waited_seconds": 100.0,
        "pending_checks": ["macos-tests"],
    }
    assert sum(fake.sleeps) == 100.0


def test_budget_already_spent_is_not_waited_out_again():
    fake = FakeTime()
    probe = MagicMock(return_value=dict(PENDING))
    context = MagicMock()
    status, waited = settle_pending_ci(context, probe, dict(PENDING), CiWaitPolicy(60.0, 15.0, 120.0), True)

    assert waited is None
    assert status["pending"] == 1
    assert probe.call_count == 0
    assert fake.sleeps == []


def test_back_off_grows_to_its_ceiling_and_no_further():
    fake = FakeTime()
    context = MagicMock()
    probe = MagicMock(return_value=dict(PENDING))
    policy = CiWaitPolicy(500.0, 15.0, CI_WAIT_POLL_CEILING_SECONDS, clock=fake.clock, sleep=fake.sleep)
    status, result = wait_for_pending_ci(context, probe, dict(PENDING), policy)

    assert fake.sleeps == [15.0, 30.0, 60.0, 120.0, 120.0, 120.0, 35.0]
    assert result.exhausted is True
    assert result.waited_seconds == 500.0
    assert status["pending"] == 1
    assert next_poll_interval(120.0) == 120.0
    assert next_poll_interval(500.0, 120.0) == 120.0
    assert next_poll_interval(15.0) == 30.0


def test_wait_ends_as_soon_as_nothing_is_pending():
    fake = FakeTime()
    probe = MagicMock(side_effect=[dict(PENDING), dict(PASSED)])
    policy = CiWaitPolicy(1800.0, 15.0, clock=fake.clock, sleep=fake.sleep)
    status, result = wait_for_pending_ci(MagicMock(), probe, dict(PENDING), policy)

    assert fake.sleeps == [15.0, 30.0]
    assert status["all_passed"] is True
    assert result.exhausted is False


@pytest.mark.parametrize("raw", ["abc", "-5", "0", "nan", "inf", "1e308"])
def test_invalid_wait_budget_falls_back_with_a_warning_naming_only_the_variable(raw, caplog):
    with caplog.at_level(logging.WARNING, logger="sandbox_executor.budgets"):
        seconds = resolve_review_ci_wait_seconds({ENV_REVIEW_CI_WAIT: raw})

    assert seconds == DEFAULT_REVIEW_CI_WAIT_SECONDS == 1800.0
    assert ENV_REVIEW_CI_WAIT in caplog.text
    assert raw == "0" or raw not in caplog.text


def test_wait_budget_environment_override_and_default():
    assert resolve_review_ci_wait_seconds({ENV_REVIEW_CI_WAIT: "900"}) == 900.0
    assert resolve_review_ci_wait_seconds({}) == 1800.0


def test_pending_outcome_names_the_checks_and_the_budget_and_says_no_test_failed():
    wait = {"state": "pending", "budget_seconds": 1800.0, "waited_seconds": 1800.0, "pending_checks": ["macos", "lint"]}
    message = describe_ci_outcome(dict(PENDING), wait, False)

    assert "CI still pending after the wait budget" in message
    assert "macos, lint" in message
    assert "1800 s" in message
    assert "no test failed" in message
    assert "test suite reported failures" not in message


def test_failed_outcome_names_every_failed_check():
    status = {
        "source": "github_cli",
        "failed": 2,
        "pending": 0,
        "checks": [
            {"name": "lint", "bucket": "fail", "state": "FAILURE"},
            {"name": "unit", "bucket": "fail", "state": "FAILURE"},
            {"name": "docs", "bucket": "pass", "state": "SUCCESS"},
        ],
    }

    assert describe_ci_outcome(status, None, False) == "Autonomous review consensus failed: CI failed: lint, unit."


def test_old_phrase_only_when_tests_really_failed_and_neutral_otherwise():
    local = {"source": "local_tests", "failed": 1, "checks": [{"name": "local_tests", "bucket": "fail"}]}

    assert "test suite reported failures" in describe_ci_outcome(local, None, True)
    assert describe_ci_outcome(dict(PASSED), None, False) == NO_BLOCKER_RECORDED
    assert "test suite reported failures" not in NO_BLOCKER_RECORDED


def test_stage_error_reports_pending_after_the_wait_budget(tmp_path):
    from sandbox_executor.flow import run_review_stage

    ctx = _context(tmp_path)
    loop_res = {
        "consensus": {"approved": False, "status": "rejected"},
        "mergeability": {"is_dirty": False},
        "ci_checks": dict(PENDING),
        "ci_wait": {"state": "pending", "budget_seconds": 1800.0, "waited_seconds": 1800.0, "pending_checks": ["x"]},
        "tests_failed": False,
    }
    with (
        patch("sandbox_executor.flow.stages.review.run_git", return_value=MagicMock(returncode=0, stdout="")),
        patch("sandbox_executor.flow.stages.review._checkout_execution_branch", return_value=None),
        patch("sandbox_executor.flow.stages.review.check_and_resolve_stage_conflicts"),
        patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=loop_res),
    ):
        result = run_review_stage(ctx)

    assert result.status == StageStatus.FAILED
    assert "CI still pending after the wait budget" in result.error
    assert "test suite reported failures" not in result.error


def test_wait_that_settles_into_a_failed_check_records_the_ci_finding(tmp_path, monkeypatch):
    res = _run_loop(tmp_path, monkeypatch, [PENDING, FAILED_CHECK], FakeTime(), ci_wait_budget=600.0)

    assert res["consensus"]["approved"] is False
    assert any(finding.startswith("FIND-CI") for finding in res["findings"])
    assert res["ci_wait"] is None
    assert res["ci_checks"]["failed"] == 1


def test_pending_checks_with_failed_local_tests_still_record_the_ci_finding(tmp_path, monkeypatch):
    res = _run_loop(
        tmp_path,
        monkeypatch,
        [PENDING],
        FakeTime(),
        payload={"test_pass_rate": 0.5, "exit_code": 1},
        ci_wait_budget=30.0,
    )

    assert res["tests_failed"] is True
    assert res["consensus"]["approved"] is False
    assert any(finding.startswith("FIND-CI") for finding in res["findings"])
    assert res["ci_wait"]["state"] == "pending"


def test_wait_stops_at_once_when_a_check_has_already_failed():
    fake = FakeTime()
    probe = MagicMock(return_value=dict(PENDING))
    mixed = {**PENDING, "failed": 1}
    policy = CiWaitPolicy(1800.0, 15.0, clock=fake.clock, sleep=fake.sleep)
    status, result = wait_for_pending_ci(MagicMock(), probe, mixed, policy)

    assert fake.sleeps == []
    assert probe.call_count == 0
    assert status["failed"] == 1
    assert result.exhausted is False


def test_stale_wait_record_is_cleared_when_a_later_probe_has_settled(tmp_path, monkeypatch):
    # First pass: the wait is spent with local tests failing (a finding), the resolver runs, then the checks settle.
    install_fake_reviewers(monkeypatch)
    fake = FakeTime()
    script = [PENDING, PENDING, PENDING, PASSED]
    probe = MagicMock(side_effect=lambda *a, **k: dict(script.pop(0) if len(script) > 1 else script[0]))
    resolver = MagicMock(return_value=MagicMock(returncode=0))
    with (
        patch("sandbox_executor.flow.review.loop.shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop.check_pr_mergeability", return_value=dict(CLEAN_MERGE)),
        patch("sandbox_executor.flow.review.loop.probe_ci_checks", probe),
        patch("sandbox_executor.flow.review.loop.post_pr_review_consensus", return_value={"dual_posted": True}),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc1234"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
    ):
        res = run_review_loop(
            _context(tmp_path, {"test_pass_rate": 0.5, "exit_code": 1}),
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=1,
            auto_pr=False,
            run_ensemble_fn=FakeEnsemble([clean_reviewer(1)]),
            run_resolver_fn=resolver,
            clock=fake.clock,
            sleep=fake.sleep,
            ci_wait_budget=30.0,
        )

    assert res["total_iterations"] == 2
    assert res["ci_checks"]["pending"] == 0
    assert res["ci_wait"] is None


def test_stale_wait_record_is_cleared_when_a_later_iteration_takes_the_objected_fast_path(tmp_path, monkeypatch):
    # Iteration 1: clean review, wait spent, local tests failing. Iteration 2: a reviewer objects and the probe settled.
    install_fake_reviewers(monkeypatch)
    fake = FakeTime()
    script = [PENDING, PENDING, PENDING, PASSED]
    probe = MagicMock(side_effect=lambda *a, **k: dict(script.pop(0) if len(script) > 1 else script[0]))
    rounds = iter([[clean_reviewer(1)], [objecting_reviewer(1)], [objecting_reviewer(1)]])
    with (
        patch("sandbox_executor.flow.review.loop.shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop.check_pr_mergeability", return_value=dict(CLEAN_MERGE)),
        patch("sandbox_executor.flow.review.loop.probe_ci_checks", probe),
        patch("sandbox_executor.flow.review.loop.post_pr_review_consensus", return_value={"dual_posted": True}),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc1234"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
    ):
        res = run_review_loop(
            _context(tmp_path, {"test_pass_rate": 0.5, "exit_code": 1}),
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=1,
            auto_pr=False,
            run_ensemble_fn=FakeEnsemble(lambda size: next(rounds)),
            run_resolver_fn=MagicMock(return_value=MagicMock(returncode=0)),
            clock=fake.clock,
            sleep=fake.sleep,
            ci_wait_budget=30.0,
        )

    assert res["total_iterations"] == 2
    assert res["ci_checks"]["pending"] == 0
    assert res["ci_wait"] is None


def test_pending_from_a_source_that_is_not_waited_on_is_not_a_missing_blocker():
    status = {**PENDING, "source": "other"}

    message = describe_ci_outcome(status, None, False)
    assert "CI still pending and not waited for: macos-tests" in message
    assert message != NO_BLOCKER_RECORDED


def test_spent_wait_is_cleared_when_the_resolver_pushes_a_new_head(tmp_path, monkeypatch):
    # Iteration 1: wait spent with local tests failing, the resolver pushes. Iteration 2: the new head waits afresh.
    install_fake_reviewers(monkeypatch)
    fake = FakeTime()
    probe = MagicMock(side_effect=lambda *a, **k: dict(PENDING))
    resolver = MagicMock(return_value={"dispositions": [], "head_sha": "newhead1", "pushed": True})
    with (
        patch("sandbox_executor.flow.review.loop.shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop.check_pr_mergeability", return_value=dict(CLEAN_MERGE)),
        patch("sandbox_executor.flow.review.loop.probe_ci_checks", probe),
        patch("sandbox_executor.flow.review.loop.post_pr_review_consensus", return_value={"dual_posted": True}),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc1234"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
    ):
        run_review_loop(
            _context(tmp_path, {"test_pass_rate": 0.5, "exit_code": 1}),
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=1,
            auto_pr=False,
            run_ensemble_fn=FakeEnsemble([clean_reviewer(1)]),
            run_resolver_fn=resolver,
            clock=fake.clock,
            sleep=fake.sleep,
            ci_wait_budget=30.0,
        )

    assert resolver.call_count >= 1
    # Without the clear the second iteration would skip its wait; each wait sleeps 15 s then 15 s (budget 30 s).
    assert sum(fake.sleeps) > 30.0
