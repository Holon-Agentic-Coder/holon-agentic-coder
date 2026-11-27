"""The post-review CI wait: its own budget and back-off, three distinct outcomes, and the environment override.

Fixtures only: ``probe_ci_checks`` is scripted, the clock and sleep are injected, and no test calls a real ``gh``.
"""

from __future__ import annotations

import logging
import subprocess
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

from tests.flow.github.fake_github import FakeGitHub, checks_for, view_for
from tests.review_fakes import FakeEnsemble, clean_reviewer, failed_reviewer, install_fake_reviewers, objecting_reviewer

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
        stage=FlowStage.EXECUTE,
        status=StageStatus.SUCCESS,
        payload=payload or {"head_sha": "stage3-head", "test_pass_rate": 1.0, "exit_code": 0},
    )
    return ctx


def _github(*probes: dict, merge: dict = CLEAN_MERGE, head_sha: str | None = None) -> FakeGitHub:
    """A fake GitHub reporting ``probes`` as successive CI states (the last one repeats), ``merge`` throughout and
    ``head_sha`` as the PR head."""
    return FakeGitHub(
        checks_sequence=[checks_for(probe) for probe in probes], standing_view=view_for(merge), head_sha=head_sha
    )


def _missing_ref_repo(tmp_path: Path) -> None:
    """Make ``tmp_path`` a git repository without the execution branch, so local mergeability is ``MISSING_REF``."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)


def _run_loop(tmp_path: Path, monkeypatch, probes: list[dict], fake: FakeTime, payload: dict | None = None, **kwargs):
    """Run the loop with scripted probes (the last one repeats) and a fake clock; ``head_sha=`` is the PR head."""
    head_sha = kwargs.pop("head_sha", None)
    install_fake_reviewers(monkeypatch)
    with (
        patch("shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc1234"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
    ):
        return run_review_loop(
            _context(tmp_path, payload),
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=3,
            auto_pr=False,
            run_ensemble_fn=FakeEnsemble(),
            clock=fake.clock,
            sleep=fake.sleep,
            github=_github(*probes, head_sha=head_sha),
            **kwargs,
        )


def test_pending_checks_that_settle_reach_consensus_without_charging_the_iteration(tmp_path, monkeypatch):
    # A one-minute iteration budget: five 15 s polls (75 s) would breach it under the old behaviour.
    monkeypatch.setenv(ENV_STAGE_BUDGET_MINUTES["review_iteration"], "1")
    fake = FakeTime()
    res = _run_loop(
        tmp_path, monkeypatch, [PENDING, PENDING, PENDING, PENDING, PENDING, PASSED], fake, ci_wait_budget=600.0
    )

    assert fake.sleeps == [15.0, 15.0, 15.0, 15.0, 15.0]
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
    assert CI_WAIT_POLL_CEILING_SECONDS == 15.0
    fake_small = FakeTime()
    policy_small = CiWaitPolicy(50.0, 3.0, CI_WAIT_POLL_CEILING_SECONDS, clock=fake_small.clock, sleep=fake_small.sleep)
    status, result = wait_for_pending_ci(
        MagicMock(), MagicMock(return_value=dict(PENDING)), dict(PENDING), policy_small
    )

    assert fake_small.sleeps == [3.0, 6.0, 12.0, 15.0, 14.0]
    assert result.exhausted is True
    assert result.waited_seconds == 50.0
    assert status["pending"] == 1

    fake_default = FakeTime()
    policy_default = CiWaitPolicy(
        50.0, 15.0, CI_WAIT_POLL_CEILING_SECONDS, clock=fake_default.clock, sleep=fake_default.sleep
    )
    wait_for_pending_ci(MagicMock(), MagicMock(return_value=dict(PENDING)), dict(PENDING), policy_default)
    assert fake_default.sleeps == [15.0, 15.0, 15.0, 5.0]

    fake_clamped = FakeTime()
    policy_clamped = CiWaitPolicy(
        35.0, 30.0, CI_WAIT_POLL_CEILING_SECONDS, clock=fake_clamped.clock, sleep=fake_clamped.sleep
    )
    wait_for_pending_ci(MagicMock(), MagicMock(return_value=dict(PENDING)), dict(PENDING), policy_clamped)
    assert fake_clamped.sleeps == [15.0, 15.0, 5.0]

    assert next_poll_interval(5.0) == 10.0
    assert next_poll_interval(10.0) == 15.0
    assert next_poll_interval(15.0) == 15.0
    assert next_poll_interval(120.0) == 15.0
    assert next_poll_interval(500.0, 120.0) == 120.0


def test_wait_ends_as_soon_as_nothing_is_pending():
    fake = FakeTime()
    probe = MagicMock(side_effect=[dict(PENDING), dict(PASSED)])
    policy = CiWaitPolicy(1800.0, 15.0, clock=fake.clock, sleep=fake.sleep)
    status, result = wait_for_pending_ci(MagicMock(), probe, dict(PENDING), policy)

    assert fake.sleeps == [15.0, 15.0]
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
        patch("sandbox_executor.flow.stages.review.at_stage_boundary", return_value=None),
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
        payload={"head_sha": "stage3-head", "test_pass_rate": 0.5, "exit_code": 1},
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
    github = _github(PENDING, PENDING, PENDING, PASSED)
    resolver = MagicMock(return_value=MagicMock(returncode=0))
    with (
        patch("shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc1234"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
    ):
        res = run_review_loop(
            _context(tmp_path, {"head_sha": "stage3-head", "test_pass_rate": 0.5, "exit_code": 1}),
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=1,
            auto_pr=False,
            run_ensemble_fn=FakeEnsemble([clean_reviewer(1)]),
            run_resolver_fn=resolver,
            clock=fake.clock,
            sleep=fake.sleep,
            github=github,
            ci_wait_budget=30.0,
        )

    assert res["total_iterations"] == 2
    assert res["ci_checks"]["pending"] == 0
    assert res["ci_wait"] is None


def test_stale_wait_record_is_cleared_when_a_later_iteration_takes_the_objected_fast_path(tmp_path, monkeypatch):
    # Iteration 1: clean review, wait spent, local tests failing. Iteration 2: a reviewer objects and the probe settled.
    install_fake_reviewers(monkeypatch)
    fake = FakeTime()
    github = _github(PENDING, PENDING, PENDING, PASSED)
    rounds = iter([[clean_reviewer(1)], [objecting_reviewer(1)], [objecting_reviewer(1)]])
    with (
        patch("shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc1234"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
    ):
        res = run_review_loop(
            _context(tmp_path, {"head_sha": "stage3-head", "test_pass_rate": 0.5, "exit_code": 1}),
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=1,
            auto_pr=False,
            run_ensemble_fn=FakeEnsemble(lambda size: next(rounds)),
            run_resolver_fn=MagicMock(return_value=MagicMock(returncode=0)),
            clock=fake.clock,
            sleep=fake.sleep,
            github=github,
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
    github = _github(PENDING)
    resolver = MagicMock(return_value={"dispositions": [], "head_sha": "newhead1", "pushed": True})
    with (
        patch("shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc1234"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
    ):
        run_review_loop(
            _context(tmp_path, {"head_sha": "stage3-head", "test_pass_rate": 0.5, "exit_code": 1}),
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=1,
            auto_pr=False,
            run_ensemble_fn=FakeEnsemble([clean_reviewer(1)]),
            run_resolver_fn=resolver,
            clock=fake.clock,
            sleep=fake.sleep,
            github=github,
            ci_wait_budget=30.0,
        )

    assert resolver.call_count >= 1
    # Without the clear the second iteration would skip its wait; each wait sleeps 15 s then 15 s (budget 30 s).
    assert sum(fake.sleeps) > 30.0


class RecordingContext:
    """Collects the wait's log lines verbatim."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def log(self, message: str) -> None:
        self.lines.append(message)


