"""Tests that Stage 4 never records or approves a test pass that Stage 3 did not measure."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus, run_review_loop, run_review_stage

from tests.flow.github.fake_github import FakeGitHub, merge_view
from tests.flow.helpers import _verified_diff


class TestStage4TestEvidence:
    """Stage 4 never records or approves a test pass that Stage 3 did not measure."""

    @staticmethod
    def _ctx(tmp_path: Path, payload: dict[str, Any] | None, *, dry_run: bool = False) -> FlowContext:
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            plan_branch="I-1-x/P-2-a-b/_",
            execution_branch="I-1-x/P-2-a-b/E-3-a-b/_",
            dry_run=dry_run,
        )
        if payload is not None:
            ctx.stage_results["execute"] = StageResult(
                stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload=payload
            )
        return ctx

    def _loop(self, ctx: FlowContext) -> dict[str, Any]:
        # A clean PR with no CI checks reported (CI falls back to local tests); the post succeeds on the fake.
        github = FakeGitHub(standing_view=merge_view())
        with patch("shutil.which", return_value=None), _verified_diff():
            # No CI registration wait: these tests are about Stage 3 evidence, as when gh was absent.
            return run_review_loop(ctx, auto_pr=False, max_iterations=1, ci_registration_timeout=0, github=github)

    def test_local_tests_fallback_without_recorded_result_is_not_measured_and_never_approves(self, tmp_path):
        res = self._loop(self._ctx(tmp_path, {"exit_code": 0, "test_pass_rate": None}))

        assert res["consensus"]["approved"] is False
        assert res["ci_checks"]["all_passed"] is False
        assert res["ci_checks"]["measured"] is False
        assert res["ci_checks"]["checks"] == [{"name": "local_tests", "bucket": "skipping", "state": "NOT_MEASURED"}]
        assert res["findings"] == ["FIND-CI-001"]

    def test_review_loop_without_stage3_result_never_approves_on_local_tests(self, tmp_path):
        res = self._loop(self._ctx(tmp_path, None))

        assert res["consensus"]["approved"] is False
        assert res["ci_checks"]["measured"] is False

    def test_recorded_passing_result_approves(self, tmp_path):
        res = self._loop(self._ctx(tmp_path, {"head_sha": "stage3-head", "exit_code": 0, "test_pass_rate": 1.0}))

        assert res["consensus"]["approved"] is True
        assert res["ci_checks"]["all_passed"] is True
        assert "measured" not in res["ci_checks"]

    def test_recorded_failing_result_fails_local_tests(self, tmp_path):
        res = self._loop(self._ctx(tmp_path, {"head_sha": "stage3-head", "exit_code": 0, "test_pass_rate": 0.5}))

        assert res["consensus"]["approved"] is False
        assert res["ci_checks"]["checks"] == [{"name": "local_tests", "bucket": "fail", "state": "FAILURE"}]
        assert res["findings"] == ["FIND-CI-001"]

    def test_dry_run_flags_unmeasured_tests_on_its_simulated_consensus(self, tmp_path):
        res = self._loop(self._ctx(tmp_path, {"exit_code": 0}, dry_run=True))

        assert res["ci_checks"]["measured"] is False

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (
                {"exit_code": 0, "test_pass_rate": None},
                {"exit_code": 0, "pass_rate": None, "measured": False, "passed": None},
            ),
            ({"exit_code": 0}, {"exit_code": 0, "pass_rate": None, "measured": False, "passed": None}),
            (
                {"head_sha": "stage3-head", "exit_code": 0, "test_pass_rate": 1.0},
                {"exit_code": 0, "pass_rate": 1.0, "measured": True, "passed": True},
            ),
            (
                {"head_sha": "stage3-head", "exit_code": 0, "test_pass_rate": 0.5},
                {"exit_code": 0, "pass_rate": 0.5, "measured": True, "passed": False},
            ),
            ({"exit_code": 2}, {"exit_code": 2, "pass_rate": None, "measured": False, "passed": False}),
        ],
    )
    def test_review_package_test_results_reflect_recorded_evidence(self, tmp_path, payload, expected):
        ctx = self._ctx(tmp_path, payload)
        loop_res = {"consensus": {"approved": False}, "mergeability": {"is_dirty": False}}
        with (
            patch("sandbox_executor.flow.stages.review.run_git", return_value=MagicMock(returncode=0, stdout="")),
            patch("sandbox_executor.flow.stages.review.at_stage_boundary", return_value=None),
            patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=loop_res),
        ):
            res = run_review_stage(ctx)

        results = res.payload["review_package"]["test_results"]
        assert {key: results[key] for key in expected} == expected
        assert {results[key] for key in ("source", "run_error")} == {None}

    def test_review_stage_approval_wording_and_status(self, tmp_path):
        ctx = self._ctx(tmp_path, {"head_sha": "stage3-head", "exit_code": 0, "test_pass_rate": 1.0})
        loop_res = {
            "consensus": {"approved": True, "status": "approved"},
            "mergeability": {"is_dirty": False},
            "reviewers": [{"status": "COMPLETED", "verdict": "APPROVED"}],
        }
        with (
            patch("sandbox_executor.flow.stages.review.run_git", return_value=MagicMock(returncode=0, stdout="")),
            patch("sandbox_executor.flow.stages.review.at_stage_boundary", return_value=None),
            patch("sandbox_executor.flow.stages.review._checkout_execution_branch", return_value=None),
            patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=loop_res),
        ):
            res = run_review_stage(ctx)

        assert res.status == StageStatus.APPROVED
        assert res.summary_line() == "review: approved"
        assert res.payload["approved"] is True
        assert res.payload["ready_for_maintainer_merge"] is True
        assert res.payload["halted_for_human"] is False
        assert any("Stage 4 approved: Review Loop reached consensus approval" in log for log in ctx.logs)
        assert not any("SAFETY HALT" in log for log in ctx.logs)

    def test_review_stage_dry_run_wording_and_status(self, tmp_path):
        ctx = self._ctx(tmp_path, {"head_sha": "stage3-head", "exit_code": 0, "test_pass_rate": 1.0}, dry_run=True)
        loop_res = {
            "consensus": {"approved": False, "status": "not_measured"},
            "mergeability": {"is_dirty": False},
            "reviewers": [{"status": "COMPLETED", "verdict": "APPROVED"}],
        }
        with (
            patch("sandbox_executor.flow.stages.review.run_git", return_value=MagicMock(returncode=0, stdout="")),
            patch("sandbox_executor.flow.stages.review.at_stage_boundary", return_value=None),
            patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=loop_res),
        ):
            res = run_review_stage(ctx)

        assert res.status == StageStatus.HALTED_FOR_HUMAN
        assert res.summary_line() == "review: halted"
        assert res.payload["halted_for_human"] is True
        assert any("SAFETY HALT" in log for log in ctx.logs)
        assert any("Dry-run review loop finished; consensus not measured and not approved." in log for log in ctx.logs)


def test_the_review_stage_builds_one_adapter_and_hands_it_to_the_loop(tmp_path):
    """Stage 4 constructs exactly one ``GitHubAdapter`` over the worktree and passes that instance to the loop."""
    ctx = FlowContext(repo_dir=str(tmp_path), execution_branch="I-1-x/P-2-a-b/E-3-a-b/_", token_reduce=False)
    loop_res = {"consensus": {"approved": False, "status": "failed"}, "mergeability": {"is_dirty": False}}
    github = FakeGitHub()
    with (
        patch("sandbox_executor.flow.stages.review.run_git", return_value=MagicMock(returncode=0, stdout="")),
        patch("sandbox_executor.flow.stages.review.at_stage_boundary", return_value=None),
        patch("sandbox_executor.flow.stages.review._checkout_execution_branch", return_value=None),
        patch("sandbox_executor.flow.stages.review.GitHubAdapter", return_value=github) as adapter,
        patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=loop_res) as loop,
    ):
        run_review_stage(ctx)

    adapter.assert_called_once_with(ctx.target_worktree, {})
    assert loop.call_args.kwargs["github"] is github


def _approved_stage(ctx: FlowContext, loop_res: dict[str, Any]) -> StageResult:
    with (
        patch("sandbox_executor.flow.stages.review.run_git", return_value=MagicMock(returncode=0, stdout="")),
        patch("sandbox_executor.flow.stages.review.at_stage_boundary", return_value=None),
        patch("sandbox_executor.flow.stages.review._checkout_execution_branch", return_value=None),
        patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=loop_res),
    ):
        return run_review_stage(ctx)


def test_the_review_stage_reports_the_counted_review_accounting(tmp_path):
    """Counts from the context reach the Stage 4 log, the payload and the flow summary lines."""
    from sandbox_executor.flow import review_summary_lines
    from sandbox_executor.review_accounting import count_accounting, count_carry_over

    ctx = FlowContext(repo_dir=str(tmp_path), execution_branch="I-1-x/P-2-a-b/E-3-a-b/_", token_reduce=False)
    for key in ("full_reviews", "scoped_reviews", "scoped_reviews", "pipeline_runs", "pipeline_reruns"):
        count_accounting(ctx, key)
    count_accounting(ctx, "non_defect_churn", "ci_noise")
    count_carry_over(ctx, "a" * 40, "b" * 40)
    loop_res = {
        "consensus": {"approved": True, "status": "approved"},
        "mergeability": {"is_dirty": False},
        "reviewers": [{"status": "COMPLETED", "verdict": "APPROVED"}],
    }

    res = _approved_stage(ctx, loop_res)

    accounting = res.payload["review_accounting"]
    assert res.payload["review_package"]["review_accounting"] == accounting
    assert (accounting["full_reviews"], accounting["scoped_reviews"], accounting["approval_carry_overs"]) == (1, 2, 1)
    assert "seen_events" not in accounting
    lines = review_summary_lines(loop_res)
    assert "  Reviews        : 1 full, 2 scoped; carry-overs: 1" in lines
    assert "  Non-defect churn: target merge 1, bookkeeping 0, CI noise 1" in lines
    assert any("Pipelines      : 1 run(s), 1 rerun(s)" in log for log in ctx.logs)


def test_a_legacy_loop_result_renders_review_churn_not_recorded(tmp_path):
    from sandbox_executor.flow import review_summary_lines

    ctx = FlowContext(repo_dir=str(tmp_path), execution_branch="I-1-x/P-2-a-b/E-3-a-b/_", token_reduce=False)
    loop_res = {"consensus": {"approved": False, "status": "failed"}, "mergeability": {"is_dirty": False}}

    res = _approved_stage(ctx, loop_res)

    assert res.payload["review_accounting"] is None
    assert "  Review churn   : not recorded" in review_summary_lines({"consensus": {"approved": False}})
    assert any("Review churn   : not recorded" in log for log in ctx.logs)
