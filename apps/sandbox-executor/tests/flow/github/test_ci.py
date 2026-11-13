"""Tests for the bounded wait for CI checks to register when Stage 3 measured no tests."""

import json
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.flow import (
    CI_REGISTRATION_TIMEOUT_ENV_VAR,
    CI_REGISTRATION_TIMEOUT_SECONDS,
    TESTS_NOT_MEASURED_ERROR,
    FlowContext,
    FlowStage,
    StageResult,
    StageStatus,
    resolve_ci_registration_timeout,
    run_review_loop,
    run_review_stage,
)

from tests.flow.helpers import _verified_diff
from tests.flow.lookup_patch import patch_lookup_sites
from tests.flow.sleep_patch import scoped_sleep_patch


class TestUnmeasuredTestsStopEarly:
    """C11-N1/C11-N2: unmeasured tests wait boundedly for CI to register, then stop once with NOT_MEASURED."""

    LOCAL: ClassVar[dict[str, Any]] = {
        "source": "local_tests",
        "total": 1,
        "passed": 1,
        "failed": 0,
        "pending": 0,
        "skipped": 0,
        "all_passed": True,
        "checks": [{"name": "local_tests", "bucket": "pass", "state": "SUCCESS"}],
    }
    GITHUB_PASS: ClassVar[dict[str, Any]] = {
        "source": "github_cli",
        "total": 1,
        "passed": 1,
        "failed": 0,
        "pending": 0,
        "skipped": 0,
        "all_passed": True,
        "checks": [{"name": "test", "bucket": "pass", "state": "SUCCESS"}],
    }

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

    @staticmethod
    def _ledger_rows(tmp_path: Path) -> list[dict[str, Any]]:
        ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
        if not ledger.exists():
            return []
        return [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]

    def _loop(
        self,
        ctx: FlowContext,
        probe_side_effect: Any,
        *,
        gh: str | None = "/usr/bin/gh",
        **kwargs: Any,
    ) -> tuple[dict[str, Any], MagicMock, MagicMock]:
        probe = MagicMock(side_effect=probe_side_effect)
        sleep = MagicMock()
        with (
            patch("sandbox_executor.flow.review.loop.shutil.which", return_value=gh),
            patch_lookup_sites(
                "sandbox_executor.flow.github.ci.probe_ci_checks",
                "sandbox_executor.flow.review.loop.probe_ci_checks",
                probe,
            ),
            patch("sandbox_executor.flow.review.loop.check_pr_mergeability", return_value={"is_dirty": False}),
            patch("sandbox_executor.flow.review.loop.post_pr_review_consensus", return_value={"dual_posted": True}),
            patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc123"),
            scoped_sleep_patch("sandbox_executor.flow.github.ci", sleep),
            scoped_sleep_patch("sandbox_executor.flow.review.loop", sleep),
            # No real ``git rev-parse``: keep the test hermetic and fast.
            patch("sandbox_executor.flow.review.local_tests._ReviewLocalTests._head", lambda self: self._head_sha),
            _verified_diff(),
        ):
            res = run_review_loop(ctx, auto_pr=False, max_iterations=25, **kwargs)
        return res, probe, sleep

    def test_checks_registering_after_a_delay_burn_no_review_iterations(self, tmp_path):
        ctx = self._ctx(tmp_path, {"exit_code": 0, "test_pass_rate": None})
        probes = [dict(self.LOCAL), dict(self.LOCAL), dict(self.GITHUB_PASS), dict(self.GITHUB_PASS)]

        res, probe, sleep = self._loop(ctx, probes, ci_registration_timeout=600.0, ci_poll_interval=5.0)

        assert res["consensus"]["approved"] is True
        assert res["total_iterations"] == 1
        assert res["ci_checks"]["source"] == "github_cli"
        assert probe.call_count == 4
        assert [c.args[0] for c in sleep.call_args_list] == [5.0, 5.0]
        rows = self._ledger_rows(tmp_path)
        assert not [r for r in rows if str(r.get("action_id", "")).startswith("ACT-RES")]
        rev_rows = [r for r in rows if str(r.get("action_id", "")).startswith("ACT-REV")]
        # One evaluation row for the iteration plus one row per (fake) reviewer.
        assert len([r for r in rev_rows if "evaluation for" in r["action"]]) == 1
        assert len([r for r in rev_rows if "review_reviewer_index" in r]) == 3

    def test_checks_never_registering_stop_once_with_not_measured(self, tmp_path):
        ctx = self._ctx(tmp_path, {"exit_code": 0, "test_pass_rate": None})
        clock = iter(float(t) for t in range(0, 1000, 5))

        with patch("sandbox_executor.flow.github.ci.time.monotonic", side_effect=lambda: next(clock)):
            res, _probe, sleep = self._loop(
                ctx, lambda *a, **k: dict(self.LOCAL), ci_registration_timeout=20.0, ci_poll_interval=5.0
            )

        assert res["consensus"]["approved"] is False
        assert res["consensus"]["status"] == "not_measured"
        assert res["total_iterations"] == 1
        assert res["ci_checks"]["measured"] is False
        assert res["findings"] == ["FIND-CI-001"]
        assert 1 <= sleep.call_count <= 4
        rows = self._ledger_rows(tmp_path)
        assert not [r for r in rows if str(r.get("action_id", "")).startswith("ACT-RES")]
        rev_rows = [r for r in rows if str(r.get("action_id", "")).startswith("ACT-REV")]
        assert len([r for r in rev_rows if "evaluation for" in r["action"]]) == 1
        assert len([r for r in rev_rows if "review_reviewer_index" in r]) == 3
        assert len([r for r in rows if r.get("entry_type") == "summary"]) == 1
        # Evaluation, three reviewer rows, FIND-CI, ACT-POST, summary and the action event.
        assert len(rows) <= 8

    def test_recorded_result_skips_the_registration_wait(self, tmp_path):
        ctx = self._ctx(tmp_path, {"exit_code": 0, "test_pass_rate": 1.0})

        res, probe, sleep = self._loop(ctx, lambda *a, **k: dict(self.LOCAL), ci_registration_timeout=600.0)

        assert res["consensus"]["approved"] is True
        assert probe.call_count == 1
        sleep.assert_not_called()

    def test_without_gh_unmeasured_tests_stop_after_one_iteration_without_waiting(self, tmp_path):
        ctx = self._ctx(tmp_path, {"exit_code": 0, "test_pass_rate": None})

        res, _probe, sleep = self._loop(ctx, lambda *a, **k: dict(self.LOCAL), gh=None)

        assert res["consensus"]["status"] == "not_measured"
        assert res["total_iterations"] == 1
        sleep.assert_not_called()

    def test_dry_run_keeps_its_simulated_consensus_without_waiting(self, tmp_path):
        ctx = self._ctx(tmp_path, {"exit_code": 0}, dry_run=True)

        res, _probe, sleep = self._loop(ctx, lambda *a, **k: dict(self.LOCAL), ci_registration_timeout=600.0)

        # A simulated approval is never an approval: the dry run reports not_measured.
        assert res["consensus"]["approved"] is False
        assert res["consensus"]["status"] == "not_measured"
        assert res["ci_checks"]["measured"] is False
        sleep.assert_not_called()
        assert self._ledger_rows(tmp_path) == []

    def test_reviewer_findings_bypass_ci_registration_wait_even_when_tests_unmeasured(self, tmp_path):
        from tests.review_fakes import FakeEnsemble, objecting_reviewer

        ctx = self._ctx(tmp_path, {"exit_code": 0, "test_pass_rate": None})

        res, probe, sleep = self._loop(
            ctx,
            lambda *a, **k: dict(self.LOCAL),
            ci_registration_timeout=600.0,
            run_ensemble_fn=FakeEnsemble([objecting_reviewer(1)]),
        )

        assert res["consensus"]["approved"] is False
        sleep.assert_not_called()
        assert probe.call_count == 1
        assert not any("FIND-CI" in fid for fid in res["findings"])

    @pytest.mark.parametrize(
        ("explicit", "env", "expected"),
        [
            (None, {}, CI_REGISTRATION_TIMEOUT_SECONDS),
            (30.0, {CI_REGISTRATION_TIMEOUT_ENV_VAR: "90"}, 30.0),
            (None, {CI_REGISTRATION_TIMEOUT_ENV_VAR: "90"}, 90.0),
            (None, {CI_REGISTRATION_TIMEOUT_ENV_VAR: "0"}, 0.0),
            (None, {CI_REGISTRATION_TIMEOUT_ENV_VAR: "bogus"}, CI_REGISTRATION_TIMEOUT_SECONDS),
            (None, {CI_REGISTRATION_TIMEOUT_ENV_VAR: "-5"}, CI_REGISTRATION_TIMEOUT_SECONDS),
        ],
    )
    def test_registration_timeout_resolution(self, explicit, env, expected):
        assert resolve_ci_registration_timeout(explicit, environ=env) == expected

    def test_stage4_reports_unmeasured_tests_distinctly_from_failures(self, tmp_path):
        ctx = self._ctx(tmp_path, {"exit_code": 0, "test_pass_rate": None})
        loop_res = {
            "consensus": {"approved": False, "status": "not_measured"},
            "mergeability": {"is_dirty": False},
            "ci_checks": {"source": "local_tests", "measured": False, "all_passed": False},
        }
        with (
            patch("sandbox_executor.flow.stages.review.run_git", return_value=MagicMock(returncode=0, stdout="")),
            patch("sandbox_executor.flow.stages.review.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=loop_res),
        ):
            res = run_review_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert res.error == TESTS_NOT_MEASURED_ERROR
        assert "reported failures" not in res.error

    def test_stage4_keeps_the_failure_message_for_measured_failures(self, tmp_path):
        ctx = self._ctx(tmp_path, {"exit_code": 0, "test_pass_rate": 0.5})
        loop_res = {
            "consensus": {"approved": False, "status": "rejected"},
            "mergeability": {"is_dirty": False},
            "ci_checks": {"source": "local_tests", "all_passed": False},
            "tests_failed": True,
        }
        with (
            patch("sandbox_executor.flow.stages.review.run_git", return_value=MagicMock(returncode=0, stdout="")),
            patch("sandbox_executor.flow.stages.review.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=loop_res),
        ):
            res = run_review_stage(ctx)

        assert res.error == "Autonomous review consensus failed: test suite reported failures."

    @pytest.mark.parametrize(
        ("has_diff", "detail", "reason", "expected"),
        [
            (False, "no changes outside the ledgers", "empty_diff", "no changes outside the ledgers"),
            (None, "fetch failed: origin unreachable", "diff_unverified", "could not verify the execution diff"),
        ],
    )
    def test_guard_refusal_is_reported_as_its_own_reason_without_a_ci_wait(
        self, tmp_path, has_diff, detail, reason, expected
    ):
        """C13E-N1: a refused PR runs no tests and triggers no CI, so Stage 4 neither waits nor blames the tests."""
        from sandbox_executor import flow as flow_module

        ctx = self._ctx(tmp_path, {"exit_code": 0, "test_pass_rate": None})
        probe = MagicMock(return_value=dict(self.LOCAL))
        sleep = MagicMock()
        wait = MagicMock(wraps=flow_module._wait_for_ci_registration)
        with (
            patch("sandbox_executor.flow.review.loop.shutil.which", return_value="/usr/bin/gh"),
            patch("sandbox_executor.flow.stages.review.run_git", return_value=MagicMock(returncode=0, stdout="")),
            patch("sandbox_executor.flow.stages.review.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow._execution_effective_diff", return_value=(has_diff, detail)),
            patch("sandbox_executor.flow.review.loop.create_or_update_pr") as mock_pr,
            patch_lookup_sites(
                "sandbox_executor.flow.github.ci.probe_ci_checks",
                "sandbox_executor.flow.review.loop.probe_ci_checks",
                probe,
            ),
            patch_lookup_sites(
                "sandbox_executor.flow.review.loop.check_pr_mergeability",
                "sandbox_executor.flow.stage_conflicts.check_pr_mergeability",
                return_value={"is_dirty": False},
            ),
            patch("sandbox_executor.flow.review.loop.post_pr_review_consensus", return_value={"dual_posted": True}),
            patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc123"),
            scoped_sleep_patch("sandbox_executor.flow.review.loop", sleep),
            patch("sandbox_executor.flow.review.loop._wait_for_ci_registration", wait),
        ):
            res = run_review_stage(ctx)

        mock_pr.assert_not_called()
        sleep.assert_not_called()
        wait.assert_not_called()
        assert res.status == StageStatus.FAILED
        assert res.error.startswith("Autonomous review halted: Refusing to create or update a PR for ")
        assert expected in res.error
        assert "reported failures" not in res.error
        assert res.error != TESTS_NOT_MEASURED_ERROR
        assert res.payload["pr"]["reason"] == reason
        summaries = [r for r in self._ledger_rows(tmp_path) if r.get("entry_type") == "summary"]
        if reason == "empty_diff":
            # The guard returns before any review iteration, so it writes no review rows.
            probe.assert_not_called()
            assert summaries == []
        else:
            # C15-N1: fail closed, the refused PR is never reviewed, so only the halted summary row is written.
            probe.assert_not_called()
            assert len(summaries) == 1
            assert summaries[0]["status"] == "failed"
            assert "could not verify the execution diff" in summaries[0]["summary"]

    GREEN_EVIDENCE: ClassVar[list[Any]] = [
        pytest.param({"exit_code": 0, "test_pass_rate": 1.0}, None, id="stage3-pass-rate-1.0"),
        pytest.param(
            {"exit_code": 0, "test_pass_rate": None},
            {"source": "github_cli", "all_passed": True, "total": 1, "passed": 1, "pending": 0},
            id="green-ci-on-existing-pr",
        ),
    ]

    def _refused_with_green_evidence(self, tmp_path, payload, ci, call):
        ctx = self._ctx(tmp_path, payload)
        probe = MagicMock(return_value=dict(ci or self.LOCAL))
        post = MagicMock(return_value={"dual_posted": True})
        with (
            patch("sandbox_executor.flow.review.loop.shutil.which", return_value="/usr/bin/gh"),
            patch("sandbox_executor.flow.stages.review.run_git", return_value=MagicMock(returncode=0, stdout="")),
            patch("sandbox_executor.flow.stages.review.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow._execution_effective_diff", return_value=(None, "fetch failed")),
            patch("sandbox_executor.flow.review.loop.create_or_update_pr") as mock_pr,
            patch_lookup_sites(
                "sandbox_executor.flow.github.ci.probe_ci_checks",
                "sandbox_executor.flow.review.loop.probe_ci_checks",
                probe,
            ),
            patch_lookup_sites(
                "sandbox_executor.flow.review.loop.check_pr_mergeability",
                "sandbox_executor.flow.stage_conflicts.check_pr_mergeability",
                return_value={"is_dirty": False},
            ),
            patch("sandbox_executor.flow.review.loop.post_pr_review_consensus", post),
            patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc123"),
            scoped_sleep_patch("sandbox_executor.flow.review.loop"),
            patch("sandbox_executor.flow.review.loop._wait_for_ci_registration"),
        ):
            res = call(ctx)
        mock_pr.assert_not_called()
        post.assert_not_called()
        rows = self._ledger_rows(tmp_path)
        assert not [r for r in rows if str(r.get("action_id", "")).startswith(("ACT-REV", "ACT-POST"))]
        summaries = [r for r in rows if r.get("entry_type") == "summary"]
        assert len(summaries) == 1
        assert summaries[0]["status"] == "failed"
        assert "halted without consensus" in summaries[0]["summary"]
        assert "could not verify the execution diff" in summaries[0]["summary"]
        return res

    @pytest.mark.parametrize(("payload", "ci"), GREEN_EVIDENCE)
    def test_unverified_diff_never_approves_despite_green_evidence(self, tmp_path, payload, ci):
        """C15-N1: the loop fails closed on diff_unverified instead of returning approved plus the refusal."""
        res = self._refused_with_green_evidence(
            tmp_path, payload, ci, lambda ctx: run_review_loop(ctx, max_iterations=2, dry_run=False)
        )
        assert res["consensus"]["approved"] is False
        assert res["consensus"]["status"] == "failed"
        assert res["halted_for_human"] is False
        assert res["pr"]["reason"] == "diff_unverified"
        assert "could not verify the execution diff (fetch failed)" in res["error"]

    @pytest.mark.parametrize(("payload", "ci"), GREEN_EVIDENCE)
    def test_stage4_fails_on_unverified_diff_despite_green_evidence(self, tmp_path, payload, ci):
        """C15-N1: Stage 4 fails with the refusal's reason, so Stage 5 never calibrates a refused PR."""
        res = self._refused_with_green_evidence(tmp_path, payload, ci, run_review_stage)
        assert res.status == StageStatus.FAILED
        assert res.payload["halted_for_human"] is False
        assert res.error.startswith("Autonomous review halted: Refusing to create or update a PR for ")
        assert "could not verify the execution diff (fetch failed)" in res.error
