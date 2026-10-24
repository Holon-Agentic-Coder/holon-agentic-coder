"""Unit and integration tests for sandbox_executor.flow."""

import argparse
import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.cli import main as cli_main
from sandbox_executor.entrypoint.executor import _sanitize_string
from sandbox_executor.entrypoint.planner import _sanitize_model_name
from sandbox_executor.flow import (
    CI_REGISTRATION_TIMEOUT_ENV_VAR,
    CI_REGISTRATION_TIMEOUT_SECONDS,
    REVIEW_GIT_ENV,
    SKIP_PUSH_DISPATCH_ERROR,
    TESTS_NOT_MEASURED_ERROR,
    ConflictResolutionError,
    FlowCheckpoint,
    FlowContext,
    FlowStage,
    PipelineEngine,
    StageResult,
    StageStatus,
    build_pr_review_consensus_report,
    check_and_resolve_stage_conflicts,
    check_pr_mergeability,
    create_or_update_pr,
    extract_valid_ledger_rows,
    format_review_git_trailers,
    generate_semantic_pr_metadata,
    load_checkpoint,
    migrate_checkpoint_data,
    post_pr_review_consensus,
    probe_ci_checks,
    reconcile_jsonl_ledger_conflict,
    reconcile_ledger_content,
    reconcile_ledger_file,
    reconcile_ledger_rows,
    reconcile_ledgers,
    resolve_ci_registration_timeout,
    run_calibrate_stage,
    run_execute_stage,
    run_intent_stage,
    run_plan_stage,
    run_review_loop,
    run_review_loop_cli,
    run_review_stage,
    save_checkpoint,
)
from sandbox_executor.ledger import parse_commit_trailers


