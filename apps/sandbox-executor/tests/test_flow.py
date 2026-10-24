"""Unit and integration tests for sandbox_executor.flow."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest
from sandbox_executor.cli import main as cli_main
from sandbox_executor.flow import (
    REVIEW_GIT_ENV,
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

    def test_stage1_intent_success(self, tmp_path):
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "stage1-test", "goal": "Build stage 1"},
            dry_run=False,
        )

        with patch("sandbox_executor.flow.run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0)
            res = run_intent_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert ctx.intent_branch is not None
        assert "stage1-test" in ctx.intent_branch
        assert res.payload["slug"] == "stage1-test"

        # Verify ledger file written
        ledger_file = ledger_dir / "intents.jsonl"
        assert ledger_file.exists()
        with open(ledger_file) as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 1
        assert lines[0]["slug"] == "stage1-test"

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

    def test_intent_stage_persists_resolved_questions_to_ledger(self, tmp_path):
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={
                "slug": "resolved-q",
                "goal": "Build stage 1",
                "questions": ["What protocol?"],
                "answers": {"What protocol?": "OAuth2"},
            },
            dry_run=False,
        )

        with patch("sandbox_executor.flow.run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0)
            res = run_intent_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        ledger_file = ledger_dir / "intents.jsonl"
        assert ledger_file.exists()
        with open(ledger_file) as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 1
        assert lines[0]["questions"] == ["What protocol?"]
        assert lines[0]["answers"] == {"What protocol?": "OAuth2"}

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

    def test_stage2_plan_generation_and_ev(self, tmp_path):
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir(parents=True)

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_branch="I-12345-my-intent/_",
            intent_data={"slug": "my-intent", "goal": "Generate plan"},
            agent="antigravity-agent",
            model="gemini-3.8-flash-medium",
            dry_run=False,
        )

        with patch("sandbox_executor.flow.run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0)
            res = run_plan_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert ctx.plan_branch is not None
        assert "P-" in ctx.plan_branch
        assert "antigravity-agent" in ctx.plan_branch

        # Verify plan markdown file generated
        plan_file = tmp_path / res.payload["plan_file"]
        assert plan_file.exists()
        content = plan_file.read_text()
        assert "# Plan for" in content
        assert "| metric" in content

        # Verify EV in metrics payload
        metrics = res.payload["metrics"]
        assert "ev" in metrics
        assert isinstance(metrics["ev"], float)

        # Verify plans.jsonl ledger entry
        plans_ledger = ledger_dir / "plans.jsonl"
        assert plans_ledger.exists()
        with open(plans_ledger) as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 1
        assert lines[0]["plan_id"] == res.payload["plan_id"]

    def test_stage3_execute_missing_plan_branch(self):
        ctx = FlowContext(plan_branch=None)
        res = run_execute_stage(ctx)
        assert res.status == StageStatus.FAILED
        assert "without an active plan_branch" in (res.error or "")

    def test_stage3_execute_success(self, tmp_path):
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            plan_branch="I-123-intent/P-456-plan/_",
            agent="antigravity-agent",
            model="gemini-3.8-flash-medium",
            dry_run=False,
        )

        with patch("sandbox_executor.flow.run_git") as mock_git, patch("subprocess.run") as mock_sub:
            mock_git.return_value = MagicMock(returncode=0)
            mock_sub.return_value = MagicMock(returncode=0, stdout="test pass", stderr="")
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert ctx.execution_branch is not None
        assert "E-" in ctx.execution_branch
        assert res.payload["test_pass_rate"] == 1.0
        assert "execution_file" not in res.payload
        assert not (tmp_path / "executions").exists()

        # Verify executions ledger contains both summary and action records
        exec_ledger = ledger_dir / "executions.jsonl"
        assert exec_ledger.exists()
        with open(exec_ledger) as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 2
        summary = next(line for line in lines if line.get("entry_type") == "summary")
        action = next(line for line in lines if line.get("entry_type") == "action")
        assert summary["execution_id"] == res.payload["execution_id"]
        assert "execution_file" not in summary
        assert action["category"] == "test"

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
            patch("sandbox_executor.flow.run_git", side_effect=fake_git),
            patch("sandbox_executor.flow.run_review_loop", return_value=loop_res),
        ):
            res = run_review_stage(ctx)
        assert res.payload["review_package"]["diff_summary"] == {"files_changed": 1, "insertions": 7, "deletions": 2}

    def test_stage3_execute_failure(self, tmp_path):
        """Test Stage 3 correctly transitions to FAILED status on test suite failure."""
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            plan_branch="I-100-intent/P-200-plan/_",
            dry_run=False,
        )
        with patch("sandbox_executor.flow.run_git") as mock_git, patch("subprocess.run") as mock_sub:
            mock_git.return_value = MagicMock(returncode=0)
            mock_sub.return_value = MagicMock(returncode=1, stdout="FAILED", stderr="Error")
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert res.payload["test_pass_rate"] == 0.0
        assert res.payload["exit_code"] == 1

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

        with patch("sandbox_executor.flow.run_calibrate", return_value=mock_report) as mock_run:
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert res.payload["calibration_report"]["accuracy_score"] == 0.95
        assert mock_run.call_args.kwargs["log_fn"] == ctx.log
        assert ctx.calibrated_branch == "I-100-intent/P-200-plan/E-300-exec/calibrated"

    def test_stage2_plan_invokes_converge_prettier(self, tmp_path):
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir(parents=True)

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_branch="I-12345-my-intent/_",
            intent_data={"slug": "my-intent", "goal": "Generate plan"},
            agent="antigravity-agent",
            model="gemini-3.8-flash-medium",
            dry_run=False,
        )

        with (
            patch("sandbox_executor.flow.run_git") as mock_git,
            patch("sandbox_executor.flow.converge_prettier") as mock_converge,
        ):
            mock_git.return_value = MagicMock(returncode=0)
            res = run_plan_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        mock_converge.assert_called_once()
        call_files = mock_converge.call_args[0][0]
        assert len(call_files) == 1
        assert call_files[0].startswith("plans/P-") and call_files[0].endswith(".md")
        assert mock_converge.call_args[1].get("repo_dir") == str(tmp_path)

    def test_stage3_execute_invokes_converge_prettier(self, tmp_path):
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            plan_branch="I-123-intent/P-456-plan/_",
            agent="antigravity-agent",
            model="gemini-3.8-flash-medium",
            dry_run=False,
        )

        with (
            patch("sandbox_executor.flow.run_git") as mock_git,
            patch("subprocess.run") as mock_sub,
            patch("sandbox_executor.flow.converge_prettier") as mock_converge,
        ):
            mock_git_res = MagicMock()
            mock_git_res.returncode = 0
            mock_git_res.stdout = " M docs/readme.md\n"

            def mock_git_side_effect(args, **kwargs):
                if "status" in args and "--porcelain" in args:
                    return mock_git_res
                return MagicMock(returncode=0)

            mock_git.side_effect = mock_git_side_effect
            mock_sub.return_value = MagicMock(returncode=0, stdout="test pass", stderr="")
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        mock_converge.assert_called_once()
        call_files = mock_converge.call_args[0][0]
        assert "docs/readme.md" in call_files
        assert not any(f.startswith("executions/") for f in call_files)
        assert mock_converge.call_args[1].get("repo_dir") == str(tmp_path)

    def test_stage5_calibrate_fallback_invokes_converge_prettier(self, tmp_path):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            dry_run=False,
        )

        with (
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
        assert git("rev-parse", "HEAD").strip() == head_before
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

    def test_stage1_intent_fails_fast_when_ledger_append_fails(self, tmp_path):
        """A failed Stage 1 intent append raises so the engine halts the pipeline."""
        (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True)
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "stage1-append-fail", "goal": "Fail fast"},
            dry_run=False,
        )
        with (
            patch("sandbox_executor.flow.run_git", return_value=MagicMock(returncode=0)),
            patch("sandbox_executor.flow.try_append_ledger_line", return_value=False),
            pytest.raises(RuntimeError, match="Stage 1 intent record"),
        ):
            run_intent_stage(ctx)

    def test_stage2_plan_fails_fast_when_ledger_append_fails(self, tmp_path):
        """A failed Stage 2 plan append raises so the engine halts the pipeline."""
        (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True)
        (tmp_path / "plans").mkdir(parents=True)
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_branch="I-12345-my-intent/_",
            intent_data={"slug": "my-intent", "goal": "Generate plan"},
            agent="antigravity-agent",
            model="gemini-3.8-flash-medium",
            dry_run=False,
        )
        with (
            patch("sandbox_executor.flow.run_git", return_value=MagicMock(returncode=0)),
            patch("sandbox_executor.flow.try_append_ledger_line", return_value=False),
            pytest.raises(RuntimeError, match="Stage 2 plan record"),
        ):
            run_plan_stage(ctx)

    def test_stage3_execute_fails_fast_when_ledger_append_fails(self, tmp_path):
        """A failed Stage 3 execution summary append raises so the engine halts the pipeline."""
        (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True)
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            plan_branch="I-123-intent/P-456-plan/_",
            agent="antigravity-agent",
            model="gemini-3.8-flash-medium",
            dry_run=False,
        )
        with (
            patch("sandbox_executor.flow.run_git", return_value=MagicMock(returncode=0)),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="test pass", stderr="")),
            patch("sandbox_executor.flow.try_append_ledger_line", return_value=False),
            pytest.raises(RuntimeError, match="Stage 3 execution summary"),
        ):
            run_execute_stage(ctx)

    def test_engine_halts_when_stage1_ledger_append_fails(self, tmp_path):
        """The engine converts a failed Stage 1 append into a FAILED stage and stops."""
        (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True)
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "engine-append-fail", "goal": "Fail fast"},
            dry_run=False,
        )
        with (
            patch("sandbox_executor.flow.run_git", return_value=MagicMock(returncode=0)),
            patch("sandbox_executor.flow.try_append_ledger_line", return_value=False),
        ):
            results = PipelineEngine(ctx).run()

        assert results["intent"].status == StageStatus.FAILED
        assert "plan" not in results


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
        """Verify subprocess environments in execute and review stages contain proxy env vars when token_reduce=True."""
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

        # 1. Execute stage propagation
        ctx = FlowContext(token_reduce=True, plan_branch="I-test/P-1/_", dry_run=False)
        with (
            patch("sandbox_executor.flow.run_git") as mock_git,
            patch("subprocess.run") as mock_subproc,
            patch("os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data="")),
        ):
            mock_git.return_value = MagicMock(returncode=0, stdout="")
            mock_subproc.return_value = MagicMock(returncode=0, stdout="", stderr="")
            res = run_execute_stage(ctx)
            assert res.status == StageStatus.SUCCESS
            assert mock_subproc.call_count >= 1
            call_kwargs = mock_subproc.call_args.kwargs
            env = call_kwargs.get("env")
            assert env is not None
            assert env.get("HTTP_PROXY") == "http://127.0.0.1:8080"
            assert env.get("HTTPS_PROXY") == "http://127.0.0.1:8080"
            assert env.get("NODE_EXTRA_CA_CERTS") == "/tmp/mitmproxy-ca-cert.pem"
            assert env.get("SSL_CERT_FILE") == "/tmp/mitmproxy-ca-cert.pem"
            assert res.payload.get("token_reduce") is True
            assert res.payload.get("proxy_active") is True

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


class SteppingClock:
    """Fake monotonic clock that advances by ``step`` seconds on every reading."""

    def __init__(self, step):
        self.step = step
        self.now = 0.0

    def __call__(self):
        self.now += self.step
        return self.now


class TestStageBudgets:
    """Bean 0139: stage ceilings, elapsed-versus-budget summary lines and the verification timeout."""

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
        expected_budgets = {"intent": 600, "plan": 2100, "execute": 2700, "review": 900, "calibrate": 600}
        for stage, budget in expected_budgets.items():
            assert results[stage].budget_seconds == budget, stage
            assert results[stage].elapsed_seconds is not None, stage
        logs = "\n".join(ctx.logs)
        assert re.search(r"intent: ok \(\d+\.\d min / 10 min\)", logs)
        assert re.search(r"plan: ok \(\d+\.\d min / 35 min\)", logs)
        assert re.search(r"execute: ok \(\d+\.\d min / 45 min\)", logs)
        assert re.search(r"review: halted \(\d+\.\d min / 15 min\)", logs)

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
        # C3-I3: two review iterations each within budget plus PR/posting overhead exceed 2 x 15 min in total; the
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
            patch("sandbox_executor.flow.run_review_stage", side_effect=fake_review),
            patch("sandbox_executor.flow.run_calibrate_stage", return_value=calibrate) as mock_cal,
        ):
            engine = PipelineEngine(ctx, from_stage="review", clock=SteppingClock(step=30.5 * 60))
            engine.run()
        assert review.status == StageStatus.HALTED_FOR_HUMAN
        assert review.budget_seconds == 2 * 900
        assert review.payload["budget_overrun"]["budget_name"] == "stage"
        assert "budget_breach" not in review.payload
        mock_cal.assert_called_once()

    def test_calibration_overrun_keeps_success(self, tmp_path):
        ctx = FlowContext(repo_dir=str(tmp_path), intent_data={"slug": "cal", "goal": "Cal"}, dry_run=True)
        calibrate = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS)
        with patch("sandbox_executor.flow.run_calibrate_stage", return_value=calibrate):
            result = PipelineEngine(ctx, clock=SteppingClock(step=3000)).timed_execute_stage(FlowStage.CALIBRATE)
        assert result.status == StageStatus.SUCCESS
        assert result.payload["budget_overrun"]["budget_seconds"] == 600

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
            patch("sandbox_executor.flow.run_plan_stage", side_effect=fake_stage("plan")),
            patch("sandbox_executor.flow.run_review_stage", side_effect=fake_stage("review")),
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

    def _run_execute(self, tmp_path, env, side_effect=None):
        (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True)
        ctx = FlowContext(repo_dir=str(tmp_path), plan_branch="I-1-x/P-2-y/_", dry_run=False)
        with (
            patch.dict(os.environ, env),
            patch("sandbox_executor.flow.run_git") as mock_git,
            patch("subprocess.run") as mock_sub,
        ):
            mock_git.return_value = MagicMock(returncode=0)
            mock_sub.return_value = MagicMock(returncode=0, stdout="", stderr="")
            if side_effect:
                mock_sub.side_effect = side_effect
            res = run_execute_stage(ctx)
        return ctx, res, mock_sub

    def test_verification_timeout_uses_the_execute_budget(self, tmp_path):
        _, _, mock_sub = self._run_execute(tmp_path, {"HOLON_STAGE_BUDGET_EXECUTE_MINUTES": "20"})
        timeout = mock_sub.call_args.kwargs["timeout"]
        assert timeout != 300
        assert 1190 < timeout <= 1200

    def test_verification_timeout_has_a_floor(self, tmp_path):
        _, _, mock_sub = self._run_execute(tmp_path, {"HOLON_STAGE_BUDGET_EXECUTE_MINUTES": "0.1"})
        assert mock_sub.call_args.kwargs["timeout"] == 30

    def test_verification_timeout_message_names_the_budget(self, tmp_path):
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

        ctx, res, _ = self._run_execute(tmp_path, {"HOLON_STAGE_BUDGET_EXECUTE_MINUTES": "20"}, side_effect=timeout)
        assert res.status == StageStatus.FAILED
        assert any("timed out after 1200 seconds (execute stage budget)" in line for line in ctx.logs)
        assert not any("300 seconds" in line for line in ctx.logs)

    def test_review_iteration_ceiling_stops_the_loop(self, tmp_path):
        ctx = FlowContext(repo_dir=str(tmp_path), execution_branch="I-1-x/P-2-y/E-3-z/_", dry_run=True)
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"test_pass_rate": 0.0, "exit_code": 1}
        )
        with patch("sandbox_executor.flow.run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0, stdout="")
            res = run_review_loop(ctx, target="I-1-x/P-2-y/E-3-z/_", max_iterations=5, clock=SteppingClock(1000))
        assert res["total_iterations"] == 1
        assert res["budget_breach"]["budget_name"] == "review_iteration"
        assert res["iteration_summaries"] == ["iteration 1 review: failed (16.7 min / 15 min)"]


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
