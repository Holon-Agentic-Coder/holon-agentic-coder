"""Tests for the Stage 4 review-resolve loop and the engine-owned loop."""

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor import flow as flow_module
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    PipelineEngine,
    StageResult,
    StageStatus,
    format_review_git_trailers,
    load_checkpoint,
    probe_ci_checks,
    run_review_loop,
    run_review_loop_cli,
    run_review_stage,
    save_checkpoint,
)

from tests.flow.helpers import _review_loop_gh_stubs
from tests.flow.lookup_patch import patch_lookup_sites


class TestReviewLoopStage:
    """Comprehensive tests for the native multi-pass review loop, CI check probing, git trailers and merge halt."""

    def test_probe_ci_checks_github_cli_success(self):
        checks_json = json.dumps(
            [
                {"bucket": "pass", "name": "unit-tests", "state": "COMPLETED", "event": "pull_request"},
                {"bucket": "pass", "name": "lint", "state": "COMPLETED", "event": "pull_request"},
            ]
        )
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run") as mock_subproc,
        ):
            mock_subproc.return_value = MagicMock(returncode=0, stdout=checks_json)
            res = probe_ci_checks("/tmp/repo", "my-exec-branch")
            assert res["source"] == "github_cli"
            assert res["total"] == 2
            assert res["passed"] == 2
            assert res["failed"] == 0
            assert res["pending"] == 0
            assert res["all_passed"] is True

    def test_probe_ci_checks_github_cli_failures(self):
        checks_json = json.dumps(
            [
                {"bucket": "pass", "name": "unit-tests", "state": "COMPLETED", "event": "pull_request"},
                {"bucket": "fail", "name": "lint", "state": "FAILURE", "event": "pull_request"},
            ]
        )
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run") as mock_subproc,
        ):
            mock_subproc.return_value = MagicMock(returncode=0, stdout=checks_json)
            res = probe_ci_checks("/tmp/repo", "my-exec-branch")
            assert res["source"] == "github_cli"
            assert res["total"] == 2
            assert res["passed"] == 1
            assert res["failed"] == 1
            assert res["all_passed"] is False

    def test_probe_ci_checks_fallback_when_gh_missing(self):
        with patch("shutil.which", return_value=None):
            res = probe_ci_checks("/tmp/repo", "my-exec-branch")
            assert res["source"] == "local_tests"
            assert res["all_passed"] is True
            assert res["passed"] == 1
            assert res["failed"] == 0

    def test_format_review_git_trailers(self):
        trailers = format_review_git_trailers(
            action_origin="pr_review",
            iteration=3,
            finding_ids=["FIND-001", "FIND-002"],
            severity="important",
        )
        assert "Action-Origin: pr_review" in trailers
        assert "Review-Iteration: 3" in trailers
        assert "Review-Finding-Ids: FIND-001, FIND-002" in trailers
        assert "Review-Severity: important" in trailers

        clean_trailers = format_review_git_trailers(iteration=1, finding_ids=None, severity="clean")
        assert "Review-Finding-Ids: none" in clean_trailers
        assert "Review-Severity: clean" in clean_trailers

    def test_review_loop_consensus_approval_halts_without_merge(self, tmp_path):
        """Verify review loop halts with HALTED_FOR_HUMAN and never invokes git merge main."""
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            dry_run=False,
        )
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"test_pass_rate": 1.0, "exit_code": 0},
        )
        with (
            patch_lookup_sites(
                "sandbox_executor.flow.git_refs.run_git",
                "sandbox_executor.flow.github.pr.run_git",
                "sandbox_executor.flow.stage_conflicts.run_git",
                "sandbox_executor.flow.stages.calibrate.run_git",
                "sandbox_executor.flow.calibrate_sync.run_git",
                "sandbox_executor.flow.stages.review.run_git",
            ) as mock_git,
            patch("sandbox_executor.flow._execution_effective_diff", return_value=(True, "1 file changed")),
            patch("sandbox_executor.flow.review.loop.create_or_update_pr", return_value={"success": True}),
            _review_loop_gh_stubs(),
        ):
            mock_git.return_value = MagicMock(returncode=0, stdout=" 1 file changed, 10 insertions(+)\n")
            res = run_review_loop(ctx, target="I-100-intent/P-200-plan/E-300-exec/_", max_iterations=5)

            # Human-only merge check: git merge main must never have been called
            for call in mock_git.call_args_list:
                args = call[0][0]
                assert "merge" not in args, f"HUMAN-ONLY MERGE RULE VIOLATED: merge invoked with args {args}"

        assert res["consensus"]["approved"] is True
        assert res["halted_for_human"] is True
        assert res["human_merge_gate_enforced"] is True
        assert "holon review approve" in res["human_approval_command"]

    def test_review_loop_stops_after_the_first_non_approved_iteration(self, tmp_path):
        """A non-approved iteration stops the loop at once, well inside ``max_iterations``, since no resolver runs."""
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            dry_run=True,
        )
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.FAILED,
            payload={"test_pass_rate": 0.0, "exit_code": 1},
        )
        res = run_review_loop(ctx, target="I-100-intent/P-200-plan/E-300-exec/_", max_iterations=3, dry_run=True)
        assert res["consensus"]["approved"] is False
        # No resolver runs, so the loop stops after the first iteration instead of re-reviewing the same diff.
        assert res["total_iterations"] == 1
        assert res["consensus"]["status"] == "rejected"

    def test_review_loop_appends_executions_ledger(self, tmp_path):
        """Assert executions.jsonl receives structured iteration and summary records with trailers when not dry_run."""
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            dry_run=False,
        )
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"test_pass_rate": 1.0, "exit_code": 0},
        )

        with (
            patch("sandbox_executor.flow.github.pr.run_git") as mock_git,
            patch("sandbox_executor.flow.review.loop.post_pr_review_consensus") as mock_post,
            patch("sandbox_executor.flow._execution_effective_diff", return_value=(True, "1 file changed")),
            patch("sandbox_executor.flow.review.loop.create_or_update_pr", return_value={"success": True}),
        ):
            mock_git.return_value = MagicMock(returncode=0, stdout=" 1 file changed, 10 insertions(+)\n")
            mock_post.return_value = {"dual_posted": True, "official_review": {"success": True}}
            run_review_loop(ctx, target="I-100-intent/P-200-plan/E-300-exec/_", max_iterations=2, dry_run=False)

        exec_file = ledger_dir / "executions.jsonl"
        assert exec_file.exists()

        records = [json.loads(line) for line in exec_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        action_records = [r for r in records if r.get("entry_type") == "action"]
        summary_records = [r for r in records if r.get("entry_type") == "summary"]

        assert len(action_records) >= 1
        assert action_records[0]["action_origin"] == "pr_review"
        assert action_records[0]["review_iteration"] == 1

        assert len(summary_records) >= 1
        assert summary_records[0]["status"] == "halted_for_human"
        assert summary_records[0]["total_iterations"] == 1
        assert summary_records[0]["trailers"]["action_origin"] == "pr_review"

    def test_run_review_loop_cli_direct(self, tmp_path, capsys):
        """Directly exercise run_review_loop_cli return codes and summary output formatting."""
        args = argparse.Namespace(
            target="I-100-intent/P-200-plan/E-300-exec/_",
            repo_dir=str(tmp_path),
            max_iterations=2,
            ensemble_size=3,
            no_auto_pr=False,
            dry_run=True,
            json=False,
            token_reduce=False,
        )
        with patch("sandbox_executor.flow.review.cli.run_review_loop") as mock_loop:
            mock_loop.return_value = {
                "consensus": {"approved": True, "status": "approved", "iterations": 1},
                "ensemble_verdict": "APPROVED",
                "max_iterations": 2,
                "ci_checks": {"total": 1, "passed": 1, "failed": 0, "pending": 0, "source": "local_tests"},
                "human_approval_command": "holon review approve I-100-intent/P-200-plan/E-300-exec/_",
            }
            code = run_review_loop_cli(args)
            assert code == 0
            captured = capsys.readouterr().out
            assert "Holon Review Loop Summary" in captured
            assert "[SAFETY HALT]" in captured
            assert "holon review approve" in captured

        # Test rejected exit code
        with patch("sandbox_executor.flow.review.cli.run_review_loop") as mock_loop:
            mock_loop.return_value = {
                "consensus": {"approved": False, "status": "rejected", "iterations": 2},
                "max_iterations": 2,
                "ci_checks": {"total": 1, "passed": 0, "failed": 1, "pending": 0, "source": "local_tests"},
            }
            code = run_review_loop_cli(args)
            assert code == 1

    def test_review_loop_requires_pushed_true_to_log_and_advance_head(self, tmp_path, monkeypatch):
        """Review loop requires resolver_outcome.pushed to be True before logging new head and advancing target."""
        from sandbox_executor.flow import ResolverOutcome, run_review_loop
        from sandbox_executor.review_report import FindingDisposition

        from tests.review_fakes import FakeEnsemble, install_fake_reviewers, objecting_reviewer
        from tests.test_review_dispatch import EXEC_TARGET
        from tests.test_review_loop import _ensemble_ctx, _LoopStubs

        disp = FindingDisposition(
            id="R1-F1",
            severity="important",
            path="src/app.py",
            line=12,
            summary="important problem in the handler",
            disposition="applied",
        )

        # 1. When pushed is False: context.log must NOT log that a new head was pushed
        tmp1 = tmp_path / "case_unpushed"
        ensemble1 = FakeEnsemble([objecting_reviewer(1)])
        install_fake_reviewers(monkeypatch, ensemble1)
        ctx1 = _ensemble_ctx(tmp1)
        outcome_unpushed = ResolverOutcome(dispositions=(disp,), head_sha="unpushed_sha_123", pushed=False)
        with _LoopStubs():
            run_review_loop(
                ctx1,
                target=EXEC_TARGET,
                max_iterations=2,
                ensemble_size=1,
                run_ensemble_fn=ensemble1,
                run_resolver_fn=lambda *args, **kwargs: outcome_unpushed,
            )
        assert not any("Resolver pushed new head" in log for log in ctx1.logs)

        # 2. When pushed is True: context.log MUST log that resolver pushed new head
        tmp2 = tmp_path / "case_pushed"
        ensemble2 = FakeEnsemble([objecting_reviewer(1)])
        install_fake_reviewers(monkeypatch, ensemble2)
        ctx2 = _ensemble_ctx(tmp2)
        outcome_pushed = ResolverOutcome(dispositions=(disp,), head_sha="pushed_sha_456", pushed=True)
        with _LoopStubs():
            run_review_loop(
                ctx2,
                target=EXEC_TARGET,
                max_iterations=2,
                ensemble_size=1,
                run_ensemble_fn=ensemble2,
                run_resolver_fn=lambda *args, **kwargs: outcome_pushed,
            )
        assert any("Resolver pushed new head 'pushed_sha_456'" in log for log in ctx2.logs)


class TestEngineOwnedReviewLoop:
    """Stage 4: the engine owns the loop, dispatches one pass at a time and labels it with its own count."""

    @pytest.mark.parametrize("iteration", [1, 4, 25])
    def test_dispatch_requests_one_pass_and_labels_it_with_the_engine_iteration(self, tmp_path, monkeypatch, iteration):
        from sandbox_executor.review_target import ReviewTarget

        monkeypatch.setenv("HOME", str(tmp_path))
        captured: list[list[str]] = []

        def fake_dispatch(context, role, args, result_file=None, **_):
            captured.append(list(args))
            Path(result_file).write_text(json.dumps({"reviewers": [], "verdict": "FAILED"}), encoding="utf-8")
            return 0

        ctx = FlowContext(repo_dir=str(tmp_path), token_reduce=False)
        target = ReviewTarget(raw="feature", head_branch="feature", head_sha="a" * 40)
        with patch("sandbox_executor.flow.review.ensemble._dispatch_role", fake_dispatch):
            flow_module._dispatch_review_ensemble(ctx, target, iteration, 3)

        (args,) = captured
        limits = [a for a in args if a.startswith("--max-iterations")]
        assert limits == ["--max-iterations=1"]
        assert [a for a in args if a.startswith("--iteration")] == [f"--iteration={iteration}"]

    @staticmethod
    def _resumable_context(tmp_path: Path, done: int, budget: int) -> FlowContext:
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "resume-review"},
            execution_branch="I-1-resume-review/P-2-a-m/E-3-a-m/_",
            dry_run=True,
            token_reduce=False,
            max_review_iterations=budget,
            review_iterations_done=done,
        )
        path = save_checkpoint(ctx, str(tmp_path / "checkpoint.json"))
        return load_checkpoint(path).context

    def test_resume_continues_the_checkpointed_count(self, tmp_path):
        ctx = self._resumable_context(tmp_path, done=3, budget=5)
        assert ctx.review_iterations_done == 3
        loop_res = {"consensus": {"iterations": 4, "passes": 1, "max_review_iterations": 2}, "total_iterations": 4}

        with patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=loop_res) as loop:
            result = run_review_stage(ctx)

        assert loop.call_args.kwargs["first_iteration"] == 4
        assert loop.call_args.kwargs["max_iterations"] == 2
        assert ctx.review_iterations_done == 4
        assert result.payload["consensus"]["max_review_iterations"] == 5
        saved = save_checkpoint(ctx, str(tmp_path / "after.json"))
        assert load_checkpoint(saved).context.review_iterations_done == 4

    def test_an_exhausted_engine_budget_fails_without_dispatching(self, tmp_path):
        ctx = self._resumable_context(tmp_path, done=5, budget=5)

        with patch("sandbox_executor.flow.review.loop.run_review_loop") as loop:
            result = run_review_stage(ctx)

        loop.assert_not_called()
        assert result.status == StageStatus.FAILED
        assert "exhausted after 5 iteration(s)" in str(result.error)
        assert ctx.review_iterations_done == 5
        # The synthetic consensus speaks label units: iterations = label of the latest pass run (the count), 0 passes.
        assert result.payload["consensus"]["iterations"] == 5
        assert result.payload["consensus"]["passes"] == 0

    def test_an_exhausted_budget_clamps_a_malformed_negative_count(self, tmp_path):
        ctx = self._resumable_context(tmp_path, done=-2, budget=0)

        with patch("sandbox_executor.flow.review.loop.run_review_loop") as loop:
            result = run_review_stage(ctx)

        loop.assert_not_called()
        assert result.status == StageStatus.FAILED
        # The synthetic consensus never reports a negative label: the raw count is clamped like _review_window does.
        assert result.payload["consensus"]["iterations"] == 0

    def test_old_checkpoint_without_the_count_loads_as_zero(self):
        data = FlowContext(token_reduce=False).to_dict()
        data.pop("review_iterations_done")

        assert FlowContext.from_dict(data).review_iterations_done == 0

    def test_review_ceiling_counts_passes_not_the_label(self):
        engine = PipelineEngine(FlowContext(token_reduce=False))
        per_pass = engine.budgets.stage_seconds("review_iteration")
        labelled = StageResult(stage=FlowStage.REVIEW, status=StageStatus.FAILED, payload={})
        labelled.payload["consensus"] = {"iterations": 4, "passes": 1}

        assert engine.stage_budget_seconds(FlowStage.REVIEW, labelled) == per_pass

    def test_review_ceiling_honours_a_present_zero_passes_over_the_label(self):
        """A resumed run that dispatched nothing records passes == 0; the label must not resize the ceiling."""
        engine = PipelineEngine(FlowContext(token_reduce=False))
        per_pass = engine.budgets.stage_seconds("review_iteration")
        spent = StageResult(stage=FlowStage.REVIEW, status=StageStatus.FAILED, payload={})
        spent.payload["consensus"] = {"iterations": 4, "passes": 0}

        assert engine.stage_budget_seconds(FlowStage.REVIEW, spent) == per_pass