class TestFlowDataModels:
    """Test data structures, enums, serialisation, and atomic checkpoint persistence."""

    def test_flow_stage_enum(self):
        assert FlowStage.INTENT.value == "intent"
        assert FlowStage.PLAN.value == "plan"
        assert FlowStage.EXECUTE.value == "execute"
        assert FlowStage.REVIEW.value == "review"
        assert FlowStage.CALIBRATE.value == "calibrate"
        assert FlowStage.COMPLETED.value == "completed"

        assert FlowStage.from_str("intent") == FlowStage.INTENT
        assert FlowStage.from_str("PLAN") == FlowStage.PLAN
        assert FlowStage.from_str(FlowStage.EXECUTE) == FlowStage.EXECUTE

        with pytest.raises(ValueError, match="Unknown FlowStage"):
            FlowStage.from_str("invalid_stage")

    def test_stage_status_enum(self):
        assert StageStatus.PENDING.value == "pending"
        assert StageStatus.RUNNING.value == "running"
        assert StageStatus.SUCCESS.value == "success"
        assert StageStatus.FAILED.value == "failed"
        assert StageStatus.HALTED_FOR_HUMAN.value == "halted_for_human"
        assert StageStatus.SKIPPED.value == "skipped"

        assert StageStatus.from_str("pending") == StageStatus.PENDING
        assert StageStatus.from_str("HALTED_FOR_HUMAN") == StageStatus.HALTED_FOR_HUMAN
        with pytest.raises(ValueError, match="Unknown StageStatus"):
            StageStatus.from_str("unknown_status")

    def test_stage_result_serialization(self):
        res = StageResult(
            stage=FlowStage.INTENT,
            status=StageStatus.SUCCESS,
            start_time="2026-09-26T00:00:00Z",
            end_time="2026-09-26T00:00:05Z",
            payload={"branch": "I-123-test/_"},
            error=None,
        )
        d = res.to_dict()
        assert d["stage"] == "intent"
        assert d["status"] == "success"
        assert d["payload"]["branch"] == "I-123-test/_"

        reconstructed = StageResult.from_dict(d)
        assert reconstructed.stage == FlowStage.INTENT
        assert reconstructed.status == StageStatus.SUCCESS
        assert reconstructed.payload == {"branch": "I-123-test/_"}

    def test_flow_context_serialization_and_logging(self):
        ctx = FlowContext(
            repo_dir="/tmp/repo",
            intent_data={"slug": "test-slug", "goal": "Write tests"},
            agent="antigravity-agent",
            model="gemini-3.8-flash-medium",
            dry_run=True,
        )
        ctx.log("Sensitive message token=secret1234567890")
        assert len(ctx.logs) == 1
        assert "secret1234567890" not in ctx.logs[0]
        assert "*******" in ctx.logs[0]

        ctx.stage_results["intent"] = StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)
        d = ctx.to_dict()
        assert d["agent"] == "antigravity-agent"
        assert d["stage_results"]["intent"]["status"] == "success"

        reconstructed = FlowContext.from_dict(d)
        assert reconstructed.agent == "antigravity-agent"
        assert reconstructed.dry_run is True
        assert reconstructed.stage_results["intent"].status == StageStatus.SUCCESS
        assert len(reconstructed.logs) == 1

    def test_flow_context_new_flags_serialization(self):
        ctx = FlowContext(
            skip_review=True,
            skip_calibrate=True,
            max_review_iterations=5,
            mitm_web=True,
            token_reduce=True,
        )
        d = ctx.to_dict()
        assert d["skip_review"] is True
        assert d["skip_calibrate"] is True
        assert d["max_review_iterations"] == 5
        assert d["mitm_web"] is True
        assert d["token_reduce"] is True

        reconstructed = FlowContext.from_dict(d)
        assert reconstructed.skip_review is True
        assert reconstructed.skip_calibrate is True
        assert reconstructed.max_review_iterations == 5
        assert reconstructed.mitm_web is True
        assert reconstructed.token_reduce is True

    def test_legacy_checkpoint_loading_defaults(self):
        legacy_data = {
            "repo_dir": ".",
            "agent": "antigravity-agent",
            "model": "gemini-3.8-flash-medium",
        }
        ctx = FlowContext.from_dict(legacy_data)
        assert ctx.skip_review is False
        assert ctx.skip_calibrate is False
        assert ctx.max_review_iterations == 25
        assert ctx.mitm_web is False
        assert ctx.token_reduce is False

    def test_atomic_checkpoint_save_and_load(self, tmp_path):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "checkpoint-test", "goal": "Verify atomic saving"},
            agent="antigravity-agent",
        )
        ctx.stage_results["intent"] = StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)

        checkpoint_file = str(tmp_path / ".holon" / "flow" / "checkpoint-checkpoint-test.json")
        saved_path = save_checkpoint(ctx, checkpoint_file)
        assert os.path.exists(saved_path)

        loaded = load_checkpoint(saved_path)
        assert loaded.intent_slug == "checkpoint-test"
        assert loaded.context.agent == "antigravity-agent"
        assert "intent" in loaded.completed_stages

        # Test non-existent checkpoint
        with pytest.raises(FileNotFoundError):
            load_checkpoint(str(tmp_path / "does_not_exist.json"))

    def test_checkpoint_schema_version_serialized(self, tmp_path):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "version-test", "goal": "Check version"},
            agent="antigravity-agent",
        )
        checkpoint_file = str(tmp_path / "checkpoint-version.json")
        saved_path = save_checkpoint(ctx, checkpoint_file)
        with open(saved_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("schema_version") == 1
        assert data.get("context", {}).get("schema_version") == 1

    def test_load_legacy_v0_checkpoint_auto_migration(self, tmp_path):
        v0_data = {
            "intent_slug": "legacy-intent",
            "current_stage": "intent",
            "completed_stages": [],
            "context": {
                "repo_dir": str(tmp_path),
                "agent": "antigravity-agent",
                "model": "gemini-3.8-flash-medium",
            },
            "timestamp": "2026-01-01T00:00:00Z",
        }
        legacy_file = tmp_path / "checkpoint-legacy.json"
        legacy_file.write_text(json.dumps(v0_data), encoding="utf-8")

        loaded = load_checkpoint(str(legacy_file))
        assert loaded.schema_version == 1
        assert loaded.context.schema_version == 1
        assert loaded.context.max_review_iterations == 25
        assert loaded.context.token_reduce is False
        assert loaded.context.mitm_web is False

    def test_migration_dispatch_table_execution(self):
        v0_raw = {
            "intent_slug": "test-dispatch",
            "current_stage": "plan",
            "completed_stages": ["intent"],
            "context": {
                "agent": "codex",
            },
        }
        migrated = migrate_checkpoint_data(v0_raw, target_version=1)
        assert migrated["schema_version"] == 1
        assert migrated["context"]["schema_version"] == 1
        assert migrated["context"]["agent"] == "codex"
        assert migrated["context"]["max_review_iterations"] == 25
        assert migrated["context"]["token_reduce"] is False

    def test_future_schema_version_rejection(self, tmp_path):
        future_data = {
            "schema_version": 999,
            "intent_slug": "future-intent",
            "current_stage": "intent",
            "completed_stages": [],
            "context": {},
            "timestamp": "2028-01-01T00:00:00Z",
        }
        future_file = tmp_path / "checkpoint-future.json"
        future_file.write_text(json.dumps(future_data), encoding="utf-8")

        with pytest.raises(ValueError, match="Unsupported checkpoint schema_version 999"):
            load_checkpoint(str(future_file))

    def test_none_schema_version_handling(self):
        v0_null = {
            "schema_version": None,
            "intent_slug": "legacy-slug",
            "current_stage": "intent",
            "context": {"schema_version": None},
        }
        cp = FlowCheckpoint.from_dict(v0_null)
        assert cp.schema_version == 1
        assert cp.context.schema_version == 1

        ctx = FlowContext.from_dict({"schema_version": None, "agent": "claude"})
        assert ctx.schema_version == 1
        assert ctx.agent == "claude"

    def test_future_context_schema_version_rejection(self):
        with pytest.raises(ValueError, match="Unsupported context schema_version 999"):
            FlowContext.from_dict({"schema_version": 999})


class TestFlowStages:
    """Test the individual execution stages with isolated mocking."""

    def test_stage1_intent_validation_failure(self):
        ctx = FlowContext(intent_data={}, dry_run=True)
        res = run_intent_stage(ctx)
        assert res.status == StageStatus.FAILED
        assert "Validation failed" in (res.error or "")

    def test_intent_stage_fails_fast_on_unresolved_open_questions(self):
        ctx = FlowContext(
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

    def test_stage4_review_bean_0034_human_only_merge_halt(self, tmp_path):
        """CRITICAL INVARIANT TEST: Bean 0034 Human-Only PR Merging rule.

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

        with patch("sandbox_executor.flow.run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0, stdout=" 1 file changed, 5 insertions(+)\n")
            res = run_review_stage(ctx)

            # Assert git merge was NEVER called
            for call in mock_git.call_args_list:
                args = call[0][0]
                assert "merge" not in args, f"VIOLATION OF BEAN 0034: Autonomous merge detected with args {args}"

        assert res.status == StageStatus.HALTED_FOR_HUMAN
        assert res.payload["halted_for_human"] is True
        assert res.payload["bean_0034_enforced"] is True
        assert res.payload["consensus"]["approved"] is True
        assert "holon review approve" in res.payload["human_approval_command"]

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

    def test_stage5_calibrate_bean_0038(self, tmp_path):
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

    def test_stage5_calibrate_bean_0038_mocked(self, tmp_path):
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
            patch("sandbox_executor.flow._checkout_execution_branch", return_value=None),
            patch("sandbox_executor.flow.run_calibrate", return_value=mock_report) as mock_run,
        ):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert res.payload["calibration_report"]["accuracy_score"] == 0.95
        assert mock_run.call_args.kwargs["log_fn"] == ctx.log
        assert ctx.calibrated_branch == "I-100-intent/P-200-plan/E-300-exec/calibrated"

    def test_stage5_calibrate_fallback_invokes_converge_prettier(self, tmp_path):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            dry_run=False,
        )

        with (
            patch("sandbox_executor.flow._checkout_execution_branch", return_value=None),
            patch("sandbox_executor.flow.run_calibrate", side_effect=RuntimeError("calibration failed")),
            patch("sandbox_executor.flow.converge_prettier") as mock_converge,
        ):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        mock_converge.assert_called_once_with(["plans/P-200-plan_calibration.md"], repo_dir=str(tmp_path))

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
            patch("sandbox_executor.flow.run_calibrate", side_effect=RuntimeError("calibration failed")),
            patch("sandbox_executor.flow.converge_prettier"),
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
            patch("sandbox_executor.flow.run_calibrate", side_effect=failing_run_calibrate),
            patch("sandbox_executor.flow.converge_prettier"),
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
            patch("sandbox_executor.flow._checkout_execution_branch", return_value=None),
            patch("sandbox_executor.flow.run_calibrate", side_effect=RuntimeError("calibration failed")),
            patch("sandbox_executor.flow.converge_prettier", side_effect=OSError("prettier missing")),
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
            patch("sandbox_executor.flow._checkout_execution_branch", return_value=None),
            patch("sandbox_executor.flow.run_calibrate", side_effect=RuntimeError(f"fetch failed {sample_value}")),
            patch("sandbox_executor.flow.converge_prettier"),
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
            patch("sandbox_executor.flow._checkout_execution_branch", return_value=None),
            patch("sandbox_executor.flow.run_calibrate", side_effect=RuntimeError(hook_output)),
            patch("sandbox_executor.flow.converge_prettier"),
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
            patch("sandbox_executor.flow.run_calibrate", side_effect=RuntimeError("calibration failed")),
            patch("sandbox_executor.flow.converge_prettier", side_effect=OSError(f"npx auth {sample_value}")),
        ):
            run_calibrate_stage(ctx)

        assert any("Prettier formatting failed" in line and "[REDACTED_SECRET]" in line for line in ctx.logs)
        assert all(sample_value not in line for line in ctx.logs)

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

        with patch("sandbox_executor.flow.run_git", side_effect=fake_run_git):
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


class TestFlowResumptionAndErrorHandling:
    """Test engine orchestration, resumption from stages, and error handling."""

    def test_pipeline_engine_sequential_run(self, tmp_path):
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "e2e-flow", "goal": "End to end test"},
            auto_calibrate=True,
            dry_run=True,
        )

        engine = PipelineEngine(ctx)
        results = engine.run()

        # Intent, Plan, Execute, Review (halted for human), and Calibrate should be recorded
        assert "intent" in results
        assert results["intent"].status == StageStatus.SUCCESS
        assert "plan" in results
        assert results["plan"].status == StageStatus.SUCCESS
        assert "execute" in results
        assert results["execute"].status == StageStatus.SUCCESS
        assert "review" in results
        assert results["review"].status == StageStatus.HALTED_FOR_HUMAN
        assert "calibrate" in results
        assert results["calibrate"].status == StageStatus.SUCCESS

    def test_pipeline_resumption_from_stage(self, tmp_path):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_branch="I-555-resume/_",
            plan_branch="I-555-resume/P-666-plan/_",
            dry_run=True,
        )
        ctx.stage_results["intent"] = StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)
        ctx.stage_results["plan"] = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)

        engine = PipelineEngine(ctx, from_stage=FlowStage.EXECUTE)
        with patch.object(engine, "execute_stage", wraps=engine.execute_stage) as mock_exec:
            engine.run()
            # Assert INTENT and PLAN were skipped
            executed_stages = [call[0][0] for call in mock_exec.call_args_list]
            assert FlowStage.INTENT not in executed_stages
            assert FlowStage.PLAN not in executed_stages
            assert FlowStage.EXECUTE in executed_stages

    def test_pipeline_invalid_resumption_stage(self, tmp_path):
        ctx = FlowContext(repo_dir=str(tmp_path), dry_run=True)
        with pytest.raises(ValueError, match="Unknown FlowStage"):
            PipelineEngine(ctx, from_stage="invalid_stage")

        engine = PipelineEngine(ctx, from_stage=FlowStage.COMPLETED)
        with pytest.raises(ValueError, match="Invalid resumption stage"):
            engine.run()

    def test_pipeline_stage_failure_handling(self, tmp_path):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={},  # Invalid intent payload triggers failure
            dry_run=True,
        )
        engine = PipelineEngine(ctx)
        results = engine.run()
        assert results["intent"].status == StageStatus.FAILED
        # Following stages must not have executed
        assert "plan" not in results

    def test_pipeline_engine_skip_review(self, tmp_path):
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "skip-rev-flow", "goal": "Skip review"},
            skip_review=True,
            auto_calibrate=True,
            dry_run=True,
        )

        engine = PipelineEngine(ctx)
        results = engine.run()

        assert "review" in results
        assert results["review"].status == StageStatus.SKIPPED
        assert results["review"].payload.get("skipped_by_flag") is True
        assert "calibrate" in results
        assert results["calibrate"].status == StageStatus.SUCCESS

    def test_pipeline_engine_skip_calibrate(self, tmp_path):
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "skip-cal-flow", "goal": "Skip calibrate"},
            skip_calibrate=True,
            auto_calibrate=False,
            dry_run=True,
        )

        engine = PipelineEngine(ctx)
        results = engine.run()

        assert "calibrate" in results
        assert results["calibrate"].status == StageStatus.SKIPPED
        assert results["calibrate"].payload.get("skipped_by_flag") is True

    def test_pipeline_engine_skip_review_and_calibrate(self, tmp_path):
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "skip-both-flow", "goal": "Skip both"},
            skip_review=True,
            skip_calibrate=True,
            dry_run=True,
        )

        engine = PipelineEngine(ctx)
        results = engine.run()

        assert results["review"].status == StageStatus.SKIPPED
        assert results["calibrate"].status == StageStatus.SKIPPED

    def test_review_stage_records_max_review_iterations(self, tmp_path):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            max_review_iterations=7,
            dry_run=True,
        )
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"test_pass_rate": 1.0, "exit_code": 0},
        )
        res = run_review_stage(ctx)
        assert res.payload["consensus"]["max_review_iterations"] == 7
        assert res.payload["review_package"]["consensus"]["max_review_iterations"] == 7

    def test_review_halted_for_human_respects_skip_calibrate(self, tmp_path):
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "halt-skip-cal", "goal": "Test halt with skip cal"},
            skip_calibrate=True,
            dry_run=True,
        )

        engine = PipelineEngine(ctx)
        results = engine.run()

        assert results["review"].status == StageStatus.HALTED_FOR_HUMAN
        assert results["calibrate"].status == StageStatus.SKIPPED
        assert results["calibrate"].payload.get("skipped_by_flag") is True


class TestFlowCLI:
    """Test CLI subcommands, flags, and dispatching."""

    def test_cli_flow_help(self):
        with pytest.raises(SystemExit) as exc_info:
            cli_main_args = ["holon", "flow", "--help"]
            with patch("sys.argv", cli_main_args):
                cli_main()
        assert exc_info.value.code == 0

    def test_cli_flow_dry_run_dispatch(self, tmp_path):
        intent_file = tmp_path / "intent.json"
        intent_file.write_text(json.dumps({"slug": "cli-test", "goal": "CLI flow test"}))

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


class TestLedgerReconciliation:
    """Tests for deterministic ledger union reconciliation and conflict resolution."""

    def test_extract_valid_ledger_rows_filters_conflict_markers(self):
        conflicted_content = (
            "<<<<<<< HEAD\n"
            '{"branch": "b1", "slug": "s1", "created_at": "2026-10-06T10:00:00Z"}\n'
            "||||||| parent\n"
            "malformed non-json line\n"
            "=======\n"
            '{"branch": "b2", "slug": "s2", "created_at": "2026-10-06T10:05:00Z"}\n'
            ">>>>>>> origin/main\n"
        )
        rows = extract_valid_ledger_rows(conflicted_content)
        assert len(rows) == 2
        assert rows[0]["slug"] == "s1"
        assert rows[1]["slug"] == "s2"

    def test_reconcile_intents_ledger_deduplication_and_order(self):
        content = (
            '{"slug": "intent-2", "branch": "I-intent-2", "created_at": "2026-10-06T12:00:00Z"}\n'
            '{"slug": "intent-1", "branch": "I-intent-1", "created_at": "2026-10-06T08:00:00Z"}\n'
            '{"slug": "intent-1", "branch": "I-intent-1", "created_at": "2026-10-06T09:00:00Z", "extra": "latest"}\n'
        )
        result = reconcile_ledger_content(content, "intents")
        lines = [json.loads(line) for line in result.splitlines() if line.strip()]
        assert len(lines) == 2
        # Ordered chronologically by created_at
        assert lines[0]["slug"] == "intent-1"
        assert lines[0]["extra"] == "latest"
        assert lines[1]["slug"] == "intent-2"

    def test_reconcile_plans_ledger_deduplication(self):
        content = (
            '{"plan_id": "P-100", "created_at": "2026-10-06T10:00:00Z", "status": "proposed"}\n'
            '{"plan_id": "P-100", "created_at": "2026-10-06T10:00:00Z", "status": "superseded", "ledger_revision": 2}\n'
            '{"plan_id": "P-200", "created_at": "2026-10-06T11:00:00Z", "status": "proposed"}\n'
        )
        result = reconcile_ledger_content(content, "plans")
        lines = [json.loads(line) for line in result.splitlines() if line.strip()]
        assert len(lines) == 2
        assert lines[0]["plan_id"] == "P-100"
        assert lines[0]["status"] == "superseded"
        assert lines[0]["ledger_revision"] == 2
        assert lines[1]["plan_id"] == "P-200"

    def test_reconcile_executions_ledger_deduplication(self):
        content = (
            '{"execution_id": "E-2", "created_at": "2026-10-06T11:00:00Z", "status": "success"}\n'
            '{"execution_id": "E-1", "created_at": "2026-10-06T09:00:00Z", "status": "failed"}\n'
            '{"execution_id": "E-1", "created_at": "2026-10-06T09:30:00Z", "status": "success"}\n'
        )
        result = reconcile_ledger_content(content, "executions")
        lines = [json.loads(line) for line in result.splitlines() if line.strip()]
        assert len(lines) == 2
        assert lines[0]["execution_id"] == "E-1"
        assert lines[0]["status"] == "success"
        assert lines[1]["execution_id"] == "E-2"

    def test_reconcile_ledger_with_git_conflict_markers(self, tmp_path):
        ledger_path = tmp_path / "executions.jsonl"
        conflicted = (
            "<<<<<<< HEAD\n"
            '{"execution_id": "E-head", "created_at": "2026-10-06T10:00:00Z"}\n'
            "=======\n"
            '{"execution_id": "E-main", "created_at": "2026-10-06T09:00:00Z"}\n'
            ">>>>>>> origin/main\n"
        )
        ledger_path.write_text(conflicted, encoding="utf-8")
        success = reconcile_ledger_file(str(ledger_path))
        assert success is True

        reconciled = ledger_path.read_text(encoding="utf-8")
        assert "<<<<<<<" not in reconciled
        assert "=======" not in reconciled
        assert ">>>>>>>" not in reconciled
        lines = [json.loads(line) for line in reconciled.splitlines() if line.strip()]
        assert len(lines) == 2
        # Chronological order: E-main (09:00) before E-head (10:00)
        assert lines[0]["execution_id"] == "E-main"
        assert lines[1]["execution_id"] == "E-head"

    def test_reconcile_ledger_rows_direct(self):
        raw_rows = [
            {"plan_id": "P-1", "created_at": "2026-10-06T10:00:00Z", "v": 1},
            {"plan_id": "P-1", "created_at": "2026-10-06T10:00:00Z", "v": 2, "ledger_revision": 2},
        ]
        reconciled = reconcile_ledger_rows(raw_rows, "plans")
        assert len(reconciled) == 1
        assert reconciled[0]["v"] == 2

    def test_reconcile_ledger_rows_with_none_or_invalid_revision(self):
        raw_rows = [
            {"plan_id": "P-1", "created_at": "2026-10-06T10:00:00Z", "v": 1, "ledger_revision": None},
            {"plan_id": "P-1", "created_at": "2026-10-06T10:00:00Z", "v": 2, "ledger_revision": 2},
        ]
        # Should not raise TypeError and higher revision should win
        reconciled = reconcile_ledger_rows(raw_rows, "plans")
        assert len(reconciled) == 1
        assert reconciled[0]["v"] == 2

        # Invalid string revision should fallback gracefully to 1
        raw_rows_invalid = [
            {"plan_id": "P-2", "created_at": "2026-10-06T10:00:00Z", "v": 1, "ledger_revision": "invalid"},
            {"plan_id": "P-2", "created_at": "2026-10-06T10:00:00Z", "v": 2, "ledger_revision": 1},
        ]
        reconciled_invalid = reconcile_ledger_rows(raw_rows_invalid, "plans")
        assert len(reconciled_invalid) == 1
        assert reconciled_invalid[0]["v"] == 1

    def test_reconcile_ledger_file_atomic(self, tmp_path):
        ledger_file = tmp_path / "plans.jsonl"
        ledger_file.write_text('{"plan_id": "p1", "created_at": "2026-10-06T10:00:00Z"}\n')
        assert reconcile_ledger_file(str(ledger_file)) is True
        # Verify no temp files remain in directory
        dir_files = [f.name for f in tmp_path.iterdir()]
        assert dir_files == ["plans.jsonl"]

    def test_reconcile_ledgers_utility(self, tmp_path):
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)
        intents_file = ledger_dir / "intents.jsonl"
        plans_file = ledger_dir / "plans.jsonl"
        intents_file.write_text('{"branch": "b1", "slug": "s1", "created_at": "2026-10-06T10:00:00Z"}\n')
        plans_file.write_text('{"plan_id": "p1", "created_at": "2026-10-06T10:05:00Z"}\n')

        reconciled = reconcile_ledgers(str(tmp_path))
        assert "holon-knowledge/ledger/intents.jsonl" in reconciled
        assert "holon-knowledge/ledger/plans.jsonl" in reconciled


class TestReviewMergeabilityGuards:
    """Tests for PR mergeability inspection and DIRTY / CONFLICTING guardrails."""

    def test_review_stage_flags_dirty_merge_state(self):
        context = FlowContext(
            repo_dir=".",
            execution_branch="I-100/P-100/E-100",
            plan_branch="I-100/P-100",
            intent_branch="I-100",
            dry_run=True,
        )
        context.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"test_pass_rate": 1.0, "exit_code": 0},
        )

        mock_mergeability = {
            "is_dirty": True,
            "merge_state_status": "DIRTY",
            "mergeable": "CONFLICTING",
            "source": "github_cli",
        }

        with patch("sandbox_executor.flow.check_pr_mergeability", return_value=mock_mergeability):
            result = run_review_stage(context)

        assert result.status == StageStatus.FAILED
        assert result.payload["consensus"]["approved"] is False
        assert result.payload["consensus"]["status"] == "conflicted_dirty"
        assert result.error is not None
        assert "CI SIGNAL SUPPRESSED" in result.error
        assert "DIRTY" in result.error

    def test_check_pr_mergeability_via_gh_cli(self):
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run") as mock_subproc,
        ):
            mock_subproc.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(
                    {
                        "number": 42,
                        "mergeable": "CONFLICTING",
                        "mergeStateStatus": "DIRTY",
                        "statusCheckRollup": [],
                    }
                ),
            )
            res = check_pr_mergeability(".", "my-branch", "main")
            assert res["is_dirty"] is True
            assert res["mergeable"] == "CONFLICTING"
            assert res["merge_state_status"] == "DIRTY"
            assert res["source"] == "github_cli"
            assert res["pr_number"] == 42

    def test_check_pr_mergeability_short_circuit_on_gh_error(self):
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run") as mock_subproc,
            patch("sandbox_executor.flow.run_git") as mock_git,
        ):
            # gh pr view returns error (e.g. no PR found)
            mock_subproc.return_value = MagicMock(returncode=1, stdout="", stderr="no pull requests found")
            mock_git.return_value = MagicMock(returncode=0, stdout="")

            res = check_pr_mergeability(".", "my-branch", "main")
            # Should short-circuit and call gh pr view only ONCE, not 3 times
            assert mock_subproc.call_count == 1
            assert res["source"] == "git_local"

    def test_check_pr_mergeability_local_git_fallback_with_remote_tracking_ref(self):
        with (
            patch("shutil.which", return_value=None),
            patch("sandbox_executor.flow.run_git") as mock_git,
        ):

            def git_side_effect(args, **kwargs):
                cmd = args[0]
                if cmd == "rev-parse":
                    ref = args[-1]
                    # Commit-peel lookups ('<ref>^{commit}') resolve the chosen refs to SHAs.
                    if ref == "origin/main^{commit}":
                        return MagicMock(returncode=0, stdout="a" * 40, stderr="")
                    if ref == "origin/feature-branch^{commit}":
                        return MagicMock(returncode=0, stdout="b" * 40, stderr="")
                    if ref == "origin/main":
                        return MagicMock(returncode=0, stdout="origin/main")
                    if ref == "feature-branch":
                        return MagicMock(returncode=1, stdout="")
                    if ref == "origin/feature-branch":
                        return MagicMock(returncode=0, stdout="origin/feature-branch")
                    return MagicMock(returncode=1, stdout="")
                if cmd == "merge-tree":
                    # args: ['merge-tree', '--write-tree', branch_sha, target_sha]
                    assert args[2] == "b" * 40
                    assert args[3] == "a" * 40
                    return MagicMock(returncode=0, stdout="tree-sha")
                return MagicMock(returncode=0, stdout="")

            mock_git.side_effect = git_side_effect
            res = check_pr_mergeability(".", "feature-branch", "main")
            assert res["source"] == "git_local"
            assert res["is_dirty"] is False
            assert res["mergeable"] == "MERGEABLE"

    def test_flow_context_token_reduce_auto_detection(self, monkeypatch):
        """Verify FlowContext defaults token_reduce=True when proxy is detected and preserves False when opted out."""
        with patch("sandbox_executor.cli.detect_holon_coherence", return_value=True):
            ctx = FlowContext()
            assert ctx.token_reduce is True

        monkeypatch.setenv("HOLON_TOKEN_REDUCE", "0")
        ctx_opt_out = FlowContext()
        assert ctx_opt_out.token_reduce is False

        # When explicitly passed False, preserves False
        ctx_explicit_false = FlowContext(token_reduce=False)
        assert ctx_explicit_false.token_reduce is False

    def test_flow_stage_environment_propagation(self, monkeypatch, tmp_path):
        """Verify proxy settings reach the execute-stage dispatcher and the review stage subprocess environment."""
        host_proxy_envs = {
            "HTTP_PROXY": "http://127.0.0.1:8080",
            "HTTPS_PROXY": "http://127.0.0.1:8080",
            "NODE_EXTRA_CA_CERTS": "/tmp/mitmproxy-ca-cert.pem",
            "SSL_CERT_FILE": "/tmp/mitmproxy-ca-cert.pem",
        }
        monkeypatch.setattr(
            "sandbox_executor.cli.get_host_proxy_envs",
            lambda: host_proxy_envs,
        )

        # 1. Execute stage: proxy settings reach the role-agent dispatcher, which mounts them into the container
        dispatcher = MagicMock(return_value=0)
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            token_reduce=True,
            mitm_web=True,
            plan_branch="I-test/P-1/_",
            dry_run=False,
            dispatcher=dispatcher,
        )
        with patch("sandbox_executor.flow.run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0, stdout="")
            res = run_execute_stage(ctx)
        dispatcher.assert_called_once()
        assert dispatcher.call_args.kwargs["token_reduce"] is True
        assert dispatcher.call_args.kwargs["mitm_web"] is True
        assert res.status == StageStatus.FAILED  # No execution branch was pushed by the mocked dispatcher.
        assert "no execution branch" in (res.error or "")

        # 2. Review stage propagation
        ctx_rev = FlowContext(
            repo_dir=str(tmp_path),
            token_reduce=True,
            execution_branch="I-test/P-1/E-1/_",
            plan_branch="I-test/P-1/_",
            dry_run=True,
        )
        ctx_rev.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"test_pass_rate": 1.0, "exit_code": 0},
        )
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("sandbox_executor.flow.run_git") as mock_git,
            patch("subprocess.run") as mock_subproc,
        ):
            mock_git.return_value = MagicMock(returncode=0, stdout="")
            mock_subproc.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "number": 123}),
            )
            res_rev = run_review_stage(ctx_rev)
            assert mock_subproc.call_count >= 1
            gh_call_kwargs = mock_subproc.call_args.kwargs
            gh_env = gh_call_kwargs.get("env")
            assert gh_env is not None
            assert gh_env.get("HTTP_PROXY") == "http://127.0.0.1:8080"
            assert gh_env.get("NODE_EXTRA_CA_CERTS") == "/tmp/mitmproxy-ca-cert.pem"
            assert res_rev.payload.get("review_package", {}).get("token_reduce") is True
            assert res_rev.payload.get("review_package", {}).get("proxy_active") is True

    def test_get_host_proxy_envs_contract(self, monkeypatch):
        """Verify get_host_proxy_envs returns localhost URLs and CA paths when probe succeeds."""
        from sandbox_executor.cli import get_host_proxy_envs

        # When opted out, returns empty dict
        monkeypatch.setenv("HOLON_TOKEN_REDUCE", "0")
        assert get_host_proxy_envs() == {}

        monkeypatch.delenv("HOLON_TOKEN_REDUCE", raising=False)

        # When socket connects and CA exists, returns host envs
        with (
            patch("socket.create_connection"),
            patch("os.path.exists", return_value=True),
        ):
            envs = get_host_proxy_envs()
            assert envs["HTTP_PROXY"] == "http://127.0.0.1:8080"
            assert envs["HTTPS_PROXY"] == "http://127.0.0.1:8080"
            assert "NODE_EXTRA_CA_CERTS" in envs
            assert "SSL_CERT_FILE" in envs


class TestReviewLoopStage:
    """Comprehensive tests for native multi-pass review loop, CI check probing, git trailers, and Bean 0034."""

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

    def test_review_loop_consensus_approval_halts_under_bean_0034(self, tmp_path):
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
        with patch("sandbox_executor.flow.run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0, stdout=" 1 file changed, 10 insertions(+)\n")
            res = run_review_loop(ctx, target="I-100-intent/P-200-plan/E-300-exec/_", max_iterations=5)

            # Bean 0034 check: git merge main must never have been called
            for call in mock_git.call_args_list:
                args = call[0][0]
                assert "merge" not in args, f"VIOLATION OF BEAN 0034: Autonomous merge detected with args {args}"

        assert res["consensus"]["approved"] is True
        assert res["halted_for_human"] is True
        assert res["bean_0034_enforced"] is True
        assert "holon review approve" in res["human_approval_command"]

    def test_review_loop_max_iterations_exhaustion(self, tmp_path):
        """Simulate failing tests and verify loop terminates at max_iterations without infinite looping."""
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
        assert res["total_iterations"] == 3
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
            patch("sandbox_executor.flow.run_git") as mock_git,
            patch("sandbox_executor.flow.post_pr_review_consensus") as mock_post,
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
        with patch("sandbox_executor.flow.run_review_loop") as mock_loop:
            mock_loop.return_value = {
                "consensus": {"approved": True, "status": "approved", "iterations": 1},
                "max_iterations": 2,
                "ci_checks": {"total": 1, "passed": 1, "failed": 0, "pending": 0, "source": "local_tests"},
                "human_approval_command": "holon review approve I-100-intent/P-200-plan/E-300-exec/_",
            }
            code = run_review_loop_cli(args)
            assert code == 0
            captured = capsys.readouterr().out
            assert "Holon Review Loop Summary" in captured
            assert "BEAN 0034 SAFETY HALT" in captured
            assert "holon review approve" in captured

        # Test rejected exit code
        with patch("sandbox_executor.flow.run_review_loop") as mock_loop:
            mock_loop.return_value = {
                "consensus": {"approved": False, "status": "rejected", "iterations": 2},
                "max_iterations": 2,
                "ci_checks": {"total": 1, "passed": 0, "failed": 1, "pending": 0, "source": "local_tests"},
            }
            code = run_review_loop_cli(args)
            assert code == 1


class TestSemanticPRAndConsensusReview:
    """Hermetic unit tests for semantic PR generation, dual-posting review consensus,
    and granular ledger action logging."""

    def test_generate_semantic_pr_metadata_conventional_categories(self):
        """Verify category mapping to conventional commit prefixes and scope detection."""
        cases = [
            ({"category": "feature", "slug": "my-feat", "title": "Add cool feature"}, "feat(my): Add cool feature"),
            ({"category": "fix", "labels": ["api"], "title": "Fix crash on null"}, "fix(api): Fix crash on null"),
            (
                {"category": "bugfix", "slug": "db-patch", "title": "Patch database leak"},
                "fix(db): Patch database leak",
            ),
            (
                {"category": "refactor", "slug": "cleanup-flow", "title": "Refactor flow stages"},
                "refactor(cleanup): Refactor flow stages",
            ),
            (
                {"category": "documentation", "slug": "docs-guide", "title": "Update README"},
                "docs(docs): Update README",
            ),
            ({"category": "chore", "title": "Bump dependencies"}, "chore(core): Bump dependencies"),
            ({"category": "test", "labels": ["flow"], "title": "Add unit tests"}, "test(flow): Add unit tests"),
            (
                {"category": "ci", "labels": ["github-actions"], "title": "Update matrix"},
                "ci(github-actions): Update matrix",
            ),
        ]
        for intent, expected_prefix in cases:
            title, _ = generate_semantic_pr_metadata(intent)
            assert title == expected_prefix, f"Expected '{expected_prefix}', got '{title}'"

    def test_generate_semantic_pr_metadata_markdown_structure(self):
        """Verify structured markdown body includes Summary, Key Changes, Referenced Beans, and Plan Metadata."""
        intent = {
            "category": "feature",
            "slug": "flow-enhancements",
            "title": "Enhance review loop and PR generation",
            "description": "Comprehensive flow pipeline enhancements.",
            "goal": "1) MAKEFILE ALIGNMENT (Bean 0116)\n2) LEDGER ACTIONS (Bean 0113)\n3) PR CONSENSUS (Bean 0119)",
            "beans": ["0116", "0113", "0119", "0109"],
            "labels": ["makefile", "flow"],
        }
        plan_meta = {
            "plan_id": "P-1792243199-antigravity",
            "ev": 81.0,
            "agent": "antigravity-agent",
            "model": "gemini-3.8-flash-medium",
        }
        title, body = generate_semantic_pr_metadata(intent, plan_meta)

        assert title == "feat(makefile): Enhance review loop and PR generation"
        assert "## Summary" in body
        assert "Comprehensive flow pipeline enhancements." in body
        assert "## Key Changes" in body
        assert "- MAKEFILE ALIGNMENT (Bean 0116)" in body
        assert "- LEDGER ACTIONS (Bean 0113)" in body
        assert "- PR CONSENSUS (Bean 0119)" in body
        assert "## Referenced Beans" in body
        assert "Closes Bean 0116, 0113, 0119, 0109" in body
        assert "## Plan & Validation Metadata" in body
        assert "- **Plan ID:** `P-1792243199-antigravity`" in body
        assert "- **Expected Value (EV):** `81.00`" in body
        assert "- **Agent:** `antigravity-agent`" in body
        assert "- **Model:** `gemini-3.8-flash-medium`" in body

    def test_generate_semantic_pr_metadata_fallback_handling(self):
        """Verify resilient fallback defaults for empty or missing intent data."""
        title, body = generate_semantic_pr_metadata({})
        assert title == "feat(core): Update components"
        assert "## Summary" in body
        assert "No description provided." in body
        assert "## Key Changes" in body
        assert "## Referenced Beans" in body
        assert "None" in body
        assert "## Plan & Validation Metadata" in body
        assert "- **Plan ID:** `N/A`" in body

    def test_generate_semantic_pr_metadata_title_length_truncation(self):
        """Verify title truncation when length exceeds 100 characters."""
        intent = {
            "category": "feature",
            "labels": ["extremely-long-label-scope"],
            "title": "A" * 120,
        }
        title, _ = generate_semantic_pr_metadata(intent)
        assert len(title) <= 100
        assert title.endswith("...")

    def test_post_pr_review_consensus_dual_posting(self):
        """Verify dual-posting runs both gh pr comment and gh pr review --approve."""
        with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="success", stderr="")
            consensus_data = {
                "approved": True,
                "status": "approved",
                "iterations": 1,
                "max_review_iterations": 25,
                "ci_checks": {"passed": 5, "total": 5, "source": "github_cli"},
                "mergeability": {"mergeable": "MERGEABLE", "merge_state_status": "CLEAN"},
                "trailers": "Action-Origin: pr_review",
            }
            res = post_pr_review_consensus(repo_dir="/tmp", target="42", consensus_data=consensus_data)

            assert res["dual_posted"] is True
            assert res["discussion_comment"]["success"] is True
            assert res["official_review"]["success"] is True
            assert res["official_review"]["self_approval_fallback"] is False
            assert res["official_review"]["flag"] == "--approve"

            # Verify subprocess call args
            assert mock_run.call_count == 2
            comment_args = mock_run.call_args_list[0][0][0]
            review_args = mock_run.call_args_list[1][0][0]
            assert comment_args[:4] == ["gh", "pr", "comment", "42"]
            assert review_args[:5] == ["gh", "pr", "review", "42", "--approve"]

    def test_post_pr_review_consensus_self_approval_422_fallback(self):
        """Verify HTTP 422 author self-approval restriction triggers graceful fallback to --comment."""
        with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run") as mock_run:
            mock_comment = MagicMock(returncode=0, stdout="comment posted", stderr="")
            mock_approve = MagicMock(
                returncode=1,
                stdout="",
                stderr="GraphQL: Can not approve your own pull request (HTTP 422)",
            )
            mock_fallback = MagicMock(returncode=0, stdout="review comment posted", stderr="")
            mock_run.side_effect = [mock_comment, mock_approve, mock_fallback]

            consensus_data = {
                "approved": True,
                "status": "approved",
                "iterations": 1,
                "max_review_iterations": 25,
            }
            res = post_pr_review_consensus(repo_dir="/tmp", target="42", consensus_data=consensus_data)

            assert res["dual_posted"] is True
            assert res["discussion_comment"]["success"] is True
            assert res["official_review"]["success"] is True
            assert res["official_review"]["self_approval_fallback"] is True
            assert res["official_review"]["flag"] == "--comment"

            assert mock_run.call_count == 3
            fallback_args = mock_run.call_args_list[2][0][0]
            assert fallback_args[:5] == ["gh", "pr", "review", "42", "--comment"]

    def test_post_pr_review_consensus_comment_failure_proceeds_to_review(self):
        """Verify failure in gh pr comment does not prevent official review submission."""
        with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run") as mock_run:
            mock_comment = MagicMock(returncode=1, stdout="", stderr="API rate limit")
            mock_review = MagicMock(returncode=0, stdout="approved", stderr="")
            mock_run.side_effect = [mock_comment, mock_review]

            consensus_data = {"approved": True}
            res = post_pr_review_consensus(repo_dir="/tmp", target="42", consensus_data=consensus_data)

            assert res["discussion_comment"]["success"] is False
            assert res["official_review"]["success"] is True
            assert res["dual_posted"] is False
            assert mock_run.call_count == 2

    def test_post_pr_review_consensus_non_422_failure_no_fallback(self):
        """Verify non-422 error does not trigger self-approval fallback."""
        with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run") as mock_run:
            mock_comment = MagicMock(returncode=0, stdout="commented", stderr="")
            mock_review = MagicMock(returncode=1, stdout="", stderr="Internal Server Error (HTTP 500)")
            mock_run.side_effect = [mock_comment, mock_review]

            consensus_data = {"approved": True}
            res = post_pr_review_consensus(repo_dir="/tmp", target="42", consensus_data=consensus_data)

            assert res["official_review"]["success"] is False
            assert res["official_review"]["self_approval_fallback"] is False
            assert mock_run.call_count == 2

    def test_post_pr_review_consensus_when_gh_missing_returns_diagnostics(self):
        """Verify post_pr_review_consensus returns clear diagnostics and handles gh absence cleanly."""
        with patch("shutil.which", return_value=None):
            consensus_data = {"approved": True, "status": "approved", "iterations": 1}
            res = post_pr_review_consensus(repo_dir="/tmp", target="42", consensus_data=consensus_data)

            assert res["gh_available"] is False
            assert res["dual_posted"] is False
            assert res["discussion_comment"]["attempted"] is False
            assert res["discussion_comment"]["success"] is False
            assert "not available in PATH" in res["diagnostics"]
            assert "not available in PATH" in res["discussion_comment"]["output"]
            assert res["official_review"]["attempted"] is False
            assert res["official_review"]["success"] is False
            assert "not available in PATH" in res["official_review"]["output"]

    def test_post_pr_review_consensus_error_output_surfaces_diagnostic_context(self):
        """Verify non-zero gh failure captures and surfaces exit code and stderr diagnostics."""
        with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run") as mock_run:
            mock_comment = MagicMock(returncode=2, stdout="", stderr="GraphQL error: Network timeout")
            mock_review = MagicMock(returncode=2, stdout="", stderr="GraphQL error: Network timeout")
            mock_run.side_effect = [mock_comment, mock_review]

            consensus_data = {"approved": True}
            res = post_pr_review_consensus(repo_dir="/tmp", target="42", consensus_data=consensus_data)

            assert res["gh_available"] is True
            assert res["dual_posted"] is False
            assert res["discussion_comment"]["success"] is False
            assert "exit code 2" in res["discussion_comment"]["output"]
            assert "Network timeout" in res["discussion_comment"]["output"]
            assert res["official_review"]["success"] is False
            assert "exit code 2" in res["official_review"]["output"]
            assert "Network timeout" in res["official_review"]["output"]
            assert "Review posting partial/failed" in res["diagnostics"]

    def test_create_or_update_pr(self):
        """Verify create_or_update_pr handles creation when absent and edit when present."""
        with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run") as mock_run:
            # Case 1: PR does not exist -> create
            mock_view_fail = MagicMock(returncode=1, stdout="", stderr="no pull requests found")
            mock_create_ok = MagicMock(returncode=0, stdout="https://github.com/org/repo/pull/1\n", stderr="")
            mock_run.side_effect = [mock_view_fail, mock_create_ok]

            res_create = create_or_update_pr("/tmp", "my-branch", "main", "feat: title", "body")
            assert res_create["action"] == "create"
            assert res_create["success"] is True
            assert res_create["pr_number"] == 1
            create_args = mock_run.call_args_list[1][0][0]
            assert "create" in create_args

            # Case 2: PR exists -> edit
            mock_view_ok = MagicMock(returncode=0, stdout=json.dumps({"number": 99, "title": "Old"}), stderr="")
            mock_edit_ok = MagicMock(returncode=0, stdout="https://github.com/org/repo/pull/99\n", stderr="")
            mock_run.side_effect = [mock_view_ok, mock_edit_ok]

            res_edit = create_or_update_pr("/tmp", "my-branch", "main", "feat: new title", "new body")
            assert res_edit["action"] == "edit"
            assert res_edit["pr_number"] == 99
            assert res_edit["success"] is True
            edit_args = mock_run.call_args_list[3][0][0]
            assert "edit" in edit_args

    def test_review_loop_granular_ledger_records(self, tmp_path):
        """Verify executions.jsonl records discrete evaluations, findings, resolver events, and dual-posting."""
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            dry_run=False,
            max_review_iterations=3,
        )
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"test_pass_rate": 1.0, "exit_code": 0},
        )

        with (
            patch("sandbox_executor.flow.check_pr_mergeability") as mock_merge,
            patch("sandbox_executor.flow.probe_ci_checks") as mock_ci,
            patch("sandbox_executor.flow.post_pr_review_consensus") as mock_post,
        ):
            # Iteration 1: finding detected (dirty conflict)
            # Iteration 2: clean and passes (resolver pass)
            mock_merge.side_effect = [
                {"is_dirty": True, "merge_state_status": "DIRTY", "mergeable": "CONFLICTING"},
                {"is_dirty": False, "merge_state_status": "CLEAN", "mergeable": "MERGEABLE"},
            ]
            mock_ci.return_value = {"all_passed": True, "passed": 1, "total": 1, "source": "github_cli"}
            mock_post.return_value = {
                "target": "I-100-intent/P-200-plan/E-300-exec/_",
                "dual_posted": True,
                "official_review": {"success": True},
            }

            res = run_review_loop(
                ctx,
                target="I-100-intent/P-200-plan/E-300-exec/_",
                max_iterations=3,
                dry_run=False,
            )
            assert res["consensus"]["approved"] is True

        exec_file = ledger_dir / "executions.jsonl"
        assert exec_file.exists()
        records = [json.loads(line) for line in exec_file.read_text(encoding="utf-8").splitlines() if line.strip()]

        action_records = [r for r in records if r.get("entry_type") == "action"]

        # 1. Evaluation records
        eval_records = [r for r in action_records if "evaluation for" in r.get("action", "")]
        assert len(eval_records) == 2
        assert eval_records[0]["review_iteration"] == 1
        assert eval_records[0]["category"] == "code"
        assert eval_records[1]["review_iteration"] == 2

        # 2. Finding detection records
        finding_records = [r for r in action_records if "findings detected" in r.get("action", "")]
        assert len(finding_records) == 1
        assert finding_records[0]["review_finding_ids"] == ["FIND-DIRTY-001"]
        assert finding_records[0]["review_severity"] == "critical"
        assert finding_records[0]["outcome"] == "failure"

        # 3. Resolver iteration record (iteration 2)
        resolver_records = [r for r in action_records if "resolver triggered" in r.get("action", "")]
        assert len(resolver_records) == 1
        assert resolver_records[0]["review_iteration"] == 2
        assert resolver_records[0]["outcome"] == "success"

        # 4. PR review dual-posting action record
        post_records = [r for r in action_records if "PR Review consensus dual-posted" in r.get("action", "")]
        assert len(post_records) == 1
        assert post_records[0]["outcome"] == "success"
        assert "dual-post" in post_records[0]["labels"]


class TestProactiveConflictAndReviewLoop:
    """Tests for Bean 0121 proactive conflict detection and Bean 0118 Review Loop Agent identity."""

    def test_review_git_env(self):
        assert REVIEW_GIT_ENV["GIT_AUTHOR_NAME"] == "Holon Review Loop Agent"
        assert REVIEW_GIT_ENV["GIT_AUTHOR_EMAIL"] == "review-loop-agent@holon-agentic-coder.com"
        assert REVIEW_GIT_ENV["GIT_COMMITTER_NAME"] == "Holon Review Loop Agent"
        assert REVIEW_GIT_ENV["GIT_COMMITTER_EMAIL"] == "review-loop-agent@holon-agentic-coder.com"

    def test_reconcile_jsonl_ledger_diff3_reset(self):
        content = (
            "<<<<<<< HEAD\n"
            '{"action_id": "ACT-001", "execution_id": "E-001", "outcome": "success", "status": "done"}\n'
            "||||||| merged common ancestors\n"
            '{"action_id": "ACT-001", "execution_id": "E-001", "outcome": "pending", "status": "open"}\n'
            "=======\n"
            '{"action_id": "ACT-002", "execution_id": "E-001", "outcome": "success", "status": "done"}\n'
            ">>>>>>> origin/main\n"
        )
        reconciled = reconcile_jsonl_ledger_conflict(content, ledger_type="executions")
        lines = [json.loads(line) for line in reconciled.strip().splitlines() if line.strip()]
        action_ids = {r.get("action_id") for r in lines}
        assert "ACT-001" in action_ids
        assert "ACT-002" in action_ids

    def test_check_and_resolve_stage_conflicts_magicmock_safety(self, tmp_path: Path):
        # Ensure isinstance(..., str) guards prevent MagicMock stdout crashes
        with patch("sandbox_executor.flow.run_git") as mock_git:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = MagicMock()  # Non-string object mock
            mock_git.return_value = mock_proc

            res = check_and_resolve_stage_conflicts(str(tmp_path), target_branch="main", stage_name="test")
            assert res["conflicts_detected"] is False
            assert res["resolved"] is True

    def test_check_and_resolve_stage_conflicts_unresolvable_raises(self, tmp_path: Path):
        with patch("sandbox_executor.flow.run_git") as mock_git:
            mock_status = MagicMock()
            mock_status.returncode = 0
            mock_status.stdout = "UU src/main.py\n"
            mock_git.return_value = mock_status

            with pytest.raises(ConflictResolutionError, match="Unresolvable merge conflict"):
                check_and_resolve_stage_conflicts(str(tmp_path), target_branch="main", stage_name="test")


# ---------------------------------------------------------------------------
# Role-agent harness: a local bare origin plus a stub dispatcher that behaves like the containers
# ---------------------------------------------------------------------------
# The shared ``_git`` fixture helper (with identity/signing overrides) is defined with the mergeability tests below.


def _configure_identity(repo: Path) -> None:
    _git(repo, "config", "user.email", "test@holon.com")
    _git(repo, "config", "user.name", "Holon Test")
    _git(repo, "config", "commit.gpgsign", "false")


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _ledger_rows(repo: Path, ref: str, name: str) -> list[dict[str, Any]]:
    content = _git(repo, "show", f"{ref}:holon-knowledge/ledger/{name}")
    return [json.loads(line) for line in content.splitlines() if line.strip()]


PLAN_MARKDOWN = """# Plan for {intent}

- **Plan ID:** {plan_id}

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.8   |
| entropy_pred        | 2.0   |
| impact_pred         | 50.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 35.4  |

## Step 1: Change the application

Edit src/app.py.
"""


class FakeRoleAgents:
    """Recording dispatcher that simulates the intent-creator, planner and executor containers.

    Each call clones origin into a fresh directory (as the containers clone HOLON_REPO_URL), creates
    the branch the real entrypoint would create, commits and pushes. Behaviour switches let tests
    model a silent planner or an executor that produces no effective diff.
    """

    def __init__(
        self,
        root: Path,
        origin: Path,
        planner_writes_plan: bool = True,
        executor_mode: str = "change",
        clock_offset: int = 0,
        during_dispatch: Callable[[str, list[str]], None] | None = None,
        recorded_test_pass_rate: Any = None,
    ):
        self.root = root
        # Written onto the executor's summary row as 'test_pass_rate' when not None (models a container test run).
        self.recorded_test_pass_rate = recorded_test_pass_rate
        self.origin = origin
        self.planner_writes_plan = planner_writes_plan
        self.executor_mode = executor_mode
        # Seconds added to the container clock that stamps branch names (models host/container skew).
        self.clock_offset = clock_offset
        # Called with (role, positional_args) while the container runs, e.g. to push a concurrent sibling.
        self.during_dispatch = during_dispatch
        self.calls: list[dict[str, Any]] = []
        self.intent_payloads: list[dict[str, Any]] = []

    def __call__(self, role: str, runner: str, model: str, positional_args: list[str], **kwargs: Any) -> int:
        self.calls.append({"role": role, "runner": runner, "model": model, "args": list(positional_args), **kwargs})
        work = self.root / f"agent-{len(self.calls)}-{role}"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(work)], check=True, capture_output=True)
        _configure_identity(work)
        handler = {"intent-creator": self._intent, "planner": self._plan, "executor": self._execute}[role]
        exit_code = handler(work, positional_args, kwargs)
        if self.during_dispatch is not None:
            self.during_dispatch(role, list(positional_args))
        return exit_code

    def now(self) -> int:
        return int(time.time()) + self.clock_offset

    def _push(self, work: Path, branch: str, message: str) -> None:
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", message)
        _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")

    def _intent(self, work: Path, _args: list[str], kwargs: dict[str, Any]) -> int:
        with open(kwargs["intent_file"], encoding="utf-8") as f:
            payload = json.load(f)
        self.intent_payloads.append(payload)
        intent_branch = f"{payload['branch']}/_"
        _git(work, "checkout", "-q", "-B", intent_branch, f"origin/{payload.get('target_branch', 'main')}")
        _append_jsonl(work / "holon-knowledge/ledger/intents.jsonl", {**payload, "status": "proposed"})
        self._push(work, intent_branch, f"intent: created {intent_branch}")
        return 0

    def _plan(self, work: Path, args: list[str], _kwargs: dict[str, Any]) -> int:
        intent_branch, agent, model = args
        if not self.planner_writes_plan:
            return 0
        plan_id = f"P-{self.now()}-{agent}-{_sanitize_model_name(model)}"
        plan_branch = f"{intent_branch.removesuffix('/_')}/{plan_id}/_"
        _git(work, "fetch", "-q", "origin", intent_branch)
        _git(work, "checkout", "-q", "-B", plan_branch, "FETCH_HEAD")
        plan_rel = f"plans/{plan_id}.md"
        (work / "plans").mkdir(exist_ok=True)
        (work / plan_rel).write_text(PLAN_MARKDOWN.format(intent=intent_branch, plan_id=plan_id), encoding="utf-8")
        _append_jsonl(
            work / "holon-knowledge/ledger/plans.jsonl",
            {
                "plan_id": plan_id,
                "intent_branch": intent_branch,
                "agent": agent,
                "model": model,
                "p_success": 0.8,
                "entropy": 2.0,
                "impact": 50.0,
                "cost": 5.0,
                "learning_value": 2.0,
                "ev": 35.4,
                "plan_file": plan_rel,
                "status": "proposed",
            },
        )
        self._push(work, plan_branch, f"plan: {plan_id} created by {agent} ({model})")
        return 0

    def _execute(self, work: Path, args: list[str], _kwargs: dict[str, Any]) -> int:
        plan_branch, agent, model = args
        exec_id = f"E-{self.now()}-{_sanitize_string(agent)}-{_sanitize_string(model)}"
        exec_branch = f"{plan_branch.removesuffix('/_')}/{exec_id}/_"
        _git(work, "fetch", "-q", "origin", plan_branch)
        _git(work, "checkout", "-q", "-B", exec_branch, "FETCH_HEAD")
        if self.executor_mode in ("change", "change_failure"):
            (work / "src" / "app.py").write_text("print('implemented')\n", encoding="utf-8")
        status = "failure" if self.executor_mode in ("agent_failure", "change_failure") else "success"
        row = {
            "entry_type": "summary",
            "execution_id": exec_id,
            "plan_branch": plan_branch,
            "agent": agent,
            "model": model,
            "status": status,
            "summary": "Plan executed successfully" if status == "success" else "Plan execution failed",
            "agent_output_truncated": False,
            "agent_output_bytes": 12,
            "ledger_revision": 1,
        }
        if self.recorded_test_pass_rate is not None:
            row["test_pass_rate"] = self.recorded_test_pass_rate
        if self.executor_mode == "empty":
            # Model a container that claims success with perfect tests while changing nothing.
            row["test_pass_rate"] = 1.0
        if self.executor_mode == "empty_no_row":
            # Model a container that pushed only bookkeeping (no ledger summary row, no effective change).
            (work / "plans").mkdir(exist_ok=True)
            (work / "plans" / f"{exec_id}_notes.md").write_text("notes\n", encoding="utf-8")
        else:
            _append_jsonl(work / "holon-knowledge/ledger/executions.jsonl", row)
        self._push(work, exec_branch, f"execute: {exec_id} completed for plan {plan_branch}")
        return 0

    def roles(self) -> list[str]:
        return [c["role"] for c in self.calls]


@pytest.fixture
def flow_repos(tmp_path, monkeypatch):
    """Bare origin with a seeded main branch, plus a host clone the flow runs in."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True, capture_output=True)
    _configure_identity(seed)
    for name in ("intents.jsonl", "plans.jsonl", "executions.jsonl"):
        (seed / "holon-knowledge" / "ledger").mkdir(parents=True, exist_ok=True)
        (seed / "holon-knowledge" / "ledger" / name).write_text("", encoding="utf-8")
    (seed / "src").mkdir()
    (seed / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "seed")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")
    host = tmp_path / "host"
    subprocess.run(["git", "clone", "-q", str(origin), str(host)], check=True, capture_output=True)
    _configure_identity(host)
    return host, origin


def _dispatch_context(host: Path, agents: FakeRoleAgents, **overrides: Any) -> FlowContext:
    params: dict[str, Any] = {
        "repo_dir": str(host),
        "intent_data": {"slug": "dispatch-test", "goal": "Change the application", "labels": ["flow"]},
        "agent": "claude-agent",
        "model": "claude-opus-5-5",
        "dry_run": False,
        "token_reduce": False,
        "skip_review": True,
        "skip_calibrate": True,
        "auto_calibrate": False,
        "dispatcher": agents,
    }
    params.update(overrides)
    return FlowContext(**params)


def _push_child_branch(tmp_path: Path, origin: Path, parent_branch: str, child_id: str) -> str:
    """Push '<parent>/<child_id>/_' to origin from a separate clone, as another (stale or concurrent) run would."""
    child_branch = f"{parent_branch.removesuffix('/_')}/{child_id}/_"
    work = tmp_path / f"other-{child_id}"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    _configure_identity(work)
    _git(work, "checkout", "-q", "-b", child_branch, f"origin/{parent_branch}")
    folder = "plans" if child_id.startswith("P-") else "src"
    (work / folder).mkdir(exist_ok=True)
    (work / folder / f"{child_id}.md").write_text("# Other run\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", f"other run {child_id}")
    _git(work, "push", "-q", "origin", child_branch)
    return child_branch


class TestFlowRoleDispatch:
    """Stages 1-3 dispatch the same role agents as the per-stage commands and never author content."""

    def test_pipeline_dispatches_intent_plan_execute_with_resolved_runner(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)

        results = PipelineEngine(ctx).run()

        assert agents.roles() == ["intent-creator", "planner", "executor"]
        for call in agents.calls:
            assert call["runner"] == "claude-agent"
            assert call["model"] == "claude-opus-5-5"
            assert call["repo_dir"] == str(host)
        assert agents.calls[1]["args"] == [ctx.intent_branch, "claude-agent", "claude-opus-5-5"]
        assert agents.calls[2]["args"] == [ctx.plan_branch, "claude-agent", "claude-opus-5-5"]
        assert results["intent"].status == StageStatus.SUCCESS
        assert results["plan"].status == StageStatus.SUCCESS
        assert results["execute"].status == StageStatus.SUCCESS
        assert ctx.plan_branch.startswith(ctx.intent_branch.removesuffix("/_") + "/P-")
        assert ctx.execution_branch.startswith(ctx.plan_branch.removesuffix("/_") + "/E-")
        assert results["execute"].payload["changed_files"] == 1
        # The container recorded no test result, so Stage 3 reports it as not measured rather than passed (C10-N1).
        assert results["execute"].payload["test_pass_rate"] is None

        # The flow authored nothing: every ledger row on origin came from a (simulated) container.
        exec_rows = _ledger_rows(host, f"origin/{ctx.execution_branch}", "executions.jsonl")
        assert len(exec_rows) == 1
        assert len(_ledger_rows(host, f"origin/{ctx.execution_branch}", "plans.jsonl")) == 1
        assert len(_ledger_rows(host, f"origin/{ctx.execution_branch}", "intents.jsonl")) == 1
        assert _git(host, "status", "--porcelain", "--untracked-files=no") == ""

    @pytest.mark.parametrize(
        ("recorded", "expected"), [(0.75, 0.75), (1, 1.0), (0.0, 0.0), ("1.0", None), (True, None)]
    )
    def test_stage3_carries_container_recorded_test_pass_rate(self, flow_repos, tmp_path, recorded, expected):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, recorded_test_pass_rate=recorded)
        ctx = _dispatch_context(host, agents)

        results = PipelineEngine(ctx).run()

        assert results["execute"].status == StageStatus.SUCCESS
        assert results["execute"].payload["test_pass_rate"] == expected

    def test_stage1_intent_dispatch_carries_resolved_payload(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(
            host,
            agents,
            intent_data={
                "slug": "Resolved Q",
                "goal": "Build stage 1",
                "questions": ["What protocol?"],
                "answers": {"What protocol?": "OAuth2"},
            },
        )

        res = run_intent_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert agents.roles() == ["intent-creator"]
        payload = agents.intent_payloads[0]
        assert payload["slug"] == "resolved-q"
        assert payload["branch"] == ctx.intent_branch.removesuffix("/_")
        assert payload["answers"] == {"What protocol?": "OAuth2"}
        rows = _ledger_rows(host, f"origin/{ctx.intent_branch}", "intents.jsonl")
        assert [r["slug"] for r in rows] == ["resolved-q"]
        # No local ledger write, and the temporary payload file is removed.
        assert (host / "holon-knowledge/ledger/intents.jsonl").read_text(encoding="utf-8") == ""
        assert not any((tmp_path / "home" / ".holon" / "tmp").iterdir())

    def test_stage1_reuses_existing_intent_branch_without_redispatch(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS

        again = _dispatch_context(host, agents, intent_data=dict(ctx.intent_data))
        assert run_intent_stage(again).status == StageStatus.SUCCESS
        assert agents.roles() == ["intent-creator"]

    def test_silent_planner_fails_plan_stage_without_fabricating(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, planner_writes_plan=False)
        ctx = _dispatch_context(host, agents)

        results = PipelineEngine(ctx).run()

        assert results["plan"].status == StageStatus.FAILED
        assert "refusing to fabricate a plan" in (results["plan"].error or "")
        assert ctx.intent_branch in (results["plan"].error or "")
        assert "execute" not in results
        assert agents.roles() == ["intent-creator", "planner"]
        assert ctx.plan_branch is None
        remote_branches = _git(host, "branch", "-r")
        assert "/P-" not in remote_branches
        assert not list(host.glob("plans/P-*.md"))
        assert (host / "holon-knowledge/ledger/plans.jsonl").read_text(encoding="utf-8") == ""

    def test_stage2_adopts_planner_branch_and_metrics(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS

        with patch("sandbox_executor.flow.converge_prettier") as mock_converge:
            res = run_plan_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        mock_converge.assert_not_called()  # The planner container owns formatting.
        assert res.payload["plan_file"] == f"plans/{res.payload['plan_id']}.md"
        assert res.payload["plan_id"].endswith("-claude-agent-claude-opus-5-5")
        assert res.payload["metrics"]["ev"] == pytest.approx(35.4)
        assert "Implementation step for" not in _git(host, "show", f"{ctx.plan_branch}:{res.payload['plan_file']}")
        assert len(_ledger_rows(host, ctx.plan_branch, "plans.jsonl")) == 1

    def test_stage2_ignores_stale_plan_branches(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, planner_writes_plan=False)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        # Plan branches pushed before dispatch (an old run, and one stamped "now") are not this run's plan.
        _push_child_branch(tmp_path, origin, ctx.intent_branch, "P-1000-claude-agent-claude-opus-5-5")
        _push_child_branch(tmp_path, origin, ctx.intent_branch, f"P-{int(time.time())}-claude-agent-claude-opus-5-5")

        res = run_plan_stage(ctx)

        assert res.status == StageStatus.FAILED  # Zero new branches appeared during dispatch.
        assert "refusing to fabricate a plan" in (res.error or "")
        assert ctx.plan_branch is None

    def test_stage2_adopts_plan_from_clock_skewed_container(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, clock_offset=-3600)  # Container clock an hour behind the host.
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS

        res = run_plan_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert int(res.payload["plan_id"].split("-")[1]) < int(time.time()) - 3000
        assert ctx.plan_branch == f"{ctx.intent_branch.removesuffix('/_')}/{res.payload['plan_id']}/_"

    def test_stage2_ignores_newer_sibling_present_before_dispatch(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        # A concurrent run with the same runner/model whose branch name carries a later timestamp.
        sibling = _push_child_branch(
            tmp_path, origin, ctx.intent_branch, f"P-{int(time.time()) + 600}-claude-agent-claude-opus-5-5"
        )

        res = run_plan_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert ctx.plan_branch != sibling
        assert ctx.plan_branch.endswith(f"/{res.payload['plan_id']}/_")
        assert int(res.payload["plan_id"].split("-")[1]) <= int(time.time())

    def test_stage2_fails_when_several_new_plan_branches_appear(self, flow_repos, tmp_path):
        host, origin = flow_repos

        def concurrent_planner(role: str, args: list[str]) -> None:
            if role == "planner":
                _push_child_branch(tmp_path, origin, args[0], "P-1-claude-agent-claude-opus-5-5")

        agents = FakeRoleAgents(tmp_path, origin, during_dispatch=concurrent_planner)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS

        res = run_plan_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "ambiguous P-branches" in (res.error or "")
        assert "refusing to guess" in (res.error or "")
        assert ctx.plan_branch is None

    def test_stage2_rejects_new_branch_from_another_runner(self, flow_repos, tmp_path):
        host, origin = flow_repos

        def other_runner(role: str, args: list[str]) -> None:
            if role == "planner":
                _push_child_branch(tmp_path, origin, args[0], f"P-{int(time.time())}-codex-agent-gpt-6")

        agents = FakeRoleAgents(tmp_path, origin, planner_writes_plan=False, during_dispatch=other_runner)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS

        res = run_plan_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "do not match the dispatched runner/model" in (res.error or "")
        assert ctx.plan_branch is None

    def test_stage3_fails_when_several_new_execution_branches_appear(self, flow_repos, tmp_path):
        host, origin = flow_repos

        def concurrent_executor(role: str, args: list[str]) -> None:
            if role == "executor":
                _push_child_branch(tmp_path, origin, args[0], "E-1-claude-agent-claude-opus-5-5")

        agents = FakeRoleAgents(tmp_path, origin, during_dispatch=concurrent_executor)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

        res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "ambiguous E-branches" in (res.error or "")
        assert ctx.execution_branch is None

    def test_stage3_adopts_execution_despite_preexisting_sibling(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, clock_offset=-120)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS
        sibling = _push_child_branch(
            tmp_path, origin, ctx.plan_branch, f"E-{int(time.time()) + 600}-claude-agent-claude-opus-5-5"
        )

        res = run_execute_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert ctx.execution_branch != sibling
        assert ctx.execution_branch.endswith(f"/{res.payload['execution_id']}/_")

    def test_stage2_fails_closed_when_origin_cannot_be_snapshotted(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS

        with patch("sandbox_executor.flow._fetch_branch_prefix", return_value=False):
            res = run_plan_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "before dispatching the planner" in (res.error or "")
        assert agents.roles() == ["intent-creator"]

    @staticmethod
    def _fail_fetch_after(agents: "FakeRoleAgents", role: str) -> Callable[..., subprocess.CompletedProcess[str]]:
        """Return a run_git stand-in whose fetches fail (with a secret in stderr) once ``role`` was dispatched."""
        from sandbox_executor import flow as flow_module

        real_run_git = flow_module.run_git

        def fake_run_git(args, *a, **kw):
            if args and args[0] == "fetch" and role in agents.roles():
                return subprocess.CompletedProcess(
                    ["git", *args], 128, "", "fatal: unable to access origin: token=ghp_supersecret123 expired"
                )
            return real_run_git(args, *a, **kw)

        return fake_run_git

    def test_stage1_fails_closed_when_pre_dispatch_fetch_fails(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)
        from sandbox_executor import flow as flow_module

        real_run_git = flow_module.run_git

        def fake_run_git(args, *a, **kw):
            if args and args[0] == "fetch" and str(args[-1]).startswith("+refs/heads/I-"):
                return subprocess.CompletedProcess(
                    ["git", *args], 128, "", "fatal: unable to access origin: token=ghp_supersecret123 expired"
                )
            return real_run_git(args, *a, **kw)

        with patch("sandbox_executor.flow.run_git", side_effect=fake_run_git):
            res = run_intent_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "before dispatching the intent creator" in (res.error or "")
        assert "ghp_supersecret123" not in (res.error or "")
        assert agents.roles() == []

    def test_stage1_reports_post_dispatch_fetch_failure(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)

        with patch("sandbox_executor.flow.run_git", side_effect=self._fail_fetch_after(agents, "intent-creator")):
            res = run_intent_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "intent-creator agent exited 0" in (res.error or "")
        assert "could not be fetched" in (res.error or "")
        assert "was not found on origin" not in (res.error or "")
        assert "ghp_supersecret123" not in (res.error or "")
        assert agents.roles() == ["intent-creator"]

    def test_stage2_reports_post_dispatch_fetch_failure(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS

        with patch("sandbox_executor.flow.run_git", side_effect=self._fail_fetch_after(agents, "planner")):
            res = run_plan_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "planner agent exited 0" in (res.error or "")
        assert "could not be fetched" in (res.error or "")
        assert "no planner agent produced" not in (res.error or "")
        assert "ghp_supersecret123" not in (res.error or "")
        assert ctx.plan_branch is None

    def test_stage3_reports_post_dispatch_fetch_failure(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

        with patch("sandbox_executor.flow.run_git", side_effect=self._fail_fetch_after(agents, "executor")):
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "executor agent exited 0" in (res.error or "")
        assert "could not be fetched" in (res.error or "")
        assert "no execution branch" not in (res.error or "")
        assert "ghp_supersecret123" not in (res.error or "")
        assert ctx.execution_branch is None

    def test_empty_execution_fails_records_failed_and_never_opens_pr(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, executor_mode="empty")
        ctx = _dispatch_context(host, agents, skip_review=False)
        real_run = subprocess.run
        gh_calls: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd and cmd[0] == "gh":
                gh_calls.append(list(cmd))
                return MagicMock(returncode=1, stdout="", stderr="gh disabled in tests")
            return real_run(cmd, *args, **kwargs)

        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run", side_effect=fake_run),
            patch("sandbox_executor.flow.create_or_update_pr") as mock_pr,
        ):
            results = PipelineEngine(ctx).run()

        assert results["execute"].status == StageStatus.FAILED
        assert "no changes outside holon-knowledge/ledger/, plans/ and executions/" in (results["execute"].error or "")
        assert results["execute"].payload["status"] == "failed"
        assert results["execute"].payload["superseding_row_recorded"] is True
        assert "review" not in results
        mock_pr.assert_not_called()
        assert not any(call[:3] == ["gh", "pr", "create"] for call in gh_calls)

        rows = _ledger_rows(host, f"origin/{ctx.execution_branch}", "executions.jsonl")
        exec_id = results["execute"].payload["execution_id"]
        exec_rows = [r for r in rows if r.get("execution_id") == exec_id]
        assert [r["status"] for r in exec_rows] == ["success", "failed"]
        latest = exec_rows[-1]
        assert latest["ledger_revision"] == 2
        assert latest["failure_reason"] == "empty_diff"
        assert "test_pass_rate" not in latest
        assert latest["agent_output_bytes"] == 12  # Authoritative fields are repeated, not merged backwards.
        # Append-only: the container's original row is untouched.
        assert exec_rows[0]["test_pass_rate"] == 1.0
        error = results["execute"].error or ""
        assert "exited with code 0" in error
        assert "container recorded status 'success'; recorded a superseding status 'failed' row" in error
        # The local execution branch is fast-forwarded to the pushed superseding row (C6-N1).
        local_rows = _ledger_rows(host, ctx.execution_branch, "executions.jsonl")
        assert [r["status"] for r in local_rows if r.get("execution_id") == exec_id] == ["success", "failed"]
        assert _git(host, "rev-parse", ctx.execution_branch) == _git(
            host, "rev-parse", f"origin/{ctx.execution_branch}"
        )
        assert _git(host, "ls-remote", str(origin), ctx.execution_branch).split()[0] == _git(
            host, "rev-parse", ctx.execution_branch
        )
        assert _git(host, "status", "--porcelain", "--untracked-files=no") == ""
        assert _git(host, "worktree", "list").count("\n") == 0

    def test_empty_execution_already_failed_is_not_duplicated(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, executor_mode="empty")
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS
        agents.executor_mode = "agent_failure"

        res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        rows = _ledger_rows(host, f"origin/{ctx.execution_branch}", "executions.jsonl")
        assert [r["status"] for r in rows] == ["failure"]
        error = res.error or ""
        assert "container already recorded status 'failure': Plan execution failed" in error
        assert "no superseding row was written" in error
        assert "recorded a superseding" not in error
        assert res.payload["container_status"] == "failure"
        assert res.payload["superseding_row_recorded"] is False

    def test_empty_execution_without_summary_row_reports_missing_row(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, executor_mode="empty_no_row")
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

        res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        error = res.error or ""
        assert "carries no executions.jsonl summary row" in error
        assert "so no superseding row was recorded" in error
        assert "recorded a superseding" not in error
        assert res.payload["container_status"] is None
        assert res.payload["superseding_row_recorded"] is False
        assert _ledger_rows(host, f"origin/{ctx.execution_branch}", "executions.jsonl") == []

    def test_empty_execution_reports_failed_superseding_write(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, executor_mode="empty")
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS
        from sandbox_executor import flow as flow_module

        real_run_git = flow_module.run_git

        def fake_run_git(args, *a, **kw):
            if args and args[0] == "push":
                raise RuntimeError("push rejected: token=ghp_supersecret123 expired")
            return real_run_git(args, *a, **kw)

        with patch("sandbox_executor.flow.run_git", side_effect=fake_run_git):
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        error = res.error or ""
        assert "recording the superseding 'failed' row (failure_reason 'empty_diff') failed" in error
        assert "the ledger still shows 'success'" in error
        assert "push rejected" in error
        assert "ghp_supersecret123" not in error
        assert res.payload["superseding_row_recorded"] is False
        rows = _ledger_rows(host, f"origin/{ctx.execution_branch}", "executions.jsonl")
        assert [r["status"] for r in rows] == ["success"]
        assert _ledger_rows(host, ctx.execution_branch, "executions.jsonl") == rows

    def test_superseding_row_leaves_diverged_local_branch_untouched(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, executor_mode="empty")
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS
        from sandbox_executor import flow as flow_module

        real_track = flow_module._track_local_branch

        def diverge_local(context, branch):
            real_track(context, branch)
            if "/E-" in branch:
                tree = _git(host, "rev-parse", f"{branch}^{{tree}}")
                commit = _git(host, "commit-tree", tree, "-p", branch, "-m", "local operator commit")
                _git(host, "update-ref", f"refs/heads/{branch}", commit)

        with patch("sandbox_executor.flow._track_local_branch", side_effect=diverge_local):
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert res.payload["superseding_row_recorded"] is True
        assert _git(host, "log", "-1", "--format=%s", ctx.execution_branch) == "local operator commit"
        assert any("has diverged from origin" in line for line in ctx.logs)
        rows = _ledger_rows(host, f"origin/{ctx.execution_branch}", "executions.jsonl")
        assert [r["status"] for r in rows] == ["success", "failed"]

    def test_track_local_branch_keeps_unpushed_local_commits(self, flow_repos):
        host, _origin = flow_repos
        from sandbox_executor import flow as flow_module

        branch = "I-1-reused/_"
        _git(host, "branch", branch, "main")
        _git(host, "push", "-q", "origin", branch)
        _git(host, "fetch", "-q", "origin")
        tree = _git(host, "rev-parse", f"{branch}^{{tree}}")
        local_commit = _git(host, "commit-tree", tree, "-p", branch, "-m", "unpushed local commit")
        _git(host, "update-ref", f"refs/heads/{branch}", local_commit)
        ctx = FlowContext(repo_dir=str(host), token_reduce=False)

        flow_module._track_local_branch(ctx, branch)

        assert _git(host, "rev-parse", f"refs/heads/{branch}") == local_commit
        assert any("has diverged from origin" in line for line in ctx.logs)

    def test_track_local_branch_fast_forwards_a_behind_local_branch(self, flow_repos, tmp_path):
        host, origin = flow_repos
        from sandbox_executor import flow as flow_module

        branch = "I-1-reused/_"
        _git(host, "branch", branch, "main")
        _git(host, "push", "-q", "origin", branch)
        other = tmp_path / "other-ff"
        subprocess.run(["git", "clone", "-q", "-b", branch, str(origin), str(other)], check=True, capture_output=True)
        _configure_identity(other)
        _git(other, "commit", "-q", "--allow-empty", "-m", "pushed by the container")
        _git(other, "push", "-q", "origin", branch)
        _git(host, "fetch", "-q", "origin")
        ctx = FlowContext(repo_dir=str(host), token_reduce=False)

        flow_module._track_local_branch(ctx, branch)

        assert _git(host, "rev-parse", f"refs/heads/{branch}") == _git(host, "rev-parse", f"origin/{branch}")
        assert not any("Warning" in line for line in ctx.logs)

    def test_flow_auto_detects_token_reduction_but_manual_commands_require_opt_in(self, monkeypatch):
        """Documented limit of the equivalence guarantee: only 'holon flow' auto-detects holon-coherence."""
        from sandbox_executor import flow as flow_module

        monkeypatch.delenv("HOLON_TOKEN_REDUCE", raising=False)
        monkeypatch.delenv("HOLON_PASSIVE_MONITORING", raising=False)
        with patch("sandbox_executor.cli.detect_holon_coherence", return_value=True):
            dispatcher = MagicMock(return_value=0)
            ctx = FlowContext(repo_dir=".", dispatcher=dispatcher)
            flow_module._dispatch_role(ctx, "planner", ["I-1-x/_"])
            assert dispatcher.call_args.kwargs["token_reduce"] is True

            with (
                patch("sandbox_executor.cli.dispatch_role_agent", return_value=0) as mock_dispatch,
                patch("sys.argv", ["holon", "plan", "I-1-x/_"]),
                pytest.raises(SystemExit),
            ):
                cli_main()
            assert mock_dispatch.call_args.kwargs["token_reduce"] is False

    def test_stage3_agent_failure_status_fails_stage(self, flow_repos, tmp_path):
        host, origin = flow_repos
        # The failed agent still edited a file, so the failure comes from the container's own status.
        agents = FakeRoleAgents(tmp_path, origin, executor_mode="change_failure")
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

        res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "recorded status 'failure'" in (res.error or "")
        rows = _ledger_rows(host, f"origin/{ctx.execution_branch}", "executions.jsonl")
        assert [r["status"] for r in rows] == ["failure"]

    def test_stage3_nonzero_container_exit_fails_stage(self, flow_repos):
        host, _origin = flow_repos
        ctx = _dispatch_context(host, MagicMock(return_value=3), plan_branch="I-1-x/P-1-claude-agent-m/_")

        res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "exited with code 3" in (res.error or "")
        assert res.payload["exit_code"] == 3

    def test_skip_push_is_rejected_before_dispatch(self, flow_repos):
        host, _origin = flow_repos
        dispatcher = MagicMock(return_value=0)
        ctx = _dispatch_context(host, dispatcher, skip_push=True)

        results = PipelineEngine(ctx).run()

        assert results["intent"].status == StageStatus.FAILED
        assert results["intent"].error == SKIP_PUSH_DISPATCH_ERROR
        dispatcher.assert_not_called()

        for stage_fn, extra in (
            (run_plan_stage, {"intent_branch": "I-1-x/_"}),
            (run_execute_stage, {"plan_branch": "I-1-x/P-1-a-b/_"}),
        ):
            stage_ctx = _dispatch_context(host, dispatcher, skip_push=True, **extra)
            res = stage_fn(stage_ctx)
            assert res.status == StageStatus.FAILED
            assert res.error == SKIP_PUSH_DISPATCH_ERROR
        dispatcher.assert_not_called()

    def test_dry_run_never_dispatches(self, tmp_path):
        dispatcher = MagicMock(return_value=0)
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "dry", "goal": "Dry run"},
            dry_run=True,
            skip_push=True,
            dispatcher=dispatcher,
        )
        results = PipelineEngine(ctx).run()
        assert results["execute"].status == StageStatus.SUCCESS
        dispatcher.assert_not_called()

    def test_default_dispatcher_is_cli_dispatch_role_agent(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, None)
        with patch("sandbox_executor.cli.dispatch_role_agent", side_effect=agents) as mock_dispatch:
            res = run_intent_stage(ctx)
        assert res.status == StageStatus.SUCCESS
        mock_dispatch.assert_called_once()
        assert mock_dispatch.call_args.args[:3] == ("intent-creator", "claude-agent", "claude-opus-5-5")

    def test_checkpoint_does_not_serialise_dispatcher(self, tmp_path):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "cp-dispatch", "goal": "Checkpoint"},
            dispatcher=MagicMock(return_value=0),
            token_reduce=False,
        )
        assert "dispatcher" not in ctx.to_dict()
        saved = save_checkpoint(ctx, str(tmp_path / "checkpoint.json"))
        with open(saved, encoding="utf-8") as f:
            assert "dispatcher" not in f.read()
        assert load_checkpoint(saved).context.dispatcher is None


# ---------------------------------------------------------------------------
# Stage 4 ledger provenance regression tests
# ---------------------------------------------------------------------------

REAL_EXECUTIONS_LEDGER = Path(__file__).resolve().parents[3] / "holon-knowledge" / "ledger" / "executions.jsonl"

# Historical row shapes for each Stage 4 record type, as found in the canonical ledger.
HISTORICAL_REVIEW_KEYS = [
    "action_id",
    "execution_id",
    "action",
    "category",
    "target",
    "outcome",
    "entry_type",
    "action_origin",
    "review_severity",
    "review_iteration",
    "review_finding_ids",
    "labels",
    "error_context",
    "created_at",
]
HISTORICAL_RESOLVER_KEYS = [
    "action_id",
    "execution_id",
    "action",
    "category",
    "target",
    "outcome",
    "entry_type",
    "action_origin",
    "review_iteration",
    "labels",
    "created_at",
]
HISTORICAL_KEYS_BY_PREFIX = {
    "ACT-REV": HISTORICAL_REVIEW_KEYS,
    "ACT-FIND": HISTORICAL_REVIEW_KEYS,
    "ACT-RES": HISTORICAL_RESOLVER_KEYS,
    "ACT-POST": HISTORICAL_RESOLVER_KEYS,
}
EXECUTION_ID_PATTERNS = {
    "ACT-REV": r"^REV-ITR-\d+-\d+$",
    "ACT-FIND": r"^REV-FIND-\d+-\d+$",
    "ACT-RES": r"^REV-RES-\d+-\d+$",
    "ACT-POST": r"^REV-POST-\d+$",
}


def _ledger_fingerprint(path: Path) -> tuple[int, str]:
    """Return the line count and SHA-256 digest of a ledger file (or a sentinel if absent)."""
    import hashlib

    if not path.exists():
        return (-1, "")
    data = path.read_bytes()
    return (data.count(b"\n"), hashlib.sha256(data).hexdigest())


def _read_rows(ledger: Path) -> list[dict]:
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]


def _prefix(row: dict) -> str:
    return str(row.get("action_id", "")).rsplit("-", 2)[0]


class TestStage4LedgerProvenance:
    """Stage 4 review loop must append validated, byte-compatible ACT-* rows to executions.jsonl."""

    @pytest.fixture(autouse=True)
    def _guard_real_ledger(self):
        """Fail if any test touches the repository's own canonical execution ledger."""
        before = _ledger_fingerprint(REAL_EXECUTIONS_LEDGER)
        yield
        assert _ledger_fingerprint(REAL_EXECUTIONS_LEDGER) == before, "Real executions.jsonl was modified"

    def _run_two_iteration_loop(self, tmp_path, posting_side_effect=None, posting_return=None):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            dry_run=False,
        )
        # The local-tests fallback only passes on a recorded Stage 3 test result (C10-N1).
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"test_pass_rate": 1.0, "exit_code": 0}
        )
        mergeability = iter([{"is_dirty": True}, {"is_dirty": False}])
        if posting_side_effect:
            posting: dict = {"side_effect": posting_side_effect}
        elif posting_return is not None:
            posting = {"return_value": posting_return}
        else:
            posting = {"return_value": {"dual_posted": True, "official_review": {"success": True}}}
        with (
            patch("sandbox_executor.flow.shutil.which", return_value=None),
            patch("sandbox_executor.flow.check_pr_mergeability", side_effect=lambda *a, **k: next(mergeability)),
            patch(
                "sandbox_executor.flow.probe_ci_checks",
                return_value={"all_passed": True, "source": "local_tests"},
            ),
            patch("sandbox_executor.flow.post_pr_review_consensus", **posting),
        ):
            result = run_review_loop(ctx, auto_pr=False)
        return ctx, result, tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"

    def test_review_loop_appends_act_rows_to_target_repository(self, tmp_path):
        _, result, ledger = self._run_two_iteration_loop(tmp_path)

        assert result["consensus"]["approved"] is True
        action_rows = [r for r in _read_rows(ledger) if _prefix(r) in HISTORICAL_KEYS_BY_PREFIX]
        assert [_prefix(r) for r in action_rows] == ["ACT-REV", "ACT-FIND", "ACT-RES", "ACT-REV", "ACT-POST"]
        for row in action_rows:
            assert row["entry_type"] == "action"
            assert row["action_origin"] == "pr_review"
        timestamps = [r["created_at"] for r in action_rows]
        assert timestamps == sorted(timestamps)
        assert action_rows[-1]["outcome"] == "success"

        ledger_dir = ledger.parent
        assert sorted(p.name for p in ledger_dir.iterdir() if p.suffix == ".jsonl") == ["executions.jsonl"]
        assert not (ledger_dir / "actions.jsonl").exists()
        assert not (ledger_dir / "reviews.jsonl").exists()

    def test_review_rows_are_byte_compatible_with_historical_shapes(self, tmp_path):
        import re

        _, _, ledger = self._run_two_iteration_loop(tmp_path)

        raw_lines = [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        seen: set[str] = set()
        for raw in raw_lines:
            row = json.loads(raw)
            prefix = _prefix(row)
            if prefix not in HISTORICAL_KEYS_BY_PREFIX:
                continue
            seen.add(prefix)
            assert list(row.keys()) == HISTORICAL_KEYS_BY_PREFIX[prefix], prefix
            assert re.match(EXECUTION_ID_PATTERNS[prefix], row["execution_id"]), row["execution_id"]
            # Serialised exactly as json.dumps would have written the literal.
            assert raw == json.dumps(row)
        assert seen == set(HISTORICAL_KEYS_BY_PREFIX)

    def test_act_post_recorded_when_posting_raises(self, tmp_path, caplog):
        caplog.set_level("WARNING", logger="sandbox_executor.flow")
        ctx, result, ledger = self._run_two_iteration_loop(tmp_path, posting_side_effect=RuntimeError("gh exploded"))

        post_rows = [r for r in _read_rows(ledger) if _prefix(r) == "ACT-POST"]
        assert len(post_rows) == 1
        assert post_rows[0]["outcome"] == "failure"
        assert post_rows[0]["error_context"] == "gh exploded"
        assert "dual-posted" not in post_rows[0]["action"]
        assert "posting to GitHub failed" in post_rows[0]["action"]
        assert list(post_rows[0].keys()) == [*HISTORICAL_RESOLVER_KEYS[:-1], "error_context", "created_at"]
        assert result["posting"] == {"error": "gh exploded"}
        assert any("gh exploded" in rec.getMessage() for rec in caplog.records if rec.levelname == "WARNING")
        assert any("gh exploded" in line for line in ctx.logs)

    def test_act_post_redacts_credentials_when_posting_raises(self, tmp_path, caplog):
        caplog.set_level("WARNING", logger="sandbox_executor.flow")
        sample_value = "ghp_" + "Q" * 36
        ctx, result, ledger = self._run_two_iteration_loop(
            tmp_path, posting_side_effect=RuntimeError(f"gh exploded using {sample_value}")
        )

        post_rows = [r for r in _read_rows(ledger) if _prefix(r) == "ACT-POST"]
        assert len(post_rows) == 1
        assert post_rows[0]["error_context"] == "gh exploded using [REDACTED_SECRET]"
        assert sample_value not in ledger.read_text(encoding="utf-8")
        assert sample_value not in str(result["posting"])
        assert all(sample_value not in rec.getMessage() for rec in caplog.records)
        assert all(sample_value not in line for line in ctx.logs)

    def test_act_post_records_returned_failure_diagnostics(self, tmp_path):
        sample_value = "ghp_" + "Z" * 36
        returned_failure = {
            "dual_posted": False,
            "diagnostics": "Review posting partial/failed: comment=True, review=False.",
            "discussion_comment": {"attempted": True, "success": True, "output": "posted"},
            "official_review": {
                "attempted": True,
                "success": False,
                "output": f"Official review submission failed (exit code 1): bad credentials {sample_value}",
            },
        }
        _, _, ledger = self._run_two_iteration_loop(tmp_path, posting_return=returned_failure)

        post_rows = [r for r in _read_rows(ledger) if _prefix(r) == "ACT-POST"]
        assert len(post_rows) == 1
        error_context = post_rows[0]["error_context"]
        assert post_rows[0]["outcome"] == "failure"
        assert "Review posting partial/failed: comment=True, review=False." in error_context
        assert "official_review: Official review submission failed (exit code 1)" in error_context
        assert "[REDACTED_SECRET]" in error_context
        assert "discussion_comment" not in error_context
        assert sample_value not in ledger.read_text(encoding="utf-8")

    def test_act_post_error_context_redacts_before_truncating(self, tmp_path):
        from sandbox_executor.flow import POSTING_ERROR_CONTEXT_MAX_CHARS

        sample_value = "ghp_" + "K" * 36
        padding = "x" * (POSTING_ERROR_CONTEXT_MAX_CHARS - 40)
        returned_failure = {
            "dual_posted": False,
            "official_review": {"success": False, "output": f"{padding} {sample_value} trailing detail"},
        }
        _, _, ledger = self._run_two_iteration_loop(tmp_path, posting_return=returned_failure)

        error_context = next(r for r in _read_rows(ledger) if _prefix(r) == "ACT-POST")["error_context"]
        assert len(error_context) <= POSTING_ERROR_CONTEXT_MAX_CHARS
        assert "ghp_" not in error_context
        assert "ghp_" not in ledger.read_text(encoding="utf-8")

    def test_dry_run_suppresses_appends_visibly(self, tmp_path):
        ctx = FlowContext(repo_dir=str(tmp_path), execution_branch="I-1/P-2/E-3/_", dry_run=True)
        with (
            patch("sandbox_executor.flow.shutil.which", return_value=None),
            patch("sandbox_executor.flow.check_pr_mergeability", return_value={"is_dirty": False}),
            patch(
                "sandbox_executor.flow.probe_ci_checks",
                return_value={"all_passed": True, "source": "local_tests"},
            ),
        ):
            run_review_loop(ctx, auto_pr=False)

        assert not (tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl").exists()
        assert any("Dry run" in line and "ledger record(s) not appended" in line for line in ctx.logs)

    def test_unwritable_ledger_warns_and_does_not_raise(self, tmp_path, caplog):
        caplog.set_level("WARNING")
        ctx = FlowContext(repo_dir=str(tmp_path), execution_branch="I-1/P-2/E-3/_", dry_run=False)
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"test_pass_rate": 1.0, "exit_code": 0}
        )
        ledger_path = str(tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl")
        with (
            patch("sandbox_executor.flow.shutil.which", return_value=None),
            patch("sandbox_executor.flow.check_pr_mergeability", return_value={"is_dirty": False}),
            patch(
                "sandbox_executor.flow.probe_ci_checks",
                return_value={"all_passed": True, "source": "local_tests"},
            ),
            patch("sandbox_executor.flow.post_pr_review_consensus", return_value={"dual_posted": True}),
            patch("sandbox_executor.ledger.LedgerLock.__enter__", side_effect=PermissionError("ledger denied")),
            patch("sandbox_executor.knowledge.records.atomic_ledger_append", side_effect=PermissionError("denied")),
        ):
            result = run_review_loop(ctx, auto_pr=False)

        assert result["consensus"]["approved"] is True
        warnings = [rec.getMessage() for rec in caplog.records if rec.levelname == "WARNING"]
        assert any(ledger_path in msg and "ledger denied" in msg and "ACT-REV" in msg for msg in warnings)
        assert any(ledger_path in msg and "ACT-POST" in msg for msg in warnings)
        assert any("review summary" in msg for msg in warnings)
        assert any(ledger_path in line and "ledger denied" in line for line in ctx.logs)
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        assert not ledger_dir.exists() or not any(ledger_dir.iterdir())

    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores file permissions")
    def test_read_only_ledger_directory_warns_and_does_not_raise(self, tmp_path, caplog):
        caplog.set_level("WARNING")
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)
        ledger_dir.chmod(0o555)
        ctx = FlowContext(repo_dir=str(tmp_path), execution_branch="I-1/P-2/E-3/_", dry_run=False)
        try:
            with (
                patch("sandbox_executor.flow.shutil.which", return_value=None),
                patch("sandbox_executor.flow.check_pr_mergeability", return_value={"is_dirty": False}),
                patch(
                    "sandbox_executor.flow.probe_ci_checks",
                    return_value={"all_passed": True, "source": "local_tests"},
                ),
                patch("sandbox_executor.flow.post_pr_review_consensus", return_value={"dual_posted": True}),
            ):
                run_review_loop(ctx, auto_pr=False)
        finally:
            ledger_dir.chmod(0o755)

        warnings = [rec.getMessage() for rec in caplog.records if rec.levelname == "WARNING"]
        assert any(str(ledger_dir / "executions.jsonl") in msg for msg in warnings)
        assert list(ledger_dir.iterdir()) == []

    def test_record_review_action_rejects_invalid_record_without_writing(self, tmp_path, caplog):
        from sandbox_executor.flow import record_review_action

        caplog.set_level("WARNING")
        ctx = FlowContext(repo_dir=str(tmp_path))
        ledger = tmp_path / "executions.jsonl"
        bad = {
            "action_id": "ACT-REV-1-deadbeef",
            "execution_id": "REV-ITR-1-1",
            "action": "x",
            "category": "not-a-category",
            "target": "t",
            "outcome": "success",
        }
        assert record_review_action(ctx, str(ledger), bad) is None
        assert not ledger.exists()
        assert any("not-a-category" in rec.getMessage() for rec in caplog.records if rec.levelname == "WARNING")