def _probe(*buckets: tuple[str, str]) -> dict:
    checks = [{"name": name, "bucket": bucket, "state": bucket.upper()} for name, bucket in buckets]
    count = {b: sum(1 for _, bucket in buckets if bucket == b) for b in ("pass", "fail", "pending")}
    return {
        "source": "github_cli",
        "pending": count["pending"],
        "failed": count["fail"],
        "passed": count["pass"],
        "all_passed": count["pass"] == len(buckets),
        "total": len(buckets),
        "checks": checks,
    }


def test_wait_logs_a_start_line_each_finished_check_and_one_summary_but_nothing_for_unchanged_polls():
    fake = FakeTime()
    context = RecordingContext()
    start = _probe(("lint", "pending"), ("unit", "pending"), ("docs", "pending"))
    probes = [
        _probe(("lint", "pending"), ("unit", "pending"), ("docs", "pending")),
        _probe(("lint", "pass"), ("unit", "pending"), ("docs", "pending")),
        _probe(("lint", "pass"), ("unit", "pass"), ("docs", "skipping")),
    ]
    policy = CiWaitPolicy(1800.0, 15.0, clock=fake.clock, sleep=fake.sleep)
    status, result = wait_for_pending_ci(context, MagicMock(side_effect=probes), start, policy)

    assert result.exhausted is False and status["pending"] == 0
    assert context.lines == [
        "[Review Loop] Tracking 3 GitHub CI check(s): 3 pending (budget 1800s).",
        "[Review Loop] CI check 'lint' finished: pass (2 pending).",
        "[Review Loop] CI check 'unit' finished: pass (0 pending).",
        "[Review Loop] CI check 'docs' finished: skipping (0 pending).",
        "[Review Loop] CI wait settled after 45s: 2 passed, 0 failed, 1 other.",
    ]
    assert not any("Waiting for" in line for line in context.lines)


