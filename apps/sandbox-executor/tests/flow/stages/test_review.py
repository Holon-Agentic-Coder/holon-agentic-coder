"""Tests that Stage 4 never records or approves a test pass that Stage 3 did not measure."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus, run_review_loop, run_review_stage

from tests.flow.helpers import _verified_diff


class TestStage4TestEvidence:
    """C10-N1: Stage 4 never records or approves a test pass that Stage 3 did not measure."""

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
        with (
            patch("sandbox_executor.flow.review.loop.shutil.which", return_value=None),
            patch("sandbox_executor.flow.review.loop.check_pr_mergeability", return_value={"is_dirty": False}),
            patch("sandbox_executor.flow.review.loop.post_pr_review_consensus", return_value={"dual_posted": True}),
            _verified_diff(),
        ):
            return run_review_loop(ctx, auto_pr=False, max_iterations=1)

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
        res = self._loop(self._ctx(tmp_path, {"exit_code": 0, "test_pass_rate": 1.0}))

        assert res["consensus"]["approved"] is True
        assert res["ci_checks"]["all_passed"] is True
        assert "measured" not in res["ci_checks"]

    def test_recorded_failing_result_fails_local_tests(self, tmp_path):
        res = self._loop(self._ctx(tmp_path, {"exit_code": 0, "test_pass_rate": 0.5}))

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
                {"exit_code": 0, "test_pass_rate": 1.0},
                {"exit_code": 0, "pass_rate": 1.0, "measured": True, "passed": True},
            ),
            (
                {"exit_code": 0, "test_pass_rate": 0.5},
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
            patch("sandbox_executor.flow.stages.review.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=loop_res),
        ):
            res = run_review_stage(ctx)

        assert res.payload["review_package"]["test_results"] == expected