# Identity and signing overrides so fixture commits never depend on the host's global git configuration.
_TEST_GIT_CONFIG = [
    "-c",
    "user.name=Holon Test",
    "-c",
    "user.email=test@holon.invalid",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "tag.gpgsign=false",
]


def _git(repo: Path, *args: str) -> str:
    """Run a git command in a fixture repository and return its stripped stdout."""
    proc = subprocess.run(
        ["git", *_TEST_GIT_CONFIG, *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _init_mergeability_repo(repo: Path) -> Path:
    """Build a local repository (no origin remote) for real merge-tree checks.

    Layout:
        main:             shared.txt edited to "main change" after the branches fork.
        clean-feature:    adds feature.txt only, so it merges cleanly into main.
        conflict-feature: edits the same line of shared.txt differently, so it conflicts with main.

    The repository is left with main checked out.
    """
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "shared.txt").write_text("base line\n", encoding="utf-8")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-q", "-m", "base")

    _git(repo, "checkout", "-q", "-b", "clean-feature")
    (repo / "feature.txt").write_text("feature only\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-q", "-m", "clean feature")

    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", "conflict-feature")
    (repo / "shared.txt").write_text("feature change\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "conflicting feature")

    _git(repo, "checkout", "-q", "main")
    (repo / "shared.txt").write_text("main change\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "main change")
    return repo


class TestMergeabilityRefResolution:
    """Real-repository tests: unresolvable refs are reported as MISSING_REF, never as merge conflicts."""

    @pytest.fixture
    def repo(self, tmp_path: Path) -> Path:
        return _init_mergeability_repo(tmp_path / "repo")

    @pytest.fixture(autouse=True)
    def _no_gh(self):
        # Force the local git path so no test can reach GitHub over the network.
        with patch("shutil.which", return_value=None) as which:
            yield which

    def test_missing_branch_ref_yields_missing_ref(self, repo: Path):
        res = check_pr_mergeability(str(repo), "does-not-exist", "main")
        assert res["source"] == "git_local"
        assert res["merge_state_status"] == "MISSING_REF"
        assert res["mergeable"] == "UNKNOWN"
        assert res["is_dirty"] is False
        assert "does-not-exist" in res["diagnostics"]["missing_ref"]
        assert res["diagnostics"]["ref_role"] == "branch"
        assert res["diagnostics"]["candidates_tried"] == ["does-not-exist", "origin/does-not-exist"]

    def test_missing_target_ref_yields_missing_ref(self, repo: Path):
        res = check_pr_mergeability(str(repo), "clean-feature", "no-such-target")
        assert res["merge_state_status"] == "MISSING_REF"
        assert res["mergeable"] == "UNKNOWN"
        assert res["is_dirty"] is False
        assert res["diagnostics"]["ref_role"] == "target"
        assert res["diagnostics"]["missing_ref"] == "no-such-target"
        assert "cannot evaluate mergeability for ref no-such-target" in res["diagnostics"]["message"]

    def test_genuine_conflict_yields_conflicting_dirty(self, repo: Path):
        res = check_pr_mergeability(str(repo), "conflict-feature", "main")
        assert res["mergeable"] == "CONFLICTING"
        assert res["merge_state_status"] == "DIRTY"
        assert res["is_dirty"] is True

    def test_clean_branch_yields_mergeable_clean(self, repo: Path):
        res = check_pr_mergeability(str(repo), "clean-feature", "main")
        assert res["mergeable"] == "MERGEABLE"
        assert res["merge_state_status"] == "CLEAN"
        assert res["is_dirty"] is False

    def test_review_loop_reports_missing_ref_without_find_dirty(self, repo: Path):
        ctx = FlowContext(repo_dir=str(repo), intent_data={"target_branch": "main"}, dry_run=True, token_reduce=False)
        ci_passed = {"source": "local_tests", "all_passed": True, "passed": 1, "failed": 0, "pending": 0}
        with patch("sandbox_executor.flow.probe_ci_checks", return_value=ci_passed):
            res = run_review_loop(ctx, target="does-not-exist", max_iterations=3, dry_run=True)

        assert not any(fid.startswith("FIND-DIRTY") for fid in res["findings"])
        assert res["findings"] == ["FIND-REF-001"]
        assert res["consensus"]["approved"] is False
        assert res["consensus"]["status"] != "conflicted_dirty"
        assert res["mergeability"]["merge_state_status"] == "MISSING_REF"
        # A ref that does not resolve will not resolve on the next pass either, so the loop stops early.
        assert res["total_iterations"] == 1
        assert any("cannot evaluate mergeability for ref does-not-exist" in line for line in ctx.logs)
        assert not any("CI SIGNAL SUPPRESSED" in line for line in ctx.logs)

        # The loop's own trailers must carry the new finding id through the round trip used by ledger consumers.
        assert res["severity"] == "important"
        trailers = parse_commit_trailers(res["trailers"])
        assert trailers["review_finding_ids"] == ["FIND-REF-001"]
        assert trailers["review_severity"] == "important"
        assert trailers["review_iteration"] == 1

    def test_consensus_report_renders_mergeability_note(self):
        note = "cannot evaluate mergeability for ref does-not-exist against main: ref could not be resolved."
        consensus = {
            "approved": False,
            "status": "rejected",
            "findings": ["FIND-REF-001"],
            "mergeability_note": note,
        }
        body = build_pr_review_consensus_report(".", "does-not-exist", consensus)
        assert "**Finding `FIND-REF-001`**" in body
        assert note in body

        without_note = build_pr_review_consensus_report(".", "does-not-exist", {**consensus, "mergeability_note": None})
        assert "cannot evaluate mergeability" not in without_note

    def test_review_stage_reports_missing_ref_instead_of_ci_signal_suppressed(self):
        context = FlowContext(
            repo_dir=".",
            execution_branch="I-100/P-100/E-100",
            plan_branch="I-100/P-100",
            intent_branch="I-100",
            dry_run=True,
            token_reduce=False,
        )
        context.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"test_pass_rate": 1.0, "exit_code": 0},
        )
        missing_ref = {
            "mergeable": "UNKNOWN",
            "merge_state_status": "MISSING_REF",
            "is_dirty": False,
            "source": "git_local",
            "pr_number": None,
            "status_check_rollup": None,
            "diagnostics": {
                "missing_ref": "origin/I-100/P-100/E-100",
                "ref_role": "branch",
                "candidates_tried": ["I-100/P-100/E-100", "origin/I-100/P-100/E-100"],
                "stderr": "fatal: Needed a single revision",
                "message": "cannot evaluate mergeability for ref origin/I-100/P-100/E-100",
            },
        }
        with patch("sandbox_executor.flow.check_pr_mergeability", return_value=missing_ref):
            result = run_review_stage(context)

        assert result.status == StageStatus.FAILED
        assert result.payload["consensus"]["approved"] is False
        assert result.error is not None
        assert "CI SIGNAL SUPPRESSED" not in result.error
        assert "cannot evaluate mergeability for ref origin/I-100/P-100/E-100" in result.error
        assert "MISSING_REF" in result.error

    def test_non_repository_yields_unknown_not_missing_ref(self, tmp_path: Path):
        # Outside a repository no ref can resolve, so blaming a specific ref would be a misdiagnosis.
        res = check_pr_mergeability(str(tmp_path), "feature", "main")
        assert res["mergeable"] == "UNKNOWN"
        assert res["merge_state_status"] == "UNKNOWN"
        assert res["is_dirty"] is False
        assert "not a git repository" in res["diagnostics"]["error"]

    # Exploratory probes (plan Step 4): does "exit 1 means conflict" hold once both refs resolve?

    def test_unrelated_histories_yield_unknown_with_stderr(self, repo: Path):
        _git(repo, "checkout", "-q", "--orphan", "orphan")
        _git(repo, "rm", "-rfq", ".")
        (repo / "orphan.txt").write_text("orphan\n", encoding="utf-8")
        _git(repo, "add", "orphan.txt")
        _git(repo, "commit", "-q", "-m", "orphan root")
        _git(repo, "checkout", "-q", "-f", "main")

        res = check_pr_mergeability(str(repo), "orphan", "main")
        assert res["mergeable"] == "UNKNOWN"
        assert res["merge_state_status"] == "UNKNOWN"
        assert res["is_dirty"] is False
        assert res["diagnostics"]["merge_tree_exit_code"] not in (0, 1)
        assert res["diagnostics"]["stderr"]

    def test_annotated_tag_is_peeled_to_commit(self, repo: Path):
        _git(repo, "tag", "-a", "v-clean", "-m", "annotated tag", "clean-feature")
        res = check_pr_mergeability(str(repo), "v-clean", "main")
        assert res["mergeable"] == "MERGEABLE"
        assert res["merge_state_status"] == "CLEAN"

    def test_tree_object_ref_yields_missing_ref(self, repo: Path):
        tree_sha = _git(repo, "rev-parse", "main^{tree}")
        res = check_pr_mergeability(str(repo), tree_sha, "main")
        assert res["merge_state_status"] == "MISSING_REF"
        assert res["is_dirty"] is False
        assert res["diagnostics"]["ref_role"] == "branch"

    def test_empty_branch_resolves_to_head(self, repo: Path):
        _git(repo, "checkout", "-q", "clean-feature")
        res = check_pr_mergeability(str(repo), "", "main")
        assert res["mergeable"] == "MERGEABLE"
        assert res["merge_state_status"] == "CLEAN"


class TestStageTargetsIgnoreOperatorHead:
    """Stages 1-3 no longer move the operator's checkout, so later stages must name their branch explicitly."""

    @pytest.fixture(autouse=True)
    def _no_gh(self):
        # Force the local git path so no test can reach GitHub over the network.
        with patch("shutil.which", return_value=None) as which:
            yield which

    @staticmethod
    def _run_stages_1_to_3(host: Path, origin: Path, tmp_path: Path) -> FlowContext:
        ctx = _dispatch_context(host, FakeRoleAgents(tmp_path, origin))
        results = PipelineEngine(ctx).run()
        assert results["execute"].status == StageStatus.SUCCESS
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == "main"
        return ctx

    @staticmethod
    def _run_loop_capturing_consensus(ctx: FlowContext, target: str) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        def _capture(**kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs["consensus_data"])
            return {"dual_posted": True}

        with (
            patch("sandbox_executor.flow.post_pr_review_consensus", side_effect=_capture),
            patch("sandbox_executor.flow.probe_ci_checks", return_value={"all_passed": True}),
        ):
            run_review_loop(ctx, target=target, max_iterations=1, auto_pr=False)
        return captured

    def test_review_target_commit_is_execution_tip_not_operator_head(self, flow_repos, tmp_path):
        host, origin = flow_repos
        ctx = self._run_stages_1_to_3(host, origin, tmp_path)
        exec_tip = _git(host, "rev-parse", "--short", f"refs/remotes/origin/{ctx.execution_branch}")
        assert exec_tip != _git(host, "rev-parse", "--short", "HEAD")

        consensus = self._run_loop_capturing_consensus(ctx, ctx.execution_branch)

        assert consensus["target_commit"] == exec_tip

    def test_review_target_commit_follows_origin_when_local_refs_are_stale(self, flow_repos, tmp_path):
        host, origin = flow_repos
        ctx = self._run_stages_1_to_3(host, origin, tmp_path)
        stale = _git(host, "rev-parse", f"refs/remotes/origin/{ctx.execution_branch}")
        # A resolver pushes a fix from elsewhere; the operator's local and tracking refs are now stale.
        work = tmp_path / "resolver-clone"
        subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
        _configure_identity(work)
        _git(work, "checkout", "-q", ctx.execution_branch)
        (work / "src" / "fix.py").write_text("print('fix')\n", encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", "fix")
        _git(work, "push", "-q", "origin", ctx.execution_branch)
        new_tip = _git(work, "rev-parse", "--short", "HEAD")
        assert _git(host, "rev-parse", f"refs/remotes/origin/{ctx.execution_branch}") == stale

        consensus = self._run_loop_capturing_consensus(ctx, ctx.execution_branch)

        assert consensus["target_commit"] == new_tip

    def test_head_target_still_reads_the_checkout(self, flow_repos):
        host, _origin = flow_repos
        ctx = FlowContext(repo_dir=str(host), dry_run=False)

        consensus = self._run_loop_capturing_consensus(ctx, "HEAD")

        assert consensus["target_commit"] == _git(host, "rev-parse", "--short", "HEAD")

    def test_review_stage_checks_out_execution_branch_and_probes_it(self, flow_repos, tmp_path):
        host, origin = flow_repos
        ctx = self._run_stages_1_to_3(host, origin, tmp_path)
        loop_res = {"consensus": {"approved": True}, "mergeability": {}, "halted_for_human": True}

        with (
            patch("sandbox_executor.flow.check_and_resolve_stage_conflicts") as mock_probe,
            patch("sandbox_executor.flow.run_review_loop", return_value=loop_res) as mock_loop,
        ):
            res = run_review_stage(ctx)

        assert res.status == StageStatus.HALTED_FOR_HUMAN
        # Stage 4 ledger rows and Stage 5 calibration run in the execution branch's working tree.
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == ctx.execution_branch
        assert mock_probe.call_args.kwargs["branch"] == ctx.execution_branch
        assert mock_loop.call_args.kwargs["target"] == ctx.execution_branch

    def test_review_stage_fails_when_execution_branch_cannot_be_checked_out(self, flow_repos, tmp_path):
        host, origin = flow_repos
        ctx = self._run_stages_1_to_3(host, origin, tmp_path)
        ctx.execution_branch = ctx.execution_branch.replace("/E-", "/E-missing-")

        with patch("sandbox_executor.flow.run_review_loop") as mock_loop:
            res = run_review_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "could not check out" in (res.error or "")
        mock_loop.assert_not_called()
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == "main"

    def test_calibrate_stage_alone_checks_out_execution_branch_from_main(self, flow_repos, tmp_path):
        host, origin = flow_repos
        ctx = self._run_stages_1_to_3(host, origin, tmp_path)
        ctx.skip_push = True
        main_tip = _git(host, "rev-parse", "main")

        # '--skip-review' or a resume entering at Stage 5: the operator is still on 'main' and calibration throws,
        # so the fallback report must still be written into the execution branch's tree, never 'main'.
        heads: list[str] = []

        def _boom(**_kwargs: Any) -> None:
            heads.append(_git(host, "rev-parse", "--abbrev-ref", "HEAD"))
            raise RuntimeError("boom")

        with patch("sandbox_executor.flow.run_calibrate", side_effect=_boom):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert heads == [ctx.execution_branch]
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == ctx.execution_branch
        assert list((host / "plans").glob("P-*_calibration.md")), "fallback report missing from the execution tree"
        assert _git(host, "log", "-1", "--format=%H", "main") == main_tip

    def test_calibrate_stage_fails_cleanly_when_execution_branch_cannot_be_checked_out(self, flow_repos, tmp_path):
        host, origin = flow_repos
        ctx = self._run_stages_1_to_3(host, origin, tmp_path)
        ctx.execution_branch = ctx.execution_branch.replace("/E-", "/E-missing-")
        status_before = _git(host, "status", "--porcelain")

        with patch("sandbox_executor.flow.run_calibrate") as mock_calibrate:
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "could not check out" in (res.error or "")
        mock_calibrate.assert_not_called()
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == "main"
        assert _git(host, "status", "--porcelain") == status_before

    def test_conflict_probe_inspects_named_branch_not_head(self, tmp_path):
        repo = _init_mergeability_repo(tmp_path / "repo")
        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"

        assert check_and_resolve_stage_conflicts(str(repo), target_branch="main")["status"] == "clean"
        with pytest.raises(ConflictResolutionError, match=r"shared\.txt"):
            check_and_resolve_stage_conflicts(str(repo), target_branch="main", branch="conflict-feature")
        clean = check_and_resolve_stage_conflicts(str(repo), target_branch="main", branch="clean-feature")
        assert clean["status"] == "clean"
        assert _git(repo, "status", "--porcelain") == ""

    def test_stage_conflict_probes_name_each_stage_branch(self, flow_repos, tmp_path):
        host, origin = flow_repos
        ctx = _dispatch_context(host, FakeRoleAgents(tmp_path, origin))
        with patch(
            "sandbox_executor.flow.check_and_resolve_stage_conflicts",
            wraps=check_and_resolve_stage_conflicts,
        ) as probe:
            results = PipelineEngine(ctx).run()
        assert results["execute"].status == StageStatus.SUCCESS
        branches = {c.kwargs["stage_name"]: c.kwargs.get("branch", "HEAD") for c in probe.call_args_list}
        assert branches == {"intent": None, "plan": ctx.intent_branch, "execute": ctx.plan_branch}

    def test_intent_temp_file_is_world_readable_for_the_container_user(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        modes: list[int] = []

        def _record(role: str, runner: str, model: str, positional_args: list[str], **kwargs: Any) -> int:
            if kwargs.get("intent_file"):
                modes.append(os.stat(kwargs["intent_file"]).st_mode & 0o777)
            return agents(role, runner, model, positional_args, **kwargs)

        ctx = _dispatch_context(host, agents, dispatcher=_record)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert modes == [0o644]


# ---------------------------------------------------------------------------
# Merge interactions: #93 runner resolution / pre-flight and #90 Stage 5 reconciliation with role-agent dispatch
# ---------------------------------------------------------------------------


class TestFlowDispatchMergeInteractions:
    """Regression tests for behaviour that only holds when the merged features work together."""

    def test_flow_dispatches_the_runner_and_model_resolved_by_the_cli_after_preflight(self, flow_repos, monkeypatch):
        """'holon flow' resolves runner/model (#93), pre-flights them, then Stage 1 dispatches with those values."""
        from sandbox_executor.agent_defaults import DEFAULT_MODELS

        host, _origin = flow_repos
        intent_file = host.parent / "intent.json"
        intent_file.write_text(json.dumps({"slug": "resolved-runner", "goal": "g"}), encoding="utf-8")
        monkeypatch.setenv("HOLON_AGENT", "claude-agent")
        monkeypatch.delenv("HOLON_SKIP_PREFLIGHT", raising=False)
        order = MagicMock()
        order.probe.return_value = MagicMock(ok=True)
        order.dispatch.return_value = 1  # stop the flow at Stage 1; only the dispatch arguments matter here

        with (
            patch("sandbox_executor.cli.run_preflight_probe", order.probe),
            patch("sandbox_executor.cli.dispatch_role_agent", order.dispatch),
            patch("sandbox_executor.cli.detect_holon_coherence", return_value=False),
            patch("sys.argv", ["holon", "flow", str(intent_file), "--repo-dir", str(host), "--skip-review"]),
            pytest.raises(SystemExit),
        ):
            cli_main()

        expected = ("claude-agent", DEFAULT_MODELS["claude"])
        assert [c[0] for c in order.mock_calls] == ["probe", "dispatch"]
        assert order.probe.call_args.args[:2] == expected
        assert order.dispatch.call_args.args[0] == "intent-creator"
        assert order.dispatch.call_args.args[1:3] == expected

    def test_stage5_checks_out_execution_branch_then_commits_reconciliation_on_calibrated(self, tmp_path):
        """Stage 5 calibrates from the execution branch (not the operator's HEAD) and, on success, commits the
        reconciliation rows on /calibrated before fast-forwarding the execution branch to it."""

        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

        execution_branch = "I-100-intent/P-200-plan/E-300-exec/_"
        calibrated_branch = "I-100-intent/P-200-plan/E-300-exec/calibrated"
        git("init", "-q", "-b", "main")
        _configure_identity(tmp_path)
        ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text("", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "initial")
        git("checkout", "-q", "-b", execution_branch)
        (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "execution change")
        git("checkout", "-q", "main")  # the operator's checkout is elsewhere when Stage 5 starts
        main_tip = git("rev-parse", "main").strip()

        seen_head: list[str] = []

        def fake_run_calibrate(**_kwargs):
            seen_head.append(git("rev-parse", "--abbrev-ref", "HEAD").strip())
            git("checkout", "-q", "-b", calibrated_branch)
            (tmp_path / "plans").mkdir()
            (tmp_path / "plans" / "P-200-plan_calibration.md").write_text("# Calibration\n", encoding="utf-8")
            git("add", "plans")
            git("commit", "-q", "-m", "calibration report")
            report = MagicMock()
            report.calibrated_branch = calibrated_branch
            report.to_dict.return_value = {"calibrated_branch": calibrated_branch}
            return report

        def fake_reconcile(**kwargs):
            _append_jsonl(Path(kwargs["ledger_path"]), {"entry_type": "action", "action_id": "ACT-RECON-1"})

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch=execution_branch,
            plan_branch="I-100-intent/P-200-plan/_",
            dry_run=False,
            skip_push=True,
        )
        with (
            patch("sandbox_executor.flow.run_calibrate", side_effect=fake_run_calibrate),
            patch("sandbox_executor.flow.reconcile_records", side_effect=fake_reconcile),
        ):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert seen_head == [execution_branch]
        assert git("log", "-1", "--format=%s", calibrated_branch).strip() == (
            "chore(calibration): record stage 5 reconciliation"
        )
        assert "ACT-RECON-1" in git("show", f"{calibrated_branch}:holon-knowledge/ledger/executions.jsonl")
        assert git("rev-parse", execution_branch).strip() == git("rev-parse", calibrated_branch).strip()
        assert git("rev-parse", "main").strip() == main_tip
        assert git("status", "--porcelain").strip() == ""


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
            patch("sandbox_executor.flow.shutil.which", return_value=None),
            patch("sandbox_executor.flow.check_pr_mergeability", return_value={"is_dirty": False}),
            patch("sandbox_executor.flow.post_pr_review_consensus", return_value={"dual_posted": True}),
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
            patch("sandbox_executor.flow.run_git", return_value=MagicMock(returncode=0, stdout="")),
            patch("sandbox_executor.flow.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow.run_review_loop", return_value=loop_res),
        ):
            res = run_review_stage(ctx)

        assert res.payload["review_package"]["test_results"] == expected


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
            patch("sandbox_executor.flow.shutil.which", return_value=gh),
            patch("sandbox_executor.flow.probe_ci_checks", probe),
            patch("sandbox_executor.flow.check_pr_mergeability", return_value={"is_dirty": False}),
            patch("sandbox_executor.flow.post_pr_review_consensus", return_value={"dual_posted": True}),
            patch("sandbox_executor.flow._resolve_review_commit", return_value="abc123"),
            patch("sandbox_executor.flow.time.sleep", sleep),
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
        assert len([r for r in rows if str(r.get("action_id", "")).startswith("ACT-REV")]) == 1

    def test_checks_never_registering_stop_once_with_not_measured(self, tmp_path):
        ctx = self._ctx(tmp_path, {"exit_code": 0, "test_pass_rate": None})
        clock = iter(float(t) for t in range(0, 1000, 5))

        with patch("sandbox_executor.flow.time.monotonic", side_effect=lambda: next(clock)):
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
        assert len([r for r in rows if str(r.get("action_id", "")).startswith("ACT-REV")]) == 1
        assert len([r for r in rows if r.get("entry_type") == "summary"]) == 1
        assert len(rows) <= 6

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

        assert res["consensus"]["approved"] is True
        assert res["ci_checks"]["measured"] is False
        sleep.assert_not_called()
        assert self._ledger_rows(tmp_path) == []

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
            patch("sandbox_executor.flow.run_git", return_value=MagicMock(returncode=0, stdout="")),
            patch("sandbox_executor.flow.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow.run_review_loop", return_value=loop_res),
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
        }
        with (
            patch("sandbox_executor.flow.run_git", return_value=MagicMock(returncode=0, stdout="")),
            patch("sandbox_executor.flow.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow.run_review_loop", return_value=loop_res),
        ):
            res = run_review_stage(ctx)

        assert res.error == "Autonomous review consensus failed: test suite reported failures."


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
            patch("sandbox_executor.flow.run_review_stage") as review,
            patch("sandbox_executor.flow.run_calibrate_stage") as calibrate,
            patch("sandbox_executor.flow.run_git") as git,
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