def test_wait_past_its_deadline_logs_one_start_line_and_one_budget_summary_naming_the_pending_checks():
    fake = FakeTime()
    context = RecordingContext()
    pending = _probe(("lint", "pass"), ("macos-tests", "pending"))
    policy = CiWaitPolicy(100.0, 15.0, clock=fake.clock, sleep=fake.sleep)
    _, result = wait_for_pending_ci(context, MagicMock(return_value=dict(pending)), dict(pending), policy)

    assert result.exhausted is True
    assert context.lines == [
        "[Review Loop] Tracking 2 GitHub CI check(s): 1 pending (budget 100s).",
        "[Review Loop] CI wait budget of 100s spent: 1 check(s) still pending (macos-tests).",
    ]


def test_wait_stopped_on_a_failure_reports_the_checks_still_pending():
    fake = FakeTime()
    context = RecordingContext()
    start = {"source": "github_cli", "pending": 3, "failed": 0, "passed": 0, "total": 3}
    probes = [dict(start, pending=2, failed=1)]
    policy = CiWaitPolicy(1800.0, 15.0, clock=fake.clock, sleep=fake.sleep)
    wait_for_pending_ci(context, MagicMock(side_effect=probes), start, policy)

    assert (
        context.lines[-1]
        == "[Review Loop] CI wait stopped on a failure after 15s: 0 passed, 1 failed, 2 still pending, 0 other."
    )


def test_wait_without_check_names_logs_the_pending_count_change():
    fake = FakeTime()
    context = RecordingContext()
    start = {"source": "github_cli", "pending": 2, "failed": 0, "passed": 0, "total": 2}
    probes = [dict(start, pending=1, passed=1), dict(start, pending=0, passed=2)]
    policy = CiWaitPolicy(1800.0, 15.0, clock=fake.clock, sleep=fake.sleep)
    wait_for_pending_ci(context, MagicMock(side_effect=probes), start, policy)

    assert context.lines[1:] == [
        "[Review Loop] CI checks pending: 2 -> 1.",
        "[Review Loop] CI checks pending: 1 -> 0.",
        "[Review Loop] CI wait settled after 30s: 2 passed, 0 failed, 0 other.",
    ]


