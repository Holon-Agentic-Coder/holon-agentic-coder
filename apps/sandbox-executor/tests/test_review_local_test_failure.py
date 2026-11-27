from __future__ import annotations

from unittest.mock import MagicMock, patch

from sandbox_executor.entrypoint.test_failure_loop import LoopOutcome
from sandbox_executor.entrypoint.test_measurement import TestMeasurement
from sandbox_executor.flow import parked_record_from_ci_status, run_review_loop
from sandbox_executor.flow.review import local_tests
from sandbox_executor.review_target import DiffVerdict

from tests.review_fakes import FakeEnsemble, install_fake_reviewers, objecting_reviewer
from tests.test_review_ci_wait import EXEC_TARGET, PENDING, FakeTime, _context, _github


def test_a_diagnosed_local_failure_reaches_the_resolver_with_ids_and_excerpts(tmp_path, monkeypatch):
    failing = TestMeasurement(0.5, 5, 5, 0, 1, None, "")
    monkeypatch.setattr(local_tests, "run_measurement", MagicMock(return_value=failing))
    ids = ("tests/test_a.py::test_alpha",)
    outcome = LoopOutcome("exhausted", failing, "introduced", [], "", None, ids, {ids[0]: "E assert 1 == 2"}, "sig")
    monkeypatch.setattr(local_tests, "_review_failure_loop", lambda *_: MagicMock(run=MagicMock(return_value=outcome)))
    install_fake_reviewers(monkeypatch)
    fake = FakeTime()
    resolver_calls = []

    def fake_resolver(target, iteration, findings, action_ids, rejected_findings):
        resolver_calls.append(findings)
        return {"dispositions": [], "head_sha": target.head_sha, "pushed": False}

    with (
        patch("shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="moved"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        patch(
            "sandbox_executor.flow.review.local_tests.run_git",
            return_value=MagicMock(returncode=0, stdout="moved\n"),
        ),
    ):
        run_review_loop(
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

    ci_row = next(f for f in resolver_calls[0] if f.id == "FIND-CI-001")
    assert "(introduced): tests/test_a.py::test_alpha." in ci_row.summary
    assert "E assert 1 == 2" in ci_row.recommendation


def test_run_review_loop_surfaces_blocked_on_and_parked_record(tmp_path, monkeypatch):
    failing = TestMeasurement(0.5, 5, 5, 0, 1, None, "")
    monkeypatch.setattr(local_tests, "run_measurement", MagicMock(return_value=failing))
    ids = ("tests/test_a.py::test_alpha",)
    rows = [{"action_id": "ACT-0", "model_tier": "haiku-x"}, {"action_id": "ACT-1", "model_tier": "opus-x"}]
    outcome = LoopOutcome(
        "exhausted",
        failing,
        "introduced",
        rows,
        "ladder exhausted",
        "opus-x",
        ids,
        {ids[0]: "E assert 1 == 2"},
        "sig-exh",
        "none",
        None,
    )
    monkeypatch.setattr(local_tests, "_review_failure_loop", lambda *_: MagicMock(run=MagicMock(return_value=outcome)))
    install_fake_reviewers(monkeypatch)
    fake = FakeTime()
    ctx = _context(tmp_path, {"head_sha": "stage3-head", "test_pass_rate": 1.0, "exit_code": 0})
    ctx.checkpoint_path = str(tmp_path / "flow_checkpoint.json")

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
            max_iterations=1,
            ensemble_size=1,
            auto_pr=False,
            run_ensemble_fn=FakeEnsemble([objecting_reviewer(1)]),
            run_resolver_fn=lambda target, **_: {"dispositions": [], "head_sha": target.head_sha, "pushed": False},
            clock=fake.clock,
            sleep=fake.sleep,
            github=_github(PENDING, head_sha="older-pr-head"),
        )

    tf = res["ci_checks"]["test_failure"]
    assert tf["blocked_on"] == "none"
    assert tf["parked_record"]["checkpoint_path"] == ctx.checkpoint_path
    assert tf["parked_record"]["rows"] == rows
    normalized = parked_record_from_ci_status(res["ci_checks"])
    assert normalized is not None
    assert normalized["blocked_on"] == "none"
    assert normalized["signature"] == "sig-exh"
    assert normalized["checkpoint_path"] == ctx.checkpoint_path
    assert normalized["rows"] == rows
