"""Unit and integration tests for sandbox_executor.flow."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.cli import main as cli_main
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    PipelineEngine,
    StageResult,
    StageStatus,
    check_pr_mergeability,
    extract_valid_ledger_rows,
    load_checkpoint,
    reconcile_ledger_content,
    reconcile_ledger_file,
    reconcile_ledger_rows,
    reconcile_ledgers,
    run_calibrate_stage,
    run_execute_stage,
    run_intent_stage,
    run_plan_stage,
    run_review_stage,
    save_checkpoint,
)


class TestFlowDataModels:
    """Test data structures, enums, serialization, and atomic checkpoint persistence."""

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

        # Verify executions ledger
        exec_ledger = ledger_dir / "executions.jsonl"
        assert exec_ledger.exists()
        with open(exec_ledger) as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 1
        assert lines[0]["execution_id"] == res.payload["execution_id"]

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

        with patch("sandbox_executor.flow.run_calibrate", return_value=mock_report):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert res.payload["calibration_report"]["accuracy_score"] == 0.95
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
        assert any(f.startswith("executions/E-") and f.endswith(".md") for f in call_files)
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