def test_pending_ci_settling_to_pass_overrides_stale_stage3_failure(tmp_path, monkeypatch):
    """Pending GitHub CI settles BEFORE test_gate.evidence runs, avoiding false FIND-CI after Stage 3 failure."""
    fake = FakeTime()
    res = _run_loop(
        tmp_path,
        monkeypatch,
        [PENDING, PASSED],
        fake,
        payload={"head_sha": "stage3-head", "test_pass_rate": 0.5, "exit_code": 1},
        head_sha="stage3-head",
        ci_wait_budget=600.0,
    )

    assert res["consensus"]["approved"] is True
    assert res["tests_failed"] is False
    assert res["findings"] == []
    assert res["ci_wait"] is None
    assert res["ci_checks"]["test_source"] == "ci_head"
    assert res["ci_checks"]["test_head_sha"] == "stage3-head"


def test_pending_ci_settling_on_moved_head_skips_local_tests_and_keeps_ci_head(tmp_path, monkeypatch):
    """On a moved head with pending CI, the loop waits for CI first rather than running local tests synchronously."""
    from sandbox_executor.flow.review import local_tests

    run = MagicMock()
    monkeypatch.setattr(local_tests, "run_measurement", run)
    fake = FakeTime()
    with (
        patch(
            "sandbox_executor.flow.review.local_tests.run_git", return_value=MagicMock(returncode=0, stdout="moved\n")
        ),
    ):
        res = _run_loop(
            tmp_path,
            monkeypatch,
            [PENDING, PASSED],
            fake,
            payload={"head_sha": "stage3-head", "test_pass_rate": 1.0, "exit_code": 0},
            head_sha="moved",
            ci_wait_budget=600.0,
        )

    run.assert_not_called()
    assert res["consensus"]["approved"] is True
    assert res["ci_checks"]["test_source"] == "ci_head"
    assert res["ci_checks"]["test_head_sha"] == "moved"


