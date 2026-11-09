"""Tests for the pipeline engine: stage orchestration, stage budgets, the flow CLI and worktree provisioning."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.cli import main as cli_main
from sandbox_executor.flow import (
    SKIP_PUSH_DISPATCH_ERROR,
    FlowContext,
    FlowStage,
    PipelineEngine,
    StageResult,
    StageStatus,
    run_calibrate_stage,
    run_execute_stage,
    run_intent_stage,
    run_plan_stage,
    run_review_loop,
    run_review_stage,
)
from sandbox_executor.git_flow import (
    derive_worktree_layout,
    ensure_worktree,
    is_bare_repository,
    is_working_tree,
    prune_worktrees,
)

from tests.flow.helpers import SteppingClock, _dispatch_context, _prefix, _read_rows, _review_loop_gh_stubs
from tests.flow.lookup_patch import patch_lookup_sites


class TestFlowStages:
    """Test the individual execution stages with isolated mocking."""

    def test_stage1_intent_validation_failure(self, tmp_path):
        ctx = FlowContext(repo_dir=str(tmp_path), intent_data={}, dry_run=True)
        res = run_intent_stage(ctx)
        assert res.status == StageStatus.FAILED
        assert "Validation failed" in (res.error or "")

    def test_intent_stage_fails_fast_on_unresolved_open_questions(self, tmp_path):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={
                "slug": "unresolved-q",
                "goal": "Build stage 1",
                "open_questions": ["What authentication protocol should we use?"],
            },
            dry_run=True,
        )
        res = run_intent_stage(ctx)
        assert res.status == StageStatus.FAILED
        assert "Unresolved questions detected" in (res.error or "")
        assert "What authentication protocol should we use?" in (res.error or "")

    def test_downstream_stages_run_unattended(self, tmp_path):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_branch="I-12345-slug/_",
            plan_branch="I-12345-slug/P-12345-agent-model/_",
            execution_branch="I-12345-slug/P-12345-agent-model/E-12345-action/_",
            dry_run=True,
        )
        plan_res = run_plan_stage(ctx)
        assert plan_res.status in (StageStatus.SUCCESS, StageStatus.PENDING)

        exec_res = run_execute_stage(ctx)
        assert exec_res.status in (StageStatus.SUCCESS, StageStatus.PENDING)

        review_res = run_review_stage(ctx)
        assert review_res.status in (StageStatus.SUCCESS, StageStatus.HALTED_FOR_HUMAN)

        cal_res = run_calibrate_stage(ctx)
        assert cal_res.status in (StageStatus.SUCCESS, StageStatus.PENDING)

    def test_stage2_plan_missing_intent_branch(self):
        ctx = FlowContext(intent_branch=None)
        res = run_plan_stage(ctx)
        assert res.status == StageStatus.FAILED
        assert "without an active intent_branch" in (res.error or "")

    def test_stage3_execute_missing_plan_branch(self):
        ctx = FlowContext(plan_branch=None)
        res = run_execute_stage(ctx)
        assert res.status == StageStatus.FAILED
        assert "without an active plan_branch" in (res.error or "")

    def test_stage4_review_missing_execution_branch(self):
        ctx = FlowContext(execution_branch=None)
        res = run_review_stage(ctx)
        assert res.status == StageStatus.FAILED
        assert "without an active execution_branch" in (res.error or "")

    def test_stage4_review_human_only_merge_halt(self, tmp_path):
        """CRITICAL INVARIANT TEST: human-only PR merging rule.

        Ensures autonomous review loop halts unconditionally with HALTED_FOR_HUMAN
        upon reaching consensus approval, and strictly NEVER executes a git merge to main.
        """
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_branch="I-100-intent/_",
            plan_branch="I-100-intent/P-200-plan/_",
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            dry_run=False,
        )
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"test_pass_rate": 1.0, "exit_code": 0},
        )

        # The empty-diff guard allows the PR: only an allowed diff can reach consensus (C15-N1).
        with (
            patch_lookup_sites(
                "sandbox_executor.flow.stage_conflicts.run_git", "sandbox_executor.flow.stages.review.run_git"
            ) as mock_git,
            patch("sandbox_executor.flow._execution_effective_diff", return_value=(True, "1 file changed")),
            patch("sandbox_executor.flow.review.loop.create_or_update_pr", return_value={"success": True}),
            _review_loop_gh_stubs(),
        ):
            mock_git.return_value = MagicMock(returncode=0, stdout=" 1 file changed, 5 insertions(+)\n")
            res = run_review_stage(ctx)

            # Assert git merge was NEVER called
            for call in mock_git.call_args_list:
                args = call[0][0]
                assert "merge" not in args, f"HUMAN-ONLY MERGE RULE VIOLATED: merge invoked with args {args}"

        assert res.status == StageStatus.HALTED_FOR_HUMAN
        assert res.payload["halted_for_human"] is True
        assert res.payload["human_merge_gate_enforced"] is True
        assert res.payload["consensus"]["approved"] is True
        assert "holon review approve" in res.payload["human_approval_command"]

    def test_stage4_review_diff_summary_excludes_execution_notes(self, tmp_path):
        """C7-I1: executions/E-*.md notes are metadata, so they never count as changed code in the review package."""
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            plan_branch="I-100-intent/P-200-plan/_",
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            dry_run=False,
        )
        numstat = (
            "40\t0\texecutions/E-300-exec.md\n"
            "3\t0\tholon-knowledge/ledger/executions.jsonl\n"
            "12\t4\tplans/P-200-plan.md\n"
            "7\t2\tsrc/app.py\n"
        )

        def fake_git(args, **kwargs):
            if args[:2] == ["diff", "--numstat"]:
                return MagicMock(returncode=0, stdout=numstat)
            return MagicMock(returncode=0, stdout="")

        loop_res = {"consensus": {"approved": True}, "mergeability": {}}
        with (
            patch_lookup_sites(
                "sandbox_executor.flow.github.pr.run_git",
                "sandbox_executor.flow.stage_conflicts.run_git",
                "sandbox_executor.flow.stages.review.run_git",
                side_effect=fake_git,
            ),
            patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=loop_res),
        ):
            res = run_review_stage(ctx)
        assert res.payload["review_package"]["diff_summary"] == {"files_changed": 1, "insertions": 7, "deletions": 2}

    def test_stage4_review_rejection_on_test_failure(self, tmp_path):
        """Test Stage 4 fails consensus and returns FAILED when previous stage failed."""
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
        res = run_review_stage(ctx)
        assert res.status == StageStatus.FAILED
        assert res.payload["consensus"]["approved"] is False

    def test_stage5_calibrate(self, tmp_path):
        """Test Stage 5 Post-Execution Calibration generating calibration report."""
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            dry_run=True,
        )

        res = run_calibrate_stage(ctx)
        assert res.status == StageStatus.SUCCESS
        assert ctx.calibrated_branch is not None
        assert ctx.calibrated_branch.endswith("/calibrated")
        assert not ctx.calibrated_branch.endswith("/_/calibrated")
        assert "calibrated_branch" in res.payload

    def test_stage5_calibrate_mocked(self, tmp_path):
        """Test Stage 5 invoking run_calibrate successfully."""
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            dry_run=False,
        )
        mock_report = MagicMock()
        mock_report.calibrated_branch = "I-100-intent/P-200-plan/E-300-exec/calibrated"
        mock_report.to_dict.return_value = {"calibrated_branch": mock_report.calibrated_branch, "accuracy_score": 0.95}

        with (
            patch("sandbox_executor.flow.stages.calibrate._checkout_execution_branch", return_value=None),
            patch("sandbox_executor.flow.stages.calibrate.run_calibrate", return_value=mock_report) as mock_run,
        ):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert res.payload["calibration_report"]["accuracy_score"] == 0.95
        assert mock_run.call_args.kwargs["log_fn"] == ctx.log
        assert ctx.calibrated_branch == "I-100-intent/P-200-plan/E-300-exec/calibrated"

    def test_stage5_calibrate_fallback_formats_report_with_lint_docs_prettier(self, tmp_path):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            dry_run=False,
        )

        with (
            patch("sandbox_executor.flow.stages.calibrate._checkout_execution_branch", return_value=None),
            patch(
                "sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=RuntimeError("calibration failed")
            ),
            patch("sandbox_executor.flow.stages.calibrate.format_calibration_report") as mock_format,
        ):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        mock_format.assert_called_once_with("plans/P-200-plan_calibration.md", str(tmp_path))

    def test_stage5_calibrate_fallback_appends_uncommitted_failure_act_cal(self, tmp_path):
        """A failed calibration run appends an ACT-CAL failure row that stays uncommitted, as documented."""
        import subprocess

        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

        git("init", "-q", "-b", "main")
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "Test")
        ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text("", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "initial")
        head_before = git("rev-parse", "HEAD").strip()
        # HEAD stays on main: Stage 5 checks the execution branch out itself before calibrating.
        git("branch", "I-100-intent/P-200-plan/E-300-exec/_")

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            plan_branch="I-100-intent/P-200-plan/_",
            dry_run=False,
            skip_push=True,
        )
        with (
            patch(
                "sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=RuntimeError("calibration failed")
            ),
            patch("sandbox_executor.flow.stages.calibrate.format_calibration_report"),
        ):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        cal_rows = [r for r in _read_rows(ledger) if _prefix(r) == "ACT-CAL"]
        assert len(cal_rows) == 1
        assert cal_rows[0]["outcome"] == "failure"
        assert "calibration failed" in cal_rows[0]["error_context"]
        assert cal_rows[0]["action"] == "Calibration failed for plan P-200-plan; fallback report written"
        assert cal_rows[0]["calibration_report_path"] == "plans/P-200-plan_calibration.md"

        # Nothing is committed on the fallback path: the row and report remain working-tree changes.
        assert git("rev-parse", "--abbrev-ref", "HEAD").strip() == "I-100-intent/P-200-plan/E-300-exec/_"
        assert git("rev-parse", "HEAD").strip() == head_before
        assert git("rev-parse", "main").strip() == head_before
        assert git("branch", "--list", "*calibrated").strip() == ""
        status = git("status", "--porcelain", "--untracked-files=all")
        assert "holon-knowledge/ledger/executions.jsonl" in status
        assert "plans/P-200-plan_calibration.md" in status

    @pytest.mark.parametrize("scenario", ["raises_after_checkout", "rerun_on_calibrated"])
    def test_stage5_calibrate_failure_on_calibrated_branch_commits_nothing(self, tmp_path, scenario):
        """A failed calibration leaves its rows uncommitted even when HEAD sits on the /calibrated branch."""
        import subprocess

        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

        execution_branch = "I-100-intent/P-200-plan/E-300-exec/_"
        calibrated_branch = "I-100-intent/P-200-plan/E-300-exec/calibrated"
        git("init", "-q", "-b", "main")
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "Test")
        ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text("", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "initial")
        git("checkout", "-q", "-b", execution_branch)
        if scenario == "rerun_on_calibrated":
            git("checkout", "-q", "-b", calibrated_branch)
        execution_tip = git("rev-parse", execution_branch).strip()

        def failing_run_calibrate(**_kwargs):
            if scenario == "raises_after_checkout":
                git("checkout", "-q", "-b", calibrated_branch)
                raise OSError("report write failed")
            raise RuntimeError("generate_calibration failed")

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch=execution_branch,
            plan_branch="I-100-intent/P-200-plan/_",
            dry_run=False,
            skip_push=True,
        )
        with (
            patch("sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=failing_run_calibrate),
            patch("sandbox_executor.flow.stages.calibrate.format_calibration_report"),
        ):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        cal_rows = [r for r in _read_rows(ledger) if _prefix(r) == "ACT-CAL"]
        assert len(cal_rows) == 1
        assert cal_rows[0]["outcome"] == "failure"

        # Neither branch gains a commit, so no ff/push can carry the failure rows into the PR.
        assert git("rev-parse", calibrated_branch).strip() == execution_tip
        assert git("rev-parse", execution_branch).strip() == execution_tip
        assert git("show", f"{calibrated_branch}:holon-knowledge/ledger/executions.jsonl") == ""
        status = git("status", "--porcelain", "--untracked-files=all")
        assert "holon-knowledge/ledger/executions.jsonl" in status
        assert "plans/P-200-plan_calibration.md" in status
        assert any("left uncommitted" in line and "calibration failed" in line for line in ctx.logs)

    def test_stage5_calibrate_fallback_records_original_error_when_prettier_fails(self, tmp_path):
        """A Prettier failure inside the fallback must not mask the original calibration error."""
        ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text("", encoding="utf-8")

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            plan_branch="I-100-intent/P-200-plan/_",
            dry_run=False,
            skip_push=True,
        )
        with (
            patch("sandbox_executor.flow.stages.calibrate._checkout_execution_branch", return_value=None),
            patch(
                "sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=RuntimeError("calibration failed")
            ),
            patch(
                "sandbox_executor.flow.stages.calibrate.format_calibration_report",
                side_effect=OSError("prettier missing"),
            ),
        ):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        cal_rows = [r for r in _read_rows(ledger) if _prefix(r) == "ACT-CAL"]
        assert len(cal_rows) == 1
        assert cal_rows[0]["outcome"] == "failure"
        assert cal_rows[0]["error_context"] == "Calibration run failed: calibration failed"

    def test_stage5_calibrate_fallback_redacts_credentials_in_act_cal(self, tmp_path):
        """The fallback ACT-CAL error_context must not carry credentials from the exception text."""
        ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text("", encoding="utf-8")
        sample_value = "ghp_" + "M" * 36

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            plan_branch="I-100-intent/P-200-plan/_",
            dry_run=False,
            skip_push=True,
        )
        with (
            patch("sandbox_executor.flow.stages.calibrate._checkout_execution_branch", return_value=None),
            patch(
                "sandbox_executor.flow.stages.calibrate.run_calibrate",
                side_effect=RuntimeError(f"fetch failed {sample_value}"),
            ),
            patch("sandbox_executor.flow.stages.calibrate.format_calibration_report"),
        ):
            run_calibrate_stage(ctx)

        cal_rows = [r for r in _read_rows(ledger) if _prefix(r) == "ACT-CAL"]
        assert len(cal_rows) == 1
        assert cal_rows[0]["error_context"] == "Calibration run failed: fetch failed [REDACTED_SECRET]"
        assert sample_value not in ledger.read_text(encoding="utf-8")

    def test_stage5_calibrate_fallback_bounds_act_cal_error_context(self, tmp_path):
        """A long fallback error_context is redacted before it is truncated (C10-N2)."""
        from sandbox_executor.flow import POSTING_ERROR_CONTEXT_MAX_CHARS

        ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text("", encoding="utf-8")
        sample_value = "ghp_" + "Q" * 36
        prefix_len = len("Calibration run failed: ")
        # The sample value starts 20 characters before the cap, so it straddles the boundary.
        padding = "x" * (POSTING_ERROR_CONTEXT_MAX_CHARS - prefix_len - 21)
        hook_output = f"{padding} {sample_value} trailing hook output"

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            plan_branch="I-100-intent/P-200-plan/_",
            dry_run=False,
            skip_push=True,
        )
        with (
            patch("sandbox_executor.flow.stages.calibrate._checkout_execution_branch", return_value=None),
            patch("sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=RuntimeError(hook_output)),
            patch("sandbox_executor.flow.stages.calibrate.format_calibration_report"),
        ):
            run_calibrate_stage(ctx)

        cal_rows = [r for r in _read_rows(ledger) if _prefix(r) == "ACT-CAL"]
        assert len(cal_rows) == 1
        error_context = cal_rows[0]["error_context"]
        assert len(error_context) <= POSTING_ERROR_CONTEXT_MAX_CHARS
        assert error_context.startswith("Calibration run failed: xxx")
        assert "ghp_" not in error_context
        assert "ghp_" not in ledger.read_text(encoding="utf-8")

    def test_stage5_fallback_prettier_warning_redacts_credentials(self, tmp_path):
        """The fallback Prettier failure warning must not echo credentials from the exception text."""
        (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True)
        sample_value = "ghp_" + "P" * 36
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            plan_branch="I-100-intent/P-200-plan/_",
            dry_run=True,
            skip_push=True,
        )
        with (
            patch(
                "sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=RuntimeError("calibration failed")
            ),
            patch(
                "sandbox_executor.flow.stages.calibrate.format_calibration_report",
                side_effect=OSError(f"npx auth {sample_value}"),
            ),
        ):
            run_calibrate_stage(ctx)

        assert any("Prettier formatting failed" in line and "[REDACTED_SECRET]" in line for line in ctx.logs)
        assert all(sample_value not in line for line in ctx.logs)

    @staticmethod
    def _stage5_push_context(tmp_path):
        return FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            plan_branch="I-100-intent/P-200-plan/_",
            dry_run=False,
            skip_push=False,
        )

    def test_stage5_prettier_check_failure_fails_stage_and_never_pushes(self, tmp_path):
        """A report that fails the repository Prettier check fails Stage 5 and is never pushed."""
        from sandbox_executor.calibration import CalibrationFormatError

        ctx = self._stage5_push_context(tmp_path)
        error = CalibrationFormatError(
            "Calibration report plans/P-200-plan_calibration.md failed the repository Prettier check "
            "(check exited 1); reproduce with: npx prettier --check plans/P-200-plan_calibration.md"
        )
        with (
            patch("sandbox_executor.flow.stages.calibrate._checkout_execution_branch", return_value=None),
            patch("sandbox_executor.flow.stages.calibrate.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=error),
            patch("sandbox_executor.flow.stages.calibrate.record_calibration_action") as mock_record,
            patch_lookup_sites(
                "sandbox_executor.flow.git_refs.run_git",
                "sandbox_executor.flow.github.pr.run_git",
                "sandbox_executor.flow.stage_conflicts.run_git",
                "sandbox_executor.flow.stages.calibrate.run_git",
                "sandbox_executor.flow.stages.review.run_git",
            ) as mock_git,
        ):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "Prettier check" in res.error
        assert "nothing was committed or pushed" in res.error
        assert "plans/P-200-plan_calibration.md" in res.error
        assert ctx.stage_results["calibrate"] is res
        git_verbs = [call.args[0][0] for call in mock_git.call_args_list]
        assert not {"push", "merge", "commit", "add"} & set(git_verbs)
        mock_record.assert_not_called()
        assert not (tmp_path / "plans").exists()

    def test_stage5_calibration_success_still_pushes(self, tmp_path):
        """Regression guard: a report that passes the check is still pushed and fast-forwarded."""
        ctx = self._stage5_push_context(tmp_path)
        mock_report = MagicMock()
        mock_report.calibrated_branch = "I-100-intent/P-200-plan/E-300-exec/calibrated"
        mock_report.to_dict.return_value = {"calibrated_branch": mock_report.calibrated_branch}
        with (
            patch("sandbox_executor.flow.stages.calibrate._checkout_execution_branch", return_value=None),
            patch("sandbox_executor.flow.stages.calibrate.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow.stages.calibrate.run_calibrate", return_value=mock_report),
            patch("sandbox_executor.flow.stages.calibrate.run_git") as mock_git,
        ):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        git_calls = [call.args[0] for call in mock_git.call_args_list]
        assert ["push", "-u", "origin", mock_report.calibrated_branch] in git_calls
        assert ["merge", "--ff-only", mock_report.calibrated_branch] in git_calls

    def test_warn_ledger_failure_redacts_credentials(self, tmp_path, caplog):
        """Non-fatal ledger failure warnings redact the exception text in both logs."""
        from sandbox_executor.flow import _warn_ledger_failure

        caplog.set_level("WARNING", logger="sandbox_executor.flow")
        sample_value = "ghp_" + "W" * 36
        ctx = FlowContext(repo_dir=str(tmp_path))
        _warn_ledger_failure(ctx, "Stage 4 review action event", "ledger.jsonl", OSError(f"denied {sample_value}"))

        assert len(ctx.logs) == 1
        assert ctx.logs[0].endswith(
            "Warning: Ledger append failed (Stage 4 review action event) for ledger.jsonl: "
            "OSError: denied [REDACTED_SECRET]"
        )
        assert any("[REDACTED_SECRET]" in rec.getMessage() for rec in caplog.records)
        assert all(sample_value not in rec.getMessage() for rec in caplog.records)

    @pytest.mark.parametrize("failure_mode", ["stderr", "raises"])
    def test_stage5_reconciliation_commit_warning_redacts_credentials(self, tmp_path, caplog, failure_mode):
        """A failed Stage 5 reconciliation commit warns without echoing credentials."""
        from sandbox_executor.flow import _commit_stage5_reconciliation

        caplog.set_level("WARNING", logger="sandbox_executor.flow")
        sample_value = "ghp_" + "R" * 36
        branch = "I-1/P-2/E-3/calibrated"
        ctx = FlowContext(repo_dir=str(tmp_path))
        ctx.calibrated_branch = branch

        def fake_run_git(args, **_kwargs):
            if args[0] == "rev-parse":
                return MagicMock(returncode=0, stdout=f"{branch}\n", stderr="")
            if args[0] == "commit":
                if failure_mode == "raises":
                    raise RuntimeError(f"hook failed {sample_value}")
                return MagicMock(returncode=1, stdout="", stderr=f"remote rejected {sample_value}")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("sandbox_executor.flow.stages.calibrate.run_git", side_effect=fake_run_git):
            _commit_stage5_reconciliation(ctx, str(tmp_path))

        assert any("[REDACTED_SECRET]" in line for line in ctx.logs)
        assert all(sample_value not in line for line in ctx.logs)
        assert all(sample_value not in rec.getMessage() for rec in caplog.records)

    @pytest.mark.parametrize("stage", ["intent", "plan", "execute"])
    def test_stage_fails_fast_when_role_agent_ledger_append_fails(self, flow_repos, stage):
        """A role container whose ledger append fails exits non-zero, and the stage fails instead of proceeding.

        Stages 1-3 no longer append intents/plans/executions rows on the host: the dispatched container does, under
        LedgerLock, and a failed append surfaces as its non-zero exit code.
        """
        host, _origin = flow_repos
        overrides: dict[str, Any] = {"intent_branch": "I-1-x/_", "plan_branch": "I-1-x/P-1-claude-agent-m/_"}
        ctx = _dispatch_context(host, MagicMock(return_value=1), **overrides)
        run_stage = {"intent": run_intent_stage, "plan": run_plan_stage, "execute": run_execute_stage}[stage]

        res = run_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "exited with code 1" in (res.error or "")

    def test_engine_halts_when_stage1_role_agent_fails(self, flow_repos):
        """The engine stops at a failed Stage 1 dispatch and never reaches the plan stage."""
        host, _origin = flow_repos
        dispatcher = MagicMock(return_value=1)
        ctx = _dispatch_context(host, dispatcher)

        results = PipelineEngine(ctx).run()

        assert results["intent"].status == StageStatus.FAILED
        assert "plan" not in results
        assert [c.args[0] for c in dispatcher.call_args_list] == ["intent-creator"]


class TestFlowCLI:
    """Test CLI subcommands, flags, and dispatching."""

    def test_cli_flow_help(self):
        with pytest.raises(SystemExit) as exc_info:
            cli_main_args = ["holon", "flow", "--help"]
            with patch("sys.argv", cli_main_args):
                cli_main()
        assert exc_info.value.code == 0

    def test_cli_flow_dry_run_dispatch(self, tmp_path, monkeypatch):
        intent_file = tmp_path / "intent.json"
        intent_file.write_text(json.dumps({"slug": "cli-test", "goal": "CLI flow test"}))
        monkeypatch.setenv("HOLON_AGENT", "antigravity-agent")
        monkeypatch.setenv("HOLON_MODEL", "gemini-3.8-flash-medium")

        with patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline, patch("sys.exit") as mock_exit:
            mock_pipeline.return_value = {"status": "ok"}
            with patch("sys.argv", ["holon", "flow", str(intent_file), "--dry-run", "--json"]):
                cli_main()
            mock_pipeline.assert_called_once()
            called_ctx = mock_pipeline.call_args[1]["context"]
            assert called_ctx.dry_run is True
            assert called_ctx.intent_data["slug"] == "cli-test"
            assert called_ctx.agent == "antigravity-agent"
            assert called_ctx.model == "gemini-3.8-flash-medium"
            mock_exit.assert_called_with(0)

    def test_cli_flow_missing_intent_file(self):
        with (
            pytest.raises(SystemExit) as exc_info,
            patch("sys.argv", ["holon", "flow", "/non/existent/intent.json"]),
        ):
            cli_main()
        assert exc_info.value.code == 1

    def test_cli_flow_missing_checkpoint_file(self):
        with (
            pytest.raises(SystemExit) as exc_info,
            patch("sys.argv", ["holon", "flow", "--checkpoint", "/non/existent/checkpoint.json"]),
        ):
            cli_main()
        assert exc_info.value.code == 1


class TestStageBudgets:
    """Stage budgets: stage ceilings, elapsed-versus-budget summary lines and the execute budget hand-down."""

    def test_summary_line_format(self):
        res = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS, elapsed_seconds=246, budget_seconds=2100)
        assert res.summary_line() == "plan: ok (4.1 min / 35 min)"
        assert StageResult(stage=FlowStage.EXECUTE, status=StageStatus.FAILED).summary_line() == "execute: failed"
        restored = StageResult.from_dict(res.to_dict())
        assert (restored.elapsed_seconds, restored.budget_seconds) == (246, 2100)

    def test_every_stage_reports_elapsed_against_budget(self, tmp_path):
        (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True)
        ctx = FlowContext(
            repo_dir=str(tmp_path), intent_data={"slug": "budget", "goal": "Budgets"}, auto_calibrate=True, dry_run=True
        )
        results = PipelineEngine(ctx).run()
        expected_budgets = {"intent": 600, "plan": 2100, "execute": 2700, "review": 7200, "calibrate": 600}
        for stage, budget in expected_budgets.items():
            assert results[stage].budget_seconds == budget, stage
            assert results[stage].elapsed_seconds is not None, stage
        logs = "\n".join(ctx.logs)
        assert re.search(r"intent: ok \(\d+\.\d min / 10 min\)", logs)
        assert re.search(r"plan: ok \(\d+\.\d min / 35 min\)", logs)
        assert re.search(r"execute: ok \(\d+\.\d min / 45 min\)", logs)
        assert re.search(r"review: halted \(\d+\.\d min / 120 min\)", logs)

    def test_stage_breach_under_fake_clock_fails_the_stage(self, tmp_path):
        (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True)
        ctx = FlowContext(repo_dir=str(tmp_path), intent_data={"slug": "slow", "goal": "Slow"}, dry_run=True)
        results = PipelineEngine(ctx, clock=SteppingClock(step=3000)).run()
        assert results["intent"].status == StageStatus.FAILED
        assert "exceeded its stage budget" in results["intent"].error
        assert results["intent"].payload["budget_breach"]["budget_name"] == "stage"
        assert "plan" not in results
        assert not (tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl").exists()

    def test_sub_minute_stage_ceiling_is_not_reported_as_zero_minutes(self, tmp_path):
        # C7-N2: budget messages use the shared minutes formatter, so a 20 s ceiling reads "0.3 min", not "0 min".
        from sandbox_executor.budgets import Budgets

        (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True)
        ctx = FlowContext(repo_dir=str(tmp_path), intent_data={"slug": "fast", "goal": "Fast"}, dry_run=True)
        engine = PipelineEngine(ctx, budgets=Budgets(stage_override_seconds=20), clock=SteppingClock(step=30))
        results = engine.run()
        assert results["intent"].status == StageStatus.FAILED
        assert results["intent"].error.endswith("against 0.3 min.")
        assert "against 0 min" not in results["intent"].error

    def test_review_overrun_after_consensus_keeps_approval_and_runs_calibration(self, tmp_path):
        # C3-I3: two review iterations each within budget plus PR/posting overhead exceed 2 x 120 min in total; the
        # approved review is not downgraded and calibration still runs.
        (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True)
        ctx = FlowContext(
            repo_dir=str(tmp_path), intent_data={"slug": "rev", "goal": "Rev"}, auto_calibrate=True, dry_run=True
        )
        review = StageResult(
            stage=FlowStage.REVIEW,
            status=StageStatus.HALTED_FOR_HUMAN,
            payload={"consensus": {"approved": True, "iterations": 2}},
        )
        calibrate = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS)

        def fake_review(context):
            context.stage_results["review"] = review
            return review

        with (
            patch("sandbox_executor.flow.pipeline.run_review_stage", side_effect=fake_review),
            patch("sandbox_executor.flow.pipeline.run_calibrate_stage", return_value=calibrate) as mock_cal,
        ):
            engine = PipelineEngine(ctx, from_stage="review", clock=SteppingClock(step=240.5 * 60))
            engine.run()
        assert review.status == StageStatus.HALTED_FOR_HUMAN
        assert review.budget_seconds == 2 * 7200
        assert review.payload["budget_overrun"]["budget_name"] == "stage"
        assert "budget_breach" not in review.payload
        mock_cal.assert_called_once()

    def test_calibration_overrun_keeps_success(self, tmp_path):
        ctx = FlowContext(repo_dir=str(tmp_path), intent_data={"slug": "cal", "goal": "Cal"}, dry_run=True)
        calibrate = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS)
        with patch("sandbox_executor.flow.pipeline.run_calibrate_stage", return_value=calibrate):
            result = PipelineEngine(ctx, clock=SteppingClock(step=3000)).timed_execute_stage(FlowStage.CALIBRATE)
        assert result.status == StageStatus.SUCCESS
        assert result.payload["budget_overrun"]["budget_seconds"] == 600

    def test_decomposed_execute_overrun_keeps_success(self, tmp_path):
        # A decomposed Stage 3 spans whole child pipelines, so its overrun is recorded, never a stage failure.
        ctx = FlowContext(repo_dir=str(tmp_path), intent_data={"slug": "dec", "goal": "Dec"}, dry_run=True)
        decomposed = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"decomposed": True})
        with patch("sandbox_executor.flow.pipeline.run_execute_stage", return_value=decomposed):
            result = PipelineEngine(ctx, clock=SteppingClock(step=3000)).timed_execute_stage(FlowStage.EXECUTE)
        assert result.status == StageStatus.SUCCESS
        assert result.payload["budget_overrun"] == {
            "budget_name": "stage",
            "budget_seconds": 2700,
            "elapsed_seconds": 3000.0,
        }
        assert "budget_breach" not in result.payload

    def test_undecomposed_execute_overrun_fails_the_stage(self, tmp_path):
        ctx = FlowContext(repo_dir=str(tmp_path), intent_data={"slug": "plain", "goal": "Plain"}, dry_run=True)
        plain = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={})
        with patch("sandbox_executor.flow.pipeline.run_execute_stage", return_value=plain):
            result = PipelineEngine(ctx, clock=SteppingClock(step=3000)).timed_execute_stage(FlowStage.EXECUTE)
        assert result.status == StageStatus.FAILED
        assert "exceeded its stage budget" in result.error
        assert result.payload["budget_breach"]["budget_name"] == "stage"

    def test_timed_stage_is_the_active_host_timer_except_review(self, tmp_path):
        # C6-I1: role containers dispatched during a stage read this timer (cli.run_docker_container hand-down).
        from sandbox_executor.budgets import current_stage_timer, stage_budget_handdown_env

        ctx = FlowContext(repo_dir=str(tmp_path), intent_data={"slug": "h", "goal": "H"}, dry_run=True)
        seen = {}

        def fake_stage(name):
            def run(context):
                seen[name] = (current_stage_timer(), stage_budget_handdown_env())
                return StageResult(stage=FlowStage(name), status=StageStatus.SUCCESS)

            return run

        engine = PipelineEngine(ctx, clock=lambda: 0.0)
        with (
            patch("sandbox_executor.flow.pipeline.run_plan_stage", side_effect=fake_stage("plan")),
            patch("sandbox_executor.flow.pipeline.run_review_stage", side_effect=fake_stage("review")),
        ):
            engine.timed_execute_stage(FlowStage.PLAN)
            engine.timed_execute_stage(FlowStage.REVIEW)
        plan_timer, plan_env = seen["plan"]
        assert plan_timer is not None and plan_timer.budget_seconds == 2100
        assert plan_env == {"HOLON_STAGE_BUDGET_SECONDS": "2040"}
        assert seen["review"] == (None, {})
        assert current_stage_timer() is None

    def test_review_stage_budget_honours_the_exact_stage_override(self, tmp_path):
        ctx = FlowContext(repo_dir=str(tmp_path), intent_data={"slug": "o", "goal": "O"}, dry_run=True)
        with patch.dict(os.environ, {"HOLON_STAGE_BUDGET_SECONDS": "120"}):
            engine = PipelineEngine(ctx)
        review = StageResult(
            stage=FlowStage.REVIEW, status=StageStatus.SUCCESS, payload={"consensus": {"iterations": 3}}
        )
        assert engine.stage_budget_seconds(FlowStage.REVIEW, review) == 360

    def _dispatch_timed_execute(self, tmp_path, env, exit_code=7):
        """Run timed Stage 3 through the real cli.dispatch_role_agent and capture the executor container's env.

        With ``exit_code`` 0 the dispatch succeeds and the post-dispatch path runs against a discovered execution branch
        that changes one path and records a ``success`` summary row.
        """
        from sandbox_executor.budgets import stage_budget_handdown_env

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "b", "goal": "B"},
            plan_branch="I-1-x/P-2-y/_",
            agent="claude-agent",
            model="claude-opus-5-5",
            dry_run=False,
        )
        seen = []

        def fake_container(role, image_name, container_args, **kwargs):
            # run_docker_container adds exactly this to the forwarded env (cli test covers the docker argv).
            seen.append((role, image_name, list(container_args), stage_budget_handdown_env()))
            return exit_code

        exec_branch = "I-1-x/P-2-y/E-3-claude-agent-claude-opus-5-5/_"
        with (
            patch.dict(os.environ, env),
            patch("sandbox_executor.flow.stages.execute.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow.stages.execute._snapshot_child_branches", return_value=set()),
            patch("sandbox_executor.cli.run_docker_container", side_effect=fake_container),
            patch("sandbox_executor.flow.stages.execute._fetch_branch_prefix_error", return_value=None),
            patch("sandbox_executor.flow.stages.execute._discover_child_branch", return_value=(exec_branch, None)),
            patch("sandbox_executor.flow.stages.execute._track_local_branch"),
            patch_lookup_sites(
                "sandbox_executor.flow.stages.execute._read_ledger_rows",
                "sandbox_executor.flow.stages.plan_decision._read_ledger_rows",
                return_value=[
                    {"entry_type": "summary", "execution_id": "E-3-claude-agent-claude-opus-5-5", "status": "success"}
                ],
            ),
            patch_lookup_sites(
                "sandbox_executor.flow.stages.execute._resolve_branch_ref",
                "sandbox_executor.flow.stages.plan_decision._resolve_branch_ref",
                return_value="refs/remotes/origin/I-1-x/P-2-y/_",
            ),
            patch("sandbox_executor.flow.stages.execute.changed_paths", return_value=["src/app.py"]),
        ):
            now = [0.0]
            result = PipelineEngine(ctx, clock=lambda: now[0]).timed_execute_stage(FlowStage.EXECUTE)
        return result, seen

    def test_execute_budget_reaches_the_dispatched_executor_container(self, tmp_path):
        # After #95 Stage 3 runs no host-side verification: the executor container measures its own tests, so the
        # execute stage budget must reach that container through dispatch_role_agent -> run_docker_container.
        result, seen = self._dispatch_timed_execute(tmp_path, {"HOLON_STAGE_BUDGET_EXECUTE_MINUTES": "20"})
        assert [(role, image) for role, image, _, _ in seen] == [("executor", "holon/agent-claude")]
        assert seen[0][2] == ["I-1-x/P-2-y/_", "claude-agent", "claude-opus-5-5"]
        # 1200 s stage budget minus CONTAINER_OVERHEAD_AND_HOST_CHECKS_MARGIN_SECONDS (at most a tenth of the budget).
        assert seen[0][3] == {"HOLON_STAGE_BUDGET_SECONDS": "1140"}
        assert result.status == StageStatus.FAILED
        assert result.error == "Execute stage failed: executor agent exited with code 7."
        assert result.budget_seconds == 1200

    def test_execute_stage_runs_no_host_side_verification(self, tmp_path):
        with patch("subprocess.run") as mock_sub:
            result, seen = self._dispatch_timed_execute(tmp_path, {}, exit_code=0)
        assert [role for role, _, _, _ in seen] == ["executor"]
        assert result.status == StageStatus.SUCCESS, result.error
        assert result.payload["changed_files"] == 1
        commands = [" ".join(map(str, call.args[0])) for call in mock_sub.call_args_list if call.args]
        assert not [cmd for cmd in commands if "pytest" in cmd or "task test" in cmd]

    def test_review_iteration_ceiling_stops_the_loop(self, tmp_path):
        ctx = FlowContext(repo_dir=str(tmp_path), execution_branch="I-1-x/P-2-y/E-3-z/_", dry_run=True)
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"test_pass_rate": 0.0, "exit_code": 1}
        )
        with patch_lookup_sites(
            "sandbox_executor.flow.git_refs.run_git", "sandbox_executor.flow.github.pr.run_git"
        ) as mock_git:
            mock_git.return_value = MagicMock(returncode=0, stdout="")
            res = run_review_loop(ctx, target="I-1-x/P-2-y/E-3-z/_", max_iterations=5, clock=SteppingClock(8000))
        assert res["total_iterations"] == 1
        assert res["budget_breach"]["budget_name"] == "review_iteration"
        assert res["iteration_summaries"] == ["iteration 1 review: failed (133.3 min / 120 min)"]


class TestSkipPushRejectedOnEveryNonDryRun:
    """C10-N2: --skip-push outside --dry-run is rejected for runs and resumes from any stage."""

    @pytest.mark.parametrize("from_stage", [None, "plan", "execute", "review", "calibrate"])
    def test_engine_rejects_skip_push_before_any_stage_or_checkpoint(self, tmp_path, from_stage):
        checkpoint = tmp_path / "checkpoint.json"
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-1-x/P-2-a-b/E-3-a-b/_",
            dry_run=False,
            skip_push=True,
        )
        with (
            patch("sandbox_executor.flow.pipeline.run_review_stage") as review,
            patch("sandbox_executor.flow.pipeline.run_calibrate_stage") as calibrate,
            patch_lookup_sites(
                "sandbox_executor.flow.git_refs.run_git",
                "sandbox_executor.flow.github.pr.run_git",
                "sandbox_executor.flow.stage_conflicts.run_git",
                "sandbox_executor.flow.stages.calibrate.run_git",
                "sandbox_executor.flow.stages.review.run_git",
            ) as git,
        ):
            results = PipelineEngine(ctx, from_stage=from_stage, checkpoint_path=str(checkpoint)).run()

        first = from_stage or "intent"
        assert list(results) == [first]
        assert results[first].status == StageStatus.FAILED
        assert results[first].error == SKIP_PUSH_DISPATCH_ERROR
        review.assert_not_called()
        calibrate.assert_not_called()
        git.assert_not_called()
        assert not checkpoint.exists()

    def test_engine_accepts_skip_push_on_dry_run_resume(self, tmp_path):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-1-x/P-2-a-b/E-3-a-b/_",
            dry_run=True,
            skip_push=True,
        )
        results = PipelineEngine(ctx, from_stage="calibrate").run()

        assert results["calibrate"].status == StageStatus.SUCCESS


class TestFlowWorktreeHermetic:
    """Hermetic integration tests for git worktree provisioning, pruning, and reuse."""

    @pytest.fixture
    def hermetic_repos(self, tmp_path: Path) -> dict[str, Path]:
        """Initialise real origin, bare, and worktree git repositories."""
        root_dir = tmp_path / "hermetic_env"
        root_dir.mkdir(parents=True, exist_ok=True)

        src_dir = root_dir / "src_repo"
        src_dir.mkdir()
        subprocess.run(["git", "init"], cwd=str(src_dir), check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Hermetic Test"],
            cwd=str(src_dir),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@hermetic.local"],
            cwd=str(src_dir),
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "checkout", "-B", "main"], cwd=str(src_dir), check=True, capture_output=True)

        readme = src_dir / "README.md"
        readme.write_text("# Hermetic Repository\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=str(src_dir), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit on main"],
            cwd=str(src_dir),
            check=True,
            capture_output=True,
        )

        bare_dir = root_dir / "bare.git"
        subprocess.run(["git", "clone", "--bare", str(src_dir), str(bare_dir)], check=True, capture_output=True)

        return {"root": root_dir, "src": src_dir, "bare": bare_dir}

    def test_is_bare_and_is_working_tree(self, hermetic_repos: dict[str, Path], tmp_path: Path) -> None:
        """Verify is_bare_repository and is_working_tree on bare, worktree, and absent paths."""
        assert is_bare_repository(str(hermetic_repos["bare"])) is True
        assert is_working_tree(str(hermetic_repos["bare"])) is False

        assert is_bare_repository(str(hermetic_repos["src"])) is False
        assert is_working_tree(str(hermetic_repos["src"])) is True

        absent_path = str(tmp_path / "does_not_exist")
        assert is_bare_repository(absent_path) is False
        assert is_working_tree(absent_path) is False

    def test_derive_worktree_layout(self, hermetic_repos: dict[str, Path]) -> None:
        """Verify canonical worktree path and branch derivations."""
        wt_path, branch = derive_worktree_layout(str(hermetic_repos["bare"]), "my-task")
        assert branch == "flow-my-task"
        assert os.path.basename(wt_path) == "flow-my-task"
        assert os.path.dirname(wt_path) == str(hermetic_repos["root"].resolve())

    def test_auto_create_worktree_from_bare_repository(self, hermetic_repos: dict[str, Path]) -> None:
        """Verify automatic worktree creation and file checkout when invoked on bare repository."""
        bare_path = str(hermetic_repos["bare"])
        wt_dir = ensure_worktree(bare_path, slug="test-auto-provision", target_branch="main")

        assert os.path.isdir(wt_dir)
        assert Path(wt_dir).name == "flow-test-auto-provision"
        assert is_working_tree(wt_dir) is True
        assert (Path(wt_dir) / "README.md").read_text(encoding="utf-8") == "# Hermetic Repository\n"

        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=wt_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        assert proc.stdout.strip() == "flow-test-auto-provision"

    def test_self_heal_stale_worktree_records_after_directory_deletion(self, hermetic_repos: dict[str, Path]) -> None:
        """Verify self-healing via prune_worktrees after deleting a worktree folder without git worktree remove."""
        bare_path = str(hermetic_repos["bare"])
        wt_dir = ensure_worktree(bare_path, slug="test-stale-heal", target_branch="main")
        assert os.path.isdir(wt_dir)

        shutil.rmtree(wt_dir)
        assert not os.path.exists(wt_dir)

        recreated_dir = ensure_worktree(bare_path, slug="test-stale-heal", target_branch="main")
        assert os.path.isdir(recreated_dir)
        assert is_working_tree(recreated_dir) is True
        assert (Path(recreated_dir) / "README.md").read_text(encoding="utf-8") == "# Hermetic Repository\n"

    def test_idempotent_attach_to_existing_branch(self, hermetic_repos: dict[str, Path]) -> None:
        """Verify worktree attaches to pre-existing branch without collision errors."""
        bare_path = str(hermetic_repos["bare"])
        subprocess.run(
            ["git", "branch", "flow-test-reuse", "main"],
            cwd=bare_path,
            check=True,
            capture_output=True,
        )

        wt_dir = ensure_worktree(bare_path, slug="test-reuse", target_branch="main")
        assert os.path.isdir(wt_dir)
        assert is_working_tree(wt_dir) is True

        wt_dir_again = ensure_worktree(bare_path, slug="test-reuse", target_branch="main")
        assert wt_dir_again == wt_dir

    def test_ensure_worktree_validates_empty_slug(self, hermetic_repos: dict[str, Path]) -> None:
        """Verify ensure_worktree rejects blank slug with ValueError."""
        with pytest.raises(ValueError, match="Intent slug must be non-empty"):
            ensure_worktree(str(hermetic_repos["bare"]), slug="   ")

    def test_prune_worktrees_directly(self, hermetic_repos: dict[str, Path]) -> None:
        """Verify prune_worktrees executes cleanly on bare and missing paths."""
        prune_worktrees(str(hermetic_repos["bare"]))
        prune_worktrees("/non/existent/path")

    def test_flow_cli_auto_creates_worktree_from_bare_repo(
        self, hermetic_repos: dict[str, Path], tmp_path: Path
    ) -> None:
        """Verify holon flow CLI detects bare repo and provisions worktree in dry-run mode."""
        intent_file = tmp_path / "test_intent.json"
        intent_file.write_text(
            json.dumps({"slug": "auto-bare-cli", "target_branch": "main"}),
            encoding="utf-8",
        )

        with (
            patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline,
            patch("sys.exit") as mock_exit,
            patch(
                "sys.argv",
                [
                    "holon",
                    "flow",
                    str(intent_file),
                    "--agent",
                    "antigravity-agent",
                    "--model",
                    "gemini-3.8-flash-medium",
                    "--dry-run",
                    "--json",
                    "--target-worktree",
                    str(hermetic_repos["bare"]),
                ],
            ),
        ):
            mock_pipeline.return_value = {"status": "ok"}
            cli_main()

        mock_pipeline.assert_called_once()
        called_ctx = mock_pipeline.call_args[1]["context"]
        assert called_ctx.dry_run is True
        assert called_ctx.intent_data["slug"] == "auto-bare-cli"
        assert is_working_tree(called_ctx.target_worktree) is True
        assert Path(called_ctx.target_worktree).name == "flow-auto-bare-cli"
        mock_exit.assert_called_with(0)