def test_ci_registration_runs_on_new_head_after_prior_iteration_local_failure(tmp_path, monkeypatch):
    """Prior iteration tests_failed must not suppress _wait_for_ci_registration on a resolver-pushed new head."""
    from sandbox_executor.entrypoint.test_measurement import TestMeasurement
    from sandbox_executor.flow.review import local_tests

    install_fake_reviewers(monkeypatch)
    fake = FakeTime()
    local_only = {"source": "local_tests", "total": 1, "passed": 1, "failed": 0, "pending": 0, "all_passed": True}
    github = _github(local_only, PASSED, head_sha="head-2")
    current_head = {"sha": "head-1"}

    def fake_resolver(findings, **kwargs):
        del kwargs
        current_head["sha"] = "head-2"
        applied = [{"id": row.id, "disposition": "applied"} for row in findings]
        return {"dispositions": applied, "head_sha": "head-2", "pushed": True}

    failing_local = TestMeasurement(0.5, 5, 5, 0, 1, None, "")
    monkeypatch.setattr(local_tests, "run_measurement", MagicMock(return_value=failing_local))
    with (
        patch("shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="head-2"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        patch("sandbox_executor.flow.review.local_tests._ReviewLocalTests._head", lambda _self: current_head["sha"]),
        patch("sandbox_executor.flow.review.loop._wait_for_ci_registration") as mock_reg,
    ):
        res = run_review_loop(
            _context(tmp_path, {"head_sha": "head-1", "test_pass_rate": None, "exit_code": 0}),
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=3,
            auto_pr=False,
            run_ensemble_fn=FakeEnsemble(),
            run_resolver_fn=fake_resolver,
            clock=fake.clock,
            sleep=fake.sleep,
            github=github,
        )

    assert mock_reg.call_count == 2
    assert res["consensus"]["approved"] is True
    assert res["total_iterations"] == 2
    assert res["ci_checks"]["test_source"] == "ci_head"
    assert res["ci_checks"]["test_head_sha"] == "head-2"


def test_stale_github_ci_cannot_approve_unmeasured_review_local_in_loop(tmp_path, monkeypatch):
    """F-9: stale green GitHub CI on an older PR head must not mask unmeasured review_local."""
    from sandbox_executor.entrypoint.test_measurement import TestMeasurement
    from sandbox_executor.flow.review import local_tests

    unmeasured = TestMeasurement(None, None, None, None, 127, "uv missing", "")
    monkeypatch.setattr(local_tests, "run_measurement", MagicMock(return_value=unmeasured))
    fake = FakeTime()
    with (
        patch(
            "sandbox_executor.flow.review.local_tests.run_git",
            return_value=MagicMock(returncode=0, stdout="moved\n"),
        ),
    ):
        res = _run_loop(
            tmp_path,
            monkeypatch,
            [PASSED],
            fake,
            payload={"head_sha": "stage3-head", "test_pass_rate": 1.0, "exit_code": 0},
            head_sha="older-pr-head",
            ci_wait_budget=600.0,
        )

    assert res["consensus"]["approved"] is False
    assert res["consensus"]["status"] == "not_measured"
    assert res["findings"] == ["FIND-CI-001"]
    assert res["ci_checks"]["source"] == "local_tests"
    assert res["ci_checks"]["measured"] is False
    assert res["ci_checks"]["test_source"] == "review_local"


@pytest.mark.parametrize("objecting", [False, True], ids=["clean-gate", "objecting-fast-path"])
def test_stale_github_ci_does_not_drop_failing_review_local_find_ci(tmp_path, monkeypatch, objecting):
    """F-9: stale green GitHub CI must not suppress FIND-CI when review_local fails on clean or fast path."""
    from sandbox_executor.entrypoint.test_measurement import TestMeasurement
    from sandbox_executor.flow.review import local_tests

    failing = TestMeasurement(0.5, 5, 5, 0, 1, None, "")
    monkeypatch.setattr(local_tests, "run_measurement", MagicMock(return_value=failing))
    install_fake_reviewers(monkeypatch)
    fake = FakeTime()
    reviewer = objecting_reviewer(1) if objecting else clean_reviewer(1)
    github = _github(PASSED, head_sha="older-pr-head")
    with (
        patch("shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="moved"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        patch(
            "sandbox_executor.flow.review.local_tests.run_git",
            return_value=MagicMock(returncode=0, stdout="moved\n"),
        ),
    ):
        res = run_review_loop(
            _context(tmp_path, {"head_sha": "stage3-head", "test_pass_rate": 1.0, "exit_code": 0}),
            target=EXEC_TARGET,
            max_iterations=1,
            ensemble_size=1,
            auto_pr=False,
            run_ensemble_fn=FakeEnsemble([reviewer]),
            clock=fake.clock,
            sleep=fake.sleep,
            github=github,
        )

    assert res["consensus"]["approved"] is False
    assert res["tests_failed"] is True
    assert "FIND-CI-001" in res["findings"]
    assert res["ci_checks"]["source"] == "local_tests"
    assert res["ci_checks"]["all_passed"] is False
    assert res["ci_checks"]["failed"] == 1
    assert res["ci_checks"]["test_source"] == "review_local"


def test_run_review_loop_dry_run_argument_overrides_false_context_dry_run(tmp_path, monkeypatch):
    """F-10: run_review_loop(..., dry_run=True) skips pr_head lookup and local tests even when context.dry_run=False."""
    from sandbox_executor.entrypoint.test_measurement import TestMeasurement
    from sandbox_executor.flow.review import local_tests

    install_fake_reviewers(monkeypatch)
    run = MagicMock(return_value=TestMeasurement(1.0, 1, 0, 0, 0, None, ""))
    github = _github(PASSED, head_sha="moved")
    monkeypatch.setattr(local_tests, "run_measurement", run)
    ctx = _context(tmp_path, {"head_sha": "stage3-head", "test_pass_rate": 1.0, "exit_code": 0})
    assert ctx.dry_run is False

    with (
        patch("shutil.which", return_value=None),
        patch(
            "sandbox_executor.flow.review.local_tests.run_git",
            return_value=MagicMock(returncode=0, stdout="moved\n"),
        ),
    ):
        res = run_review_loop(ctx, target=EXEC_TARGET, max_iterations=1, auto_pr=False, dry_run=True, github=github)

    run.assert_not_called()
    assert "pr_head_sha" not in github.methods()
    assert res["consensus"]["approved"] is False
    assert res["consensus"]["status"] == "not_measured"
    assert res["ci_checks"]["test_source"] == "ci"


def test_objecting_fast_path_with_pending_ci_and_failing_review_local_raises_find_ci(tmp_path, monkeypatch):
    """F-13: objecting fast path raises FIND-CI alongside reviewer findings when review_local fails under pending CI."""
    from sandbox_executor.entrypoint.test_measurement import TestMeasurement
    from sandbox_executor.flow.review import local_tests, loop

    failing = TestMeasurement(0.5, 5, 5, 0, 1, None, "")
    monkeypatch.setattr(local_tests, "run_measurement", MagicMock(return_value=failing))
    install_fake_reviewers(monkeypatch)
    fake = FakeTime()
    resolver_calls = []

    def fake_resolver(target, iteration, findings, action_ids, rejected_findings):
        resolver_calls.append({"findings": findings, "iteration": iteration})
        return {"dispositions": [], "head_sha": target.head_sha, "pushed": False}

    with (
        patch("shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="moved"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        patch(
            "sandbox_executor.flow.review.local_tests.run_git",
            return_value=MagicMock(returncode=0, stdout="moved\n"),
        ),
        patch(
            "sandbox_executor.flow.review.loop.decide_returned_consensus", wraps=loop.decide_returned_consensus
        ) as spy_consensus,
    ):
        res = run_review_loop(
            _context(tmp_path, {"head_sha": "stage3-head", "test_pass_rate": 1.0, "exit_code": 0}),
            target=EXEC_TARGET,
            max_iterations=1,
            ensemble_size=1,
            auto_pr=False,
            run_ensemble_fn=FakeEnsemble([objecting_reviewer(1)]),
            run_resolver_fn=fake_resolver,
            clock=fake.clock,
            sleep=fake.sleep,
            github=_github(PENDING, head_sha="older-pr-head"),
        )

    assert fake.sleeps == []
    assert spy_consensus.call_args.args[3] is False
    assert res["consensus"]["approved"] is False
    assert res["tests_failed"] is True
    assert res["findings"] == ["FIND-CI-001", "FIND-R1-001-01"]
    assert len(resolver_calls) == 1
    assert [f.id for f in resolver_calls[0]["findings"]] == ["R1-F1", "FIND-CI-001"]
    assert res["ci_checks"]["source"] == "github_cli"
    assert res["ci_checks"]["test_source"] == "review_local"


def test_fallback_else_skips_local_tests_on_infra_error(tmp_path, monkeypatch):
    """F-15: infra_error in the fallback branch must not invoke local tests or flag tests_not_measured."""
    from sandbox_executor.entrypoint.test_measurement import TestMeasurement
    from sandbox_executor.flow.review import local_tests

    run = MagicMock(return_value=TestMeasurement(None, None, None, None, 127, "uv missing", ""))
    monkeypatch.setattr(local_tests, "run_measurement", run)
    install_fake_reviewers(monkeypatch)
    fake = FakeTime()
    ctx = _context(tmp_path, {"head_sha": "stage3-head", "test_pass_rate": 0.5, "exit_code": 1})
    local_probe = {"source": "local_tests", "total": 1, "passed": 1, "failed": 0, "pending": 0, "all_passed": True}

    with (
        patch("shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="moved"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        patch(
            "sandbox_executor.flow.review.local_tests.run_git",
            return_value=MagicMock(returncode=0, stdout="moved\n"),
        ),
        patch("sandbox_executor.flow.review.ensemble.build_review_request", side_effect=RuntimeError("diff failed")),
    ):
        res = run_review_loop(
            ctx,
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=1,
            auto_pr=False,
            run_ensemble_fn=FakeEnsemble([clean_reviewer(1)]),
            clock=fake.clock,
            sleep=fake.sleep,
            github=_github(local_probe),
        )

    run.assert_not_called()
    assert res["consensus"]["status"] == "failed"
    assert res["tests_failed"] is False
    assert "FIND-CI-001" not in res["findings"]
    assert not any("test results not measured" in log for log in ctx.logs)


def test_fallback_else_skips_local_tests_on_missing_ref(tmp_path, monkeypatch):
    """F-15: missing_ref in the fallback branch must not invoke local tests or report status not_measured."""
    from sandbox_executor.entrypoint.test_measurement import TestMeasurement
    from sandbox_executor.flow.review import local_tests

    run = MagicMock(return_value=TestMeasurement(None, None, None, None, 127, "uv missing", ""))
    monkeypatch.setattr(local_tests, "run_measurement", run)
    install_fake_reviewers(monkeypatch)
    fake = FakeTime()
    ctx = _context(tmp_path, {"head_sha": "stage3-head", "test_pass_rate": 0.5, "exit_code": 1})
    # Without gh, CI falls back to local tests and mergeability to local git, where the branch is a missing ref.

    _missing_ref_repo(tmp_path)
    with (
        patch("shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="moved"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        patch(
            "sandbox_executor.flow.review.local_tests.run_git",
            return_value=MagicMock(returncode=0, stdout="moved\n"),
        ),
    ):
        res = run_review_loop(
            ctx,
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=1,
            auto_pr=False,
            run_ensemble_fn=FakeEnsemble([clean_reviewer(1)]),
            clock=fake.clock,
            sleep=fake.sleep,
            github=FakeGitHub(available=False),
        )

    run.assert_not_called()
    assert res["consensus"]["status"] == "changes_requested"
    assert res["findings"] == ["FIND-REF-001"]
    assert res["tests_failed"] is False
    assert not any("test results not measured" in log for log in ctx.logs)


def test_objecting_fast_path_skips_local_tests_on_infra_error(tmp_path, monkeypatch):
    """F-20: infra_error in the objecting fast-path must not invoke local tests or record FIND-CI."""
    from sandbox_executor.entrypoint.test_measurement import TestMeasurement
    from sandbox_executor.flow.review import local_tests

    run = MagicMock(return_value=TestMeasurement(None, None, None, None, 127, "uv missing", ""))
    monkeypatch.setattr(local_tests, "run_measurement", run)
    install_fake_reviewers(monkeypatch)
    fake = FakeTime()
    ctx = _context(tmp_path, {"head_sha": "stage3-head", "test_pass_rate": 0.5, "exit_code": 1})
    local_probe = {"source": "local_tests", "total": 1, "passed": 1, "failed": 0, "pending": 0, "all_passed": True}

    with (
        patch("shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop.reviewers_sandboxed", return_value=False),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="moved"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        patch(
            "sandbox_executor.flow.review.local_tests.run_git",
            return_value=MagicMock(returncode=0, stdout="moved\n"),
        ),
        patch(
            "sandbox_executor.flow.review.loop._dispatch_review_ensemble",
            return_value=([objecting_reviewer(1), failed_reviewer(2)], None, "partial ensemble error"),
        ),
    ):
        res = run_review_loop(
            ctx,
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=2,
            auto_pr=False,
            clock=fake.clock,
            sleep=fake.sleep,
            github=_github(local_probe),
        )

    run.assert_not_called()
    assert res["consensus"]["status"] == "failed"
    assert res["tests_failed"] is False
    assert "FIND-R1-001-01" in res["findings"]
    assert "FIND-CI-001" not in res["findings"]
    assert not any("test results not measured" in log for log in ctx.logs)


@pytest.mark.parametrize("reviewer", [objecting_reviewer(1), failed_reviewer(1)], ids=["objecting", "failed"])
def test_objecting_fast_path_skips_local_tests_on_missing_ref(tmp_path, monkeypatch, reviewer):
    """F-20: missing_ref in the objecting fast-path must not invoke local tests or record FIND-CI."""
    from sandbox_executor.entrypoint.test_measurement import TestMeasurement
    from sandbox_executor.flow.review import local_tests

    run = MagicMock(return_value=TestMeasurement(None, None, None, None, 127, "uv missing", ""))
    monkeypatch.setattr(local_tests, "run_measurement", run)
    install_fake_reviewers(monkeypatch)
    fake = FakeTime()
    ctx = _context(tmp_path, {"head_sha": "stage3-head", "test_pass_rate": 0.5, "exit_code": 1})
    # Without gh, CI falls back to local tests and mergeability to local git, where the branch is a missing ref.

    _missing_ref_repo(tmp_path)
    with (
        patch("shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="moved"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        patch(
            "sandbox_executor.flow.review.local_tests.run_git",
            return_value=MagicMock(returncode=0, stdout="moved\n"),
        ),
    ):
        res = run_review_loop(
            ctx,
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=1,
            auto_pr=False,
            run_ensemble_fn=FakeEnsemble([reviewer]),
            clock=fake.clock,
            sleep=fake.sleep,
            github=FakeGitHub(available=False),
        )

    run.assert_not_called()
    assert res["tests_failed"] is False
    assert "FIND-REF-001" in res["findings"]
    assert "FIND-CI-001" not in res["findings"]
    assert not any("test results not measured" in log for log in ctx.logs)


# --- 15-second poll ceiling and fail-early in review-loop CI wait ----------------------------------------------------


def test_review_loop_fails_early_on_failed_check_while_others_pending(tmp_path, monkeypatch):
    """Review-loop wait stops immediately when any check fails, even if other checks are still pending."""
    fake = FakeTime()
    mixed = _probe(("lint", "fail"), ("macos-tests", "pending"))
    res = _run_loop(tmp_path, monkeypatch, [PENDING, mixed, PASSED], fake, ci_wait_budget=600.0)

    assert fake.sleeps == [15.0]
    assert res["consensus"]["approved"] is False
    assert any(finding.startswith("FIND-CI") for finding in res["findings"])
    assert res["ci_checks"]["failed"] == 1
    assert res["ci_checks"]["pending"] == 1


def test_settle_pending_ci_caps_polls_at_15s_and_exits_early_on_failure():
    fake = FakeTime()
    probes = [dict(PENDING), dict(PENDING), dict(PENDING), dict(PASSED)]
    policy = CiWaitPolicy(600.0, clock=fake.clock, sleep=fake.sleep)
    status, waited = settle_pending_ci(MagicMock(), MagicMock(side_effect=probes), dict(PENDING), policy, spent=False)

    assert status["all_passed"] is True
    assert waited is not None and waited.exhausted is False
    assert fake.sleeps == [15.0, 15.0, 15.0, 15.0]

    fake_early = FakeTime()
    mixed = _probe(("lint", "fail"), ("unit", "pending"))
    probe_early = MagicMock(return_value=dict(PASSED))
    policy_early = CiWaitPolicy(600.0, clock=fake_early.clock, sleep=fake_early.sleep)
    status_early, waited_early = settle_pending_ci(MagicMock(), probe_early, mixed, policy_early, spent=False)

    assert status_early["failed"] == 1
    assert waited_early is not None and waited_early.exhausted is False
    assert fake_early.sleeps == []
    assert probe_early.call_count == 0
