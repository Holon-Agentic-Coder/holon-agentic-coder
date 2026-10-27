"""Unit and integration tests for sandbox_executor.flow."""

import argparse
import dataclasses
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor import flow as flow_module
from sandbox_executor.budgets import MAX_REPLAN_ATTEMPTS, MAX_SUB_INTENT_DEPTH, resolve_entropy_budget
from sandbox_executor.cli import main as cli_main
from sandbox_executor.entrypoint.executor import OVER_BUDGET_EXIT_CODE, _sanitize_string, should_decompose
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

from tests.leak_assertions import make_gh_probe, make_probe


@contextmanager
def _review_loop_gh_stubs() -> Iterator[None]:
    """Stub every review-loop helper that shells out to ``gh``, so an approving loop issues no real gh call."""
    clean_merge = {"is_dirty": False, "merge_state_status": "CLEAN", "mergeable": "MERGEABLE", "source": "github_cli"}
    local_ci = {
        "source": "local_tests",
        "total": 1,
        "passed": 1,
        "failed": 0,
        "pending": 0,
        "skipped": 0,
        "all_passed": True,
        "checks": [{"name": "local_tests", "bucket": "pass", "state": "SUCCESS"}],
    }
    with (
        patch("sandbox_executor.flow.check_pr_mergeability", side_effect=lambda *a, **k: dict(clean_merge)),
        patch("sandbox_executor.flow.probe_ci_checks", side_effect=lambda *a, **k: json.loads(json.dumps(local_ci))),
        patch(
            "sandbox_executor.flow.post_pr_review_consensus",
            return_value={"dual_posted": True, "official_review": {"success": True}},
        ) as mock_post,
    ):
        yield
    mock_post.assert_called_once()


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

    def test_flow_context_log_and_stage_failure_mask_provider_tokens_and_env_literals(self, monkeypatch):
        gho_probe = make_gh_probe("gh" + "o_")
        ghp_probe = make_gh_probe()
        env_probe = make_probe("ghtoken")
        monkeypatch.setenv("GITHUB_TOKEN", env_probe)
        ctx = FlowContext(repo_dir="/tmp/repo", dry_run=True, token_reduce=False)

        ctx.log(f"push failed for {gho_probe} and {ghp_probe} using {env_probe}")
        res = PipelineEngine(ctx).handle_stage_failure(
            FlowStage.EXECUTE, RuntimeError(f"auth {gho_probe} {ghp_probe} {env_probe} rejected")
        )

        for text in (*ctx.logs, res.error):
            for sample in (gho_probe, ghp_probe, env_probe):
                assert sample not in text
        assert "push failed" in ctx.logs[0]
        assert "rejected" in res.error

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

        # The empty-diff guard allows the PR: only an allowed diff can reach consensus (C15-N1).
        with (
            patch("sandbox_executor.flow.run_git") as mock_git,
            patch("sandbox_executor.flow._execution_effective_diff", return_value=(True, "1 file changed")),
            patch("sandbox_executor.flow.create_or_update_pr", return_value={"success": True}),
            _review_loop_gh_stubs(),
        ):
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
        with (
            patch("sandbox_executor.flow.run_git") as mock_git,
            patch("sandbox_executor.flow._execution_effective_diff", return_value=(True, "1 file changed")),
            patch("sandbox_executor.flow.create_or_update_pr", return_value={"success": True}),
            _review_loop_gh_stubs(),
        ):
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
            patch("sandbox_executor.flow._execution_effective_diff", return_value=(True, "1 file changed")),
            patch("sandbox_executor.flow.create_or_update_pr", return_value={"success": True}),
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
            patch("sandbox_executor.flow._execution_effective_diff", return_value=(True, "1 file changed")),
            patch("sandbox_executor.flow.create_or_update_pr", return_value={"success": True}),
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
    """Bean 0139: stage ceilings, elapsed-versus-budget summary lines and the execute budget hand-down."""

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

    def test_decomposed_execute_overrun_keeps_success(self, tmp_path):
        # A decomposed Stage 3 spans whole child pipelines, so its overrun is recorded, never a stage failure.
        ctx = FlowContext(repo_dir=str(tmp_path), intent_data={"slug": "dec", "goal": "Dec"}, dry_run=True)
        decomposed = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"decomposed": True})
        with patch("sandbox_executor.flow.run_execute_stage", return_value=decomposed):
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
        with patch("sandbox_executor.flow.run_execute_stage", return_value=plain):
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
            patch("sandbox_executor.flow.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow._snapshot_child_branches", return_value=set()),
            patch("sandbox_executor.cli.run_docker_container", side_effect=fake_container),
            patch("sandbox_executor.flow._fetch_branch_prefix_error", return_value=None),
            patch("sandbox_executor.flow._discover_child_branch", return_value=(exec_branch, None)),
            patch("sandbox_executor.flow._track_local_branch"),
            patch(
                "sandbox_executor.flow._read_ledger_rows",
                return_value=[
                    {"entry_type": "summary", "execution_id": "E-3-claude-agent-claude-opus-5-5", "status": "success"}
                ],
            ),
            patch("sandbox_executor.flow._resolve_branch_ref", return_value="refs/remotes/origin/I-1-x/P-2-y/_"),
            patch("sandbox_executor.flow.changed_paths", return_value=["src/app.py"]),
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
        with patch("sandbox_executor.flow.run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0, stdout="")
            res = run_review_loop(ctx, target="I-1-x/P-2-y/E-3-z/_", max_iterations=5, clock=SteppingClock(1000))
        assert res["total_iterations"] == 1
        assert res["budget_breach"]["budget_name"] == "review_iteration"
        assert res["iteration_summaries"] == ["iteration 1 review: failed (16.7 min / 15 min)"]


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

    @pytest.mark.parametrize("code", [1, 2])
    def test_stage3_nonzero_container_exit_fails_stage(self, flow_repos, code):
        host, _origin = flow_repos
        # Any non-zero exit except the executor's over-budget refusal code (read from its pushed row) fails at once.
        ctx = _dispatch_context(host, MagicMock(return_value=code), plan_branch="I-1-x/P-1-claude-agent-m/_")

        res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert res.error == f"Execute stage failed: executor agent exited with code {code}."
        assert res.payload["exit_code"] == code

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
            patch("sandbox_executor.flow.shutil.which", return_value="/usr/bin/gh"),
            patch("sandbox_executor.flow.run_git", return_value=MagicMock(returncode=0, stdout="")),
            patch("sandbox_executor.flow.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow._execution_effective_diff", return_value=(has_diff, detail)),
            patch("sandbox_executor.flow.create_or_update_pr") as mock_pr,
            patch("sandbox_executor.flow.probe_ci_checks", probe),
            patch("sandbox_executor.flow.check_pr_mergeability", return_value={"is_dirty": False}),
            patch("sandbox_executor.flow.post_pr_review_consensus", return_value={"dual_posted": True}),
            patch("sandbox_executor.flow._resolve_review_commit", return_value="abc123"),
            patch("sandbox_executor.flow.time.sleep", sleep),
            patch("sandbox_executor.flow._wait_for_ci_registration", wait),
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
            patch("sandbox_executor.flow.shutil.which", return_value="/usr/bin/gh"),
            patch("sandbox_executor.flow.run_git", return_value=MagicMock(returncode=0, stdout="")),
            patch("sandbox_executor.flow.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow._execution_effective_diff", return_value=(None, "fetch failed")),
            patch("sandbox_executor.flow.create_or_update_pr") as mock_pr,
            patch("sandbox_executor.flow.probe_ci_checks", probe),
            patch("sandbox_executor.flow.check_pr_mergeability", return_value={"is_dirty": False}),
            patch("sandbox_executor.flow.post_pr_review_consensus", post),
            patch("sandbox_executor.flow._resolve_review_commit", return_value="abc123"),
            patch("sandbox_executor.flow.time.sleep"),
            patch("sandbox_executor.flow._wait_for_ci_registration"),
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
        assert res["consensus"]["status"] == "rejected"
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


# ---------------------------------------------------------------------------
# Entropy budget handoff: Stage 3 never halts for a human, re-plans, and drives explicit sub-intent lists
# ---------------------------------------------------------------------------


class BudgetRoleAgents(FakeRoleAgents):
    """FakeRoleAgents whose planner writes chosen entropies and sections, and whose executor judges plans like the
    real entrypoint (``should_decompose``), recording ``decomposed`` and ``over_budget`` rows instead of running.

    ``plans`` is consumed one item per planner call: ``(entropy, extra_markdown)``; the last item repeats. A plan for
    an intent branch containing ``fail_planning_for`` writes nothing, so that child's Stage 2 fails.
    """

    def __init__(
        self,
        root: Path,
        origin: Path,
        plans: list[tuple[float, str]],
        row_budget: bool = False,
        fail_planning_for: str | None = None,
    ):
        super().__init__(root, origin)
        self.plans = list(plans)
        self.row_budget = row_budget
        self.fail_planning_for = fail_planning_for
        self.planner_calls = 0
        self.replan_feedback_seen: list[str | None] = []
        self.in_progress = False
        self.overlaps = 0

    def __call__(self, role: str, runner: str, model: str, positional_args: list[str], **kwargs: Any) -> int:
        if self.in_progress:
            self.overlaps += 1
        self.in_progress = True
        try:
            return super().__call__(role, runner, model, positional_args, **kwargs)
        finally:
            self.in_progress = False

    def _plan(self, work: Path, args: list[str], _kwargs: dict[str, Any]) -> int:
        intent_branch, agent, model = args
        self.replan_feedback_seen.append(os.environ.get("HOLON_REPLAN_FEEDBACK"))
        entropy, extra = self.plans[min(self.planner_calls, len(self.plans) - 1)]
        self.planner_calls += 1
        # A real planner run takes minutes; keep each plan branch name (stamped in seconds) unique.
        self.clock_offset += 1
        if self.fail_planning_for and self.fail_planning_for in intent_branch:
            return 0
        plan_id = f"P-{self.now()}-{agent}-{_sanitize_model_name(model)}"
        plan_branch = f"{intent_branch.removesuffix('/_')}/{plan_id}/_"
        _git(work, "fetch", "-q", "origin", intent_branch)
        _git(work, "checkout", "-q", "-B", plan_branch, "FETCH_HEAD")
        plan_rel = f"plans/{plan_id}.md"
        (work / "plans").mkdir(exist_ok=True)
        (work / plan_rel).write_text(
            PLAN_MARKDOWN.format(intent=intent_branch, plan_id=plan_id) + extra, encoding="utf-8"
        )
        row: dict[str, Any] = {
            "plan_id": plan_id,
            "intent_branch": intent_branch,
            "agent": agent,
            "model": model,
            "entropy": entropy,
            "plan_file": plan_rel,
            "status": "proposed",
        }
        if self.row_budget:
            intents = [
                json.loads(line)
                for line in (work / "holon-knowledge/ledger/intents.jsonl").read_text().splitlines()
                if line.strip()
            ]
            row["entropy_budget"] = resolve_entropy_budget({}, intents[-1] if intents else None)
        _append_jsonl(work / "holon-knowledge/ledger/plans.jsonl", row)
        self._push(work, plan_branch, f"plan: {plan_id} created by {agent} ({model})")
        return 0

    def _execute(self, work: Path, args: list[str], kwargs: dict[str, Any]) -> int:
        plan_branch = args[0]
        _git(work, "fetch", "-q", "origin", plan_branch)
        plan_id = plan_branch.removesuffix("/_").rsplit("/", 1)[-1]
        plans = _ledger_rows(work, "FETCH_HEAD", "plans.jsonl")
        plan_row = next(r for r in reversed(plans) if r.get("plan_id") == plan_id)
        intent_rows = _ledger_rows(work, "FETCH_HEAD", "intents.jsonl")
        content = _git(work, "show", f"FETCH_HEAD:{plan_row['plan_file']}")
        decision = should_decompose(plan_row, content, intent_rows[-1] if intent_rows else None)
        if decision.decision == "execute":
            return super()._execute(work, args, kwargs)
        agent, model = args[1], args[2]
        exec_id = f"E-{self.now()}-{_sanitize_string(agent)}-{_sanitize_string(model)}"
        exec_branch = f"{plan_branch.removesuffix('/_')}/{exec_id}/_"
        _git(work, "checkout", "-q", "-B", exec_branch, "FETCH_HEAD")
        row: dict[str, Any] = {
            "entry_type": "summary",
            "execution_id": exec_id,
            "plan_branch": plan_branch,
            "status": decision.decision,
            "entropy": decision.entropy,
            "entropy_budget": decision.entropy_budget,
            "ledger_revision": 1,
        }
        if decision.sub_intents:
            row["sub_intents"] = decision.sub_intents
        _append_jsonl(work / "holon-knowledge/ledger/executions.jsonl", row)
        self._push(work, exec_branch, f"execute: {exec_id} recorded {decision.decision}")
        # Like the real executor: a refused plan exits non-zero after pushing its row.
        return OVER_BUDGET_EXIT_CODE if decision.decision == "over_budget" else 0


SUB_INTENT_LIST = "\n## Sub-intent list\n\n- Add the parser\n- Add the CLI flag\n"


def _budget_context(host: Path, agents: FakeRoleAgents, budget: float, **intent_extra: Any) -> FlowContext:
    intent = {"slug": "budget-test", "goal": "Change the application", "labels": ["flow"], "entropy_budget": budget}
    intent.update(intent_extra)
    return _dispatch_context(host, agents, intent_data=intent)


def _all_intent_rows(host: Path) -> list[dict[str, Any]]:
    """Every intents.jsonl row on every origin branch, de-duplicated by branch."""
    _git(host, "fetch", "-q", "origin")
    rows: dict[str, dict[str, Any]] = {}
    for ref in _git(host, "for-each-ref", "--format=%(refname)", "refs/remotes/origin").splitlines():
        for row in _ledger_rows(host, ref, "intents.jsonl"):
            rows[row["branch"]] = row
    return list(rows.values())


class TestEntropyBudgetHandoffInFlow:
    """Intent goal 4: no Stage 3 halt, budget handoff, bounded re-planning and sequential child pipelines."""

    @pytest.mark.parametrize(
        ("entropy", "extra", "expected_status"),
        [
            (2.0, "", StageStatus.SUCCESS),
            (12.6, "", StageStatus.FAILED),
            (2.0, SUB_INTENT_LIST, StageStatus.SUCCESS),
        ],
        ids=["normal", "over-budget", "decomposed"],
    )
    @pytest.mark.parametrize("precheck", [True, False], ids=["precheck", "container-row"])
    def test_stage3_never_halts_for_a_human(self, flow_repos, tmp_path, entropy, extra, expected_status, precheck):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(entropy, extra), (2.0, "")])
        ctx = _budget_context(host, agents, 5.0)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

        if precheck:
            res = run_execute_stage(ctx)
        else:
            # Model a plan the flow could not read before dispatch: the executor's own row decides.
            with patch("sandbox_executor.flow._plan_budget_decision", return_value=None):
                res = run_execute_stage(ctx)

        assert res.status != StageStatus.HALTED_FOR_HUMAN
        assert res.status == expected_status
        if expected_status == StageStatus.FAILED:
            assert res.payload["over_budget"] is True
            assert res.payload["dispatched"] is (not precheck)
            assert (res.payload["entropy"], res.payload["entropy_budget"]) == (entropy, 5.0)
            assert res.error == (
                "Execute stage failed: plan entropy 12.6 exceeds the entropy budget 5 and the plan lists no sub-intent "
                "list."
            )
            if not precheck:
                # The executor's row also carries the execution it recorded.
                assert res.payload["execution_branch"] == ctx.execution_branch is not None

    def test_intent_budget_16_with_plan_entropy_12_6_executes_normally(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, "")])
        ctx = _budget_context(host, agents, 16.0)

        results = PipelineEngine(ctx).run()

        assert results["execute"].status == StageStatus.SUCCESS
        assert agents.roles() == ["intent-creator", "planner", "executor"]
        assert ctx.replan_attempts == 0

    def test_budget_persisted_on_plan_row_is_the_one_enforced(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, "")], row_budget=True)
        ctx = _budget_context(host, agents, 16.0)

        results = PipelineEngine(ctx).run()

        assert results["execute"].status == StageStatus.SUCCESS
        plan_rows = _ledger_rows(host, f"origin/{ctx.plan_branch}", "plans.jsonl")
        assert plan_rows[-1]["entropy_budget"] == 16.0

    def test_over_budget_plan_replans_then_fails_without_intent_rows(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, "")])
        ctx = _budget_context(host, agents, 5.0)

        results = PipelineEngine(ctx).run()

        assert agents.planner_calls == 1 + MAX_REPLAN_ATTEMPTS
        assert "executor" not in agents.roles()
        assert ctx.replan_attempts == MAX_REPLAN_ATTEMPTS
        execute = results["execute"]
        assert execute.status == StageStatus.FAILED
        assert execute.error == (
            f"Execute stage failed: plan entropy 12.6 exceeds the entropy budget 5 after {MAX_REPLAN_ATTEMPTS} "
            "re-plan attempts and the plan lists no sub-intent list."
        )
        assert "review" not in results
        assert execute.payload["replan_attempts"] == MAX_REPLAN_ATTEMPTS
        # The feedback reached the planner on each re-plan only, and never lingers in the payload or environment.
        assert agents.replan_feedback_seen[0] is None
        assert all("entropy 12.6" in (fb or "") for fb in agents.replan_feedback_seen[1:])
        assert "replan_feedback" not in ctx.intent_data
        assert "HOLON_REPLAN_FEEDBACK" not in os.environ
        assert [r["slug"] for r in _all_intent_rows(host)] == ["budget-test"]

    def test_over_budget_plan_recovers_after_one_replan(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, ""), (4.0, "")])
        ctx = _budget_context(host, agents, 5.0)

        results = PipelineEngine(ctx).run()

        assert results["execute"].status == StageStatus.SUCCESS
        assert ctx.replan_attempts == 1
        assert agents.roles() == ["intent-creator", "planner", "planner", "executor"]

    def test_explicit_sub_intent_list_runs_child_pipelines_in_order(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(9.0, SUB_INTENT_LIST), (2.0, "")])
        ctx = _budget_context(host, agents, 5.0)

        results = PipelineEngine(ctx).run()

        assert agents.overlaps == 0
        assert agents.roles() == [
            "intent-creator",
            "planner",
            "executor",
            *(["intent-creator", "planner", "executor"] * 2),
        ]
        children = [p["slug"] for p in agents.intent_payloads[1:]]
        assert children == ["add-the-parser", "add-the-cli-flag"]
        for payload in agents.intent_payloads[1:]:
            assert payload["target_branch"] == ctx.intent_branch
            assert payload["parent_intent_id"] == ctx.intent_branch
            assert payload["entropy_budget"] == 5.0
            assert payload["sub_intent_depth"] == 1
            # Labels drive the PR title scope, so each child PR is scoped like its parent's.
            assert payload["labels"] == ["flow"]
        execute = results["execute"]
        assert execute.status == StageStatus.SUCCESS
        assert execute.payload["decomposed"] is True
        assert [c["slug"] for c in execute.payload["children"]] == children
        assert execute.payload["parent_branch"] == ctx.intent_branch
        assert execute.payload["parent_target_branch"] == "main"
        for child in execute.payload["children"]:
            assert child["execution_branch"].startswith(child["intent_branch"].removesuffix("/_") + "/P-")
            assert {"pr_url", "pr_number"} <= child.keys()
        summary = flow_module.format_decomposition_summary(execute)
        assert summary[0] == "Decomposed into 2 sub-intent(s); merge each child PR into the parent intent branch:"
        assert summary[1].startswith("  1. add-the-parser: ")
        assert summary[-1] == (
            f"Pending (not yet automated): after the child PRs merge, raise a PR from '{ctx.intent_branch}' to 'main'."
        )
        assert all(c["status"] in ("success", "skipped", "halted_for_human") for c in execute.payload["children"])
        assert results["review"].status == StageStatus.SKIPPED
        assert results["review"].payload == {"reason": "decomposed"}
        assert results["calibrate"].status == StageStatus.SKIPPED
        # Skipped because the plan was decomposed, not only by the test context's skip flag.
        assert results["calibrate"].payload == {"reason": "decomposed"}
        assert any("[sub-intent add-the-parser]" in line for line in ctx.logs)
        # The executor wrote no intent rows: only the parent and the two children created through Stage 1 exist.
        assert sorted(r["slug"] for r in _all_intent_rows(host)) == sorted(["budget-test", *children])

    def test_child_that_itself_decomposes_lists_its_own_children_in_the_summary(self, flow_repos, tmp_path):
        host, origin = flow_repos
        # Parent and first child both list sub-intents; the two grandchildren and the second child execute.
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(9.0, SUB_INTENT_LIST), (9.0, SUB_INTENT_LIST), (2.0, "")])
        ctx = _budget_context(host, agents, 5.0)

        results = PipelineEngine(ctx).run()

        execute = results["execute"]
        assert execute.status == StageStatus.SUCCESS
        first, second = execute.payload["children"]
        assert first["decomposed"] is True
        assert "decomposed" not in second
        grandchildren = first["children"]
        assert [g["slug"] for g in grandchildren] == ["add-the-parser", "add-the-cli-flag"]
        # The first grandchild shares its parent's slug yet runs on its own branch, however fast the run.
        assert [g["intent_branch"] for g in grandchildren] == [
            first["intent_branch"].removesuffix("/_") + suffix
            for suffix in ("--1-add-the-parser/_", "--2-add-the-cli-flag/_")
        ]
        assert [p["sub_intent_depth"] for p in agents.intent_payloads[1:]] == [1, 2, 2, 1]
        assert [p["target_branch"] for p in agents.intent_payloads[2:4]] == [first["intent_branch"]] * 2
        summary = flow_module.format_decomposition_summary(execute)
        assert summary == [
            "Decomposed into 2 sub-intent(s); merge each child PR into the parent intent branch:",
            f"  1. add-the-parser: decomposed into 2 sub-intent(s), so it has no PR of its own; merge its child PRs "
            f"below into '{first['intent_branch']}', then raise a PR from '{first['intent_branch']}' to "
            f"'{ctx.intent_branch}' (not yet automated):",
            f"     1. add-the-parser: {grandchildren[0]['execution_branch']} ({grandchildren[0]['status']})",
            f"     2. add-the-cli-flag: {grandchildren[1]['execution_branch']} ({grandchildren[1]['status']})",
            f"  2. add-the-cli-flag: {second['execution_branch']} ({second['status']})",
            f"Pending (not yet automated): after the child PRs merge, raise a PR from '{ctx.intent_branch}' to 'main'.",
        ]
        # The decomposed child's ledger-only execution branch is never offered as a PR to merge.
        assert not any(first["execution_branch"] in line for line in summary)

    @pytest.mark.parametrize(
        ("parent", "index", "slug", "expected"),
        [
            ("I-1-root/_", 1, "add-the-parser", "I-1-root--1-add-the-parser"),
            ("I-1-root--1-a/_", 2, "add-the-parser", "I-1-root--1-a--2-add-the-parser"),
            ("I-1-root/_", 3, " Add the--CLI flag! ", "I-1-root--3-add-the-cli-flag"),
            ("I-1-root/_", 4, "--", "I-1-root--4-sub-intent-4"),
        ],
        ids=["child", "grandchild", "unsanitised-slug", "empty-slug"],
    )
    def test_child_intent_branch_is_derived_from_parent_and_position(self, parent, index, slug, expected):
        assert flow_module._child_intent_branch(parent, index, slug) == expected

    def test_same_slug_child_created_in_the_same_second_gets_its_own_intent_branch(self, flow_repos, tmp_path):
        host, origin = flow_repos
        # The first grandchild shares its parent's slug, and a frozen clock creates every intent in the same second.
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(9.0, SUB_INTENT_LIST), (9.0, SUB_INTENT_LIST), (2.0, "")])
        ctx = _budget_context(host, agents, 5.0)

        with patch.object(flow_module.time, "time", return_value=1793098938.0):
            results = PipelineEngine(ctx).run()

        # Every intent was dispatched to the intent creator on a branch of its own: none reused another's.
        branches = [p["branch"] for p in agents.intent_payloads]
        assert len(branches) == len(set(branches)) == 5
        root = ctx.intent_branch.removesuffix("/_")
        assert branches == [
            "I-1793098938-budget-test",
            f"{root}--1-add-the-parser",
            f"{root}--1-add-the-parser--1-add-the-parser",
            f"{root}--1-add-the-parser--2-add-the-cli-flag",
            f"{root}--2-add-the-cli-flag",
        ]
        assert [p["sub_intent_depth"] for p in agents.intent_payloads[1:]] == [1, 2, 2, 1]
        assert results["execute"].status == StageStatus.SUCCESS
        first = results["execute"].payload["children"][0]
        assert [g["intent_branch"] for g in first["children"]] == [f"{b}/_" for b in branches[2:4]]

    def test_child_halting_at_its_own_review_is_a_success_and_records_its_pr(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(9.0, SUB_INTENT_LIST), (2.0, "")])
        ctx = _budget_context(host, agents, 5.0)
        ctx.skip_review = False
        reviewed: list[str | None] = []

        def fake_review(child_ctx: FlowContext) -> StageResult:
            # The payload shape run_review_stage returns at its consensus halt: the PR result sits under "pr".
            number = 101 + len(reviewed)
            reviewed.append(child_ctx.execution_branch)
            return StageResult(
                stage=FlowStage.REVIEW,
                status=StageStatus.HALTED_FOR_HUMAN,
                payload={
                    "consensus": {"approved": True, "score": 1.0, "iterations": 1, "status": "approved"},
                    "halted_for_human": True,
                    "pr": {
                        "attempted": True,
                        "success": True,
                        "action": "create",
                        "pr_number": number,
                        "output": f"https://github.com/example/app/pull/{number}",
                    },
                },
            )

        with patch("sandbox_executor.flow.run_review_stage", side_effect=fake_review):
            results = PipelineEngine(ctx).run()

        execute = results["execute"]
        assert execute.status == StageStatus.SUCCESS
        children = execute.payload["children"]
        # Both children ran although the first stopped at its own merge boundary.
        assert [c["slug"] for c in children] == ["add-the-parser", "add-the-cli-flag"]
        assert reviewed == [c["execution_branch"] for c in children]
        assert [(c["pr_url"], c["pr_number"]) for c in children] == [
            ("https://github.com/example/app/pull/101", 101),
            ("https://github.com/example/app/pull/102", 102),
        ]
        summary = flow_module.format_decomposition_summary(execute)
        assert summary[1].startswith("  1. add-the-parser: https://github.com/example/app/pull/101 (")
        assert results["review"].payload == {"reason": "decomposed"}

    def test_replan_whose_stage_2_fails_ends_the_run_in_stage_2(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, "")])
        ctx = _budget_context(host, agents, 5.0)
        real_plan_stage = flow_module.run_plan_stage
        calls: list[int] = []

        def plan_then_fail(plan_ctx: FlowContext) -> StageResult:
            calls.append(1)
            if len(calls) == 1:
                return real_plan_stage(plan_ctx)
            return StageResult(stage=FlowStage.PLAN, status=StageStatus.FAILED, error="Plan stage failed: no plan")

        with patch("sandbox_executor.flow.run_plan_stage", side_effect=plan_then_fail):
            results = PipelineEngine(ctx).run()

        assert ctx.replan_attempts == 1
        assert results["plan"].status == StageStatus.FAILED
        assert results["plan"].error == "Plan stage failed: no plan"
        # The rejected plan is dropped before re-planning, so the checkpoint never resumes from the over-budget plan.
        assert ctx.plan_branch is None
        assert results["execute"].payload["over_budget"] is True
        assert "review" not in results
        assert ctx.logs[-1].endswith("Pipeline halted due to failure in stage 'plan'.")
        assert "replan_feedback" not in ctx.intent_data
        assert "HOLON_REPLAN_FEEDBACK" not in os.environ

    def test_first_failing_child_stops_the_loop(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(
            tmp_path, origin, plans=[(9.0, SUB_INTENT_LIST), (2.0, "")], fail_planning_for="add-the-parser"
        )
        ctx = _budget_context(host, agents, 5.0)

        results = PipelineEngine(ctx).run()

        execute = results["execute"]
        assert execute.status == StageStatus.FAILED
        assert "sub-intent 1/2 'add-the-parser' failed at stage 'plan'" in (execute.error or "")
        assert [p["slug"] for p in agents.intent_payloads[1:]] == ["add-the-parser"]
        assert "review" not in results
        summary = flow_module.format_decomposition_summary(execute)
        assert summary[0] == (
            "Decomposed into 2 sub-intent(s); 1 of 2 ran before Stage 3 failed, so no child PR is ready to merge:"
        )
        assert len(summary) == 2
        assert not any("merge each child PR" in line or line.startswith("Pending") for line in summary)

    def test_sub_intent_depth_is_bounded(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(2.0, SUB_INTENT_LIST)])
        ctx = _budget_context(host, agents, 5.0, sub_intent_depth=MAX_SUB_INTENT_DEPTH)

        results = PipelineEngine(ctx).run()

        assert results["execute"].status == StageStatus.FAILED
        assert "deeper than the limit" in (results["execute"].error or "")
        # The pre-dispatch check refuses the over-deep list, so no executor container runs.
        assert results["execute"].payload["dispatched"] is False
        assert agents.roles() == ["intent-creator", "planner"]

    def test_sub_intent_depth_is_bounded_when_the_precheck_cannot_read_the_plan(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(2.0, SUB_INTENT_LIST)])
        ctx = _budget_context(host, agents, 5.0, sub_intent_depth=MAX_SUB_INTENT_DEPTH)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

        # With no pre-dispatch decision, the executor's decomposed row is the only signal: the depth bound must hold.
        with patch("sandbox_executor.flow._plan_budget_decision", return_value=None):
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "deeper than the limit" in (res.error or "")
        assert res.payload["decomposed"] is True
        assert res.payload["children"] == []
        assert flow_module.format_decomposition_summary(res) == [
            "Decomposed into 2 sub-intent(s); 0 of 2 ran before Stage 3 failed, so no child PR is ready to merge:"
        ]
        # The executor ran once and no child intent was created.
        assert agents.roles() == ["intent-creator", "planner", "executor"]
        assert len(agents.intent_payloads) == 1

    @pytest.mark.parametrize("sub_intents", [[], None, ["not-a-dict"]], ids=["empty", "missing", "no-dict-items"])
    def test_decomposed_row_without_sub_intent_items_fails_stage(self, sub_intents):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent", "goal": "Change the application"})
        row: dict[str, Any] = {"status": "decomposed", "plan_branch": "I-1-parent/P-2-agent-model/_"}
        if sub_intents is not None:
            row["sub_intents"] = sub_intents

        with patch("sandbox_executor.flow._dispatch_role") as dispatch:
            res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", row, {"execution_id": "E-1"})

        assert res.status == StageStatus.FAILED
        assert "without any sub-intent list items" in (res.error or "")
        assert res.payload["sub_intents"] == []
        assert res.payload["children"] == []
        dispatch.assert_not_called()

    def test_format_decomposition_summary_is_empty_for_undecomposed_execute(self):
        plain = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"execution_id": "E-1"})
        assert flow_module.format_decomposition_summary(plain) == []
        assert flow_module.format_decomposition_summary(None) == []

    def test_replan_attempts_round_trip_through_checkpoints(self):
        ctx = FlowContext(token_reduce=False, replan_attempts=2)
        assert FlowContext.from_dict(ctx.to_dict()).replan_attempts == 2
        legacy = ctx.to_dict()
        del legacy["replan_attempts"]
        assert FlowContext.from_dict(legacy).replan_attempts == 0
        # A hand-edited or foreign checkpoint may carry null or a numeric string; both load as an int.
        for recorded, expected in ((None, 0), ("1", 1)):
            loaded = FlowContext.from_dict({**legacy, "replan_attempts": recorded}).replan_attempts
            assert (loaded, type(loaded)) == (expected, int)

    def test_replan_restores_a_pre_existing_replan_feedback_variable(self, flow_repos, tmp_path, monkeypatch):
        host, origin = flow_repos
        monkeypatch.setenv("HOLON_REPLAN_FEEDBACK", "operator value")
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, ""), (4.0, "")])
        ctx = _budget_context(host, agents, 5.0)

        results = PipelineEngine(ctx).run()

        assert results["execute"].status == StageStatus.SUCCESS
        assert "entropy 12.6" in (agents.replan_feedback_seen[1] or "")
        assert os.environ.get("HOLON_REPLAN_FEEDBACK") == "operator value"

    def test_failed_decomposed_execute_does_not_skip_review_or_calibration(self):
        ctx = FlowContext(token_reduce=False)
        engine = PipelineEngine(ctx)
        for status, expected in ((StageStatus.FAILED, False), (StageStatus.SUCCESS, True)):
            ctx.stage_results["execute"] = StageResult(
                stage=FlowStage.EXECUTE, status=status, payload={"decomposed": True}
            )
            assert engine._parent_was_decomposed() is expected

    @pytest.mark.parametrize(("recorded", "expected"), [(-3, 0), ("-1", 0), (None, 0), ("deep", 0), ("2", 2)], ids=str)
    def test_sub_intent_depth_is_never_negative(self, recorded, expected):
        # A negative or unreadable depth counts as a root intent, so children start at depth 1.
        assert flow_module._sub_intent_depth({"sub_intent_depth": recorded}) == expected

    def test_decomposed_summary_names_the_parent_intents_own_target_branch(self):
        # The pending parent PR targets the parent intent's target_branch, not always main.
        ctx = FlowContext(
            token_reduce=False,
            intent_data={"slug": "parent", "goal": "Change the application", "target_branch": "release/1.x"},
        )
        ctx.intent_branch = "I-1-parent/_"
        row = {
            "status": "decomposed",
            "plan_branch": "I-1-parent/P-2-agent-model/_",
            "sub_intents": [{"slug": "first", "goal": "Add the parser"}],
        }
        done = {"intent": StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)}

        with patch.object(PipelineEngine, "run", return_value=done):
            res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", row, {"execution_id": "E-1"})

        assert res.status == StageStatus.SUCCESS
        assert res.payload["parent_target_branch"] == "release/1.x"
        assert flow_module.format_decomposition_summary(res)[-1] == (
            "Pending (not yet automated): after the child PRs merge, raise a PR from 'I-1-parent/_' to 'release/1.x'."
        )

    def test_child_pipeline_that_runs_no_stage_fails_the_parent(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent", "goal": "Change the application"})
        ctx.intent_branch = "I-1-parent/_"
        row = {
            "status": "decomposed",
            "plan_branch": "I-1-parent/P-2-agent-model/_",
            "sub_intents": [{"slug": "first", "goal": "Add the parser"}, {"slug": "second", "goal": "Add the flag"}],
        }

        with patch.object(PipelineEngine, "run", return_value={}) as run:
            res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", row, {"execution_id": "E-1"})

        assert res.status == StageStatus.FAILED
        assert res.error == "Execute stage failed: sub-intent 1/2 'first' failed at stage 'none': no stage ran"
        assert [c["slug"] for c in res.payload["children"]] == ["first"]
        assert run.call_count == 1

    def test_child_context_inherits_or_resets_every_field(self):
        """Table-driven: every FlowContext field is either inherited from the parent or reset for the new child."""
        dispatcher = MagicMock()
        parent_values: dict[str, Any] = {
            "repo_dir": "/srv/parent-repo",
            "agent": "codex",
            "model": "parent-only-model",
            "dry_run": True,
            "auto_calibrate": False,
            "token_reduce": True,
            "skip_push": True,
            "skip_review": True,
            "skip_calibrate": True,
            "max_review_iterations": 7,
            "mitm_web": True,
            "dispatcher": dispatcher,
        }
        parent = FlowContext(
            intent_data={"slug": "parent", "entropy_budget": 5.0, "labels": ["parent-label"]},
            intent_branch="I-1-parent/_",
            plan_branch="I-1-parent/P-2-agent-model/_",
            execution_branch="I-1-parent/P-2-agent-model/E-3-agent-model/_",
            calibrated_branch="I-1-parent/P-2-agent-model/E-3-agent-model/calibrated",
            stage_results={"intent": StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)},
            logs=["parent log line"],
            # A non-current version, as FlowContext.from_dict accepts from a malformed checkpoint (e.g. -1).
            schema_version=-1,
            replan_attempts=1,
            **parent_values,
        )
        child_intent = {"slug": "child", "goal": "Add the parser"}
        expected: dict[str, Any] = {
            **parent_values,
            "intent_data": child_intent,
            "intent_branch": None,
            "plan_branch": None,
            "execution_branch": None,
            "calibrated_branch": None,
            "stage_results": {},
            "logs": [],
            "schema_version": flow_module.CURRENT_CONTEXT_SCHEMA_VERSION,
            "replan_attempts": 0,
        }
        defaults = FlowContext(token_reduce=False)
        for name, value in parent_values.items():
            assert getattr(defaults, name) != value, f"{name}: parent value must differ from the default"

        # Pin proxy detection to the opposite of the parent's token_reduce so a dropped copy cannot pass by detection.
        with patch("sandbox_executor.cli.detect_holon_coherence", return_value=False):
            child = flow_module._child_context(parent, child_intent)

        assert set(expected) == {f.name for f in dataclasses.fields(FlowContext)}, "classify every new field"
        for name, value in expected.items():
            assert getattr(child, name) == value, name
        assert child.dispatcher is dispatcher

    @staticmethod
    def _decide(rows: list[dict[str, Any]], files: dict[str, str]) -> Any:
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent", "entropy_budget": 5.0})
        ctx.plan_branch = "I-1-parent/P-2-agent-model/_"
        with (
            patch("sandbox_executor.flow._resolve_branch_ref", return_value="refs/remotes/origin/plan"),
            patch("sandbox_executor.flow._read_ledger_rows", return_value=rows),
            patch("sandbox_executor.flow._git_show_text", side_effect=lambda _repo, _ref, path: files.get(path)),
        ):
            return flow_module._plan_budget_decision(ctx)

    def test_budget_precheck_reads_the_newest_plan_row(self):
        rows = [
            {"plan_id": "P-2-agent-model", "entropy": 12.6, "plan_file": "plans/P-2-agent-model.md"},
            {"plan_id": "P-2-agent-model", "entropy": 2.0, "plan_file": "plans/P-2-agent-model.md"},
        ]
        decision = self._decide(rows, {"plans/P-2-agent-model.md": "# Plan\n"})
        assert (decision.decision, decision.entropy) == ("execute", 2.0)

    @pytest.mark.parametrize("plan_file", ["plans/P-x/nested.md", "plans/P-x/../../README.md"])
    def test_budget_precheck_ignores_a_plan_file_below_a_subdirectory_of_plans(self, plan_file):
        rows = [{"plan_id": "P-2-agent-model", "entropy": 9.0, "plan_file": plan_file}]
        files = {"plans/P-2-agent-model.md": "# Plan\n" + SUB_INTENT_LIST, plan_file: "# Plan\n"}
        assert self._decide(rows, files).decision == "decomposed"

    def test_budget_precheck_with_a_missing_plan_file_judges_the_row_alone(self):
        rows = [{"plan_id": "P-2-agent-model", "entropy": 9.0, "plan_file": "plans/P-2-agent-model.md"}]
        decision = self._decide(rows, {})
        assert (decision.decision, decision.entropy, decision.entropy_budget) == ("over_budget", 9.0, 5.0)

    def test_budget_precheck_ignores_a_plan_file_outside_plans(self):
        rows = [{"plan_id": "P-2-agent-model", "entropy": 9.0, "plan_file": "README.md"}]
        files = {"plans/P-2-agent-model.md": "# Plan\n" + SUB_INTENT_LIST, "README.md": "# Readme\n"}
        decision = self._decide(rows, files)
        assert decision.decision == "decomposed"
        assert [i["goal"] for i in decision.sub_intents] == ["Add the parser", "Add the CLI flag"]

    def test_only_the_review_stage_constructs_a_halt(self):
        source = Path(flow_module.__file__).read_text(encoding="utf-8")
        execute_body = source.split("def run_execute_stage(", 1)[1].split("\ndef ", 1)[0]
        assert "HALTED_FOR_HUMAN" not in execute_body
        assert source.count("status=StageStatus.HALTED_FOR_HUMAN") == 1
        review_body = source.split("def run_review_stage(", 1)[1].split("\ndef ", 1)[0]
        assert "status=StageStatus.HALTED_FOR_HUMAN" in review_body

    # --- Depth cap boundary -------------------------------------------------------------------------------------

    def test_sub_intent_depth_error_allows_children_at_the_cap_and_refuses_one_deeper(self):
        at_cap = FlowContext(token_reduce=False, intent_data={"sub_intent_depth": MAX_SUB_INTENT_DEPTH - 1})
        past_cap = FlowContext(token_reduce=False, intent_data={"sub_intent_depth": MAX_SUB_INTENT_DEPTH})

        assert flow_module._sub_intent_depth_error(at_cap) is None
        assert flow_module._sub_intent_depth_error(past_cap) == (
            f"Execute stage failed: the plan lists a sub-intent list at depth {MAX_SUB_INTENT_DEPTH + 1}, deeper than "
            f"the limit of {MAX_SUB_INTENT_DEPTH}; refusing to recurse further."
        )

    @staticmethod
    def _run_children(
        intent_data: dict[str, Any], row: dict[str, Any], child_results: dict[str, StageResult] | None = None, **ctx_kw
    ) -> tuple[StageResult, FlowContext, list[FlowContext]]:
        """Run _run_decomposed_plan with each child pipeline replaced by a stub; return result, parent, children."""
        ctx = FlowContext(token_reduce=False, intent_data=intent_data, **ctx_kw)
        children: list[FlowContext] = []
        done = child_results or {"intent": StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)}

        def fake_run(engine: PipelineEngine) -> dict[str, StageResult]:
            children.append(engine.context)
            return done

        with patch.object(PipelineEngine, "run", autospec=True, side_effect=fake_run):
            res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", row, {"execution_id": "E-1"})
        return res, ctx, children

    _ROW: ClassVar[dict[str, Any]] = {
        "status": "decomposed",
        "plan_branch": "I-1-parent/P-2-agent-model/_",
        "entropy_budget": 5.0,
        "sub_intents": [{"slug": "first", "description": "Add the parser\n- with tests", "goal": "Add the parser"}],
    }

    def test_child_at_exactly_the_depth_cap_runs(self):
        res, ctx, children = self._run_children(
            {"slug": "parent", "sub_intent_depth": MAX_SUB_INTENT_DEPTH - 1},
            self._ROW,
            intent_branch="I-1-parent/_",
            plan_branch="I-1-parent/P-2-agent-model/_",
        )

        assert res.status == StageStatus.SUCCESS
        assert [c.intent_data["sub_intent_depth"] for c in children] == [MAX_SUB_INTENT_DEPTH]
        assert ctx.logs[-1].endswith(
            "] Stage 3 completed: all 1 sub-intent(s) of 'I-1-parent/P-2-agent-model/_' ran through the pipeline."
        )

    def test_child_one_deeper_than_the_cap_is_refused(self):
        res, _ctx, children = self._run_children(
            {"slug": "parent", "sub_intent_depth": MAX_SUB_INTENT_DEPTH}, self._ROW, intent_branch="I-1-parent/_"
        )

        assert res.status == StageStatus.FAILED
        assert "deeper than the limit" in (res.error or "")
        assert children == []

    # --- Child intent payloads ----------------------------------------------------------------------------------

    def test_child_payload_fallbacks_and_copies(self):
        labels = ["flow"]
        row = {
            "status": "decomposed",
            "plan_branch": "I-1-parent/P-2-agent-model/_",
            "sub_intents": [{"goal": "Add the parser"}, {"description": "Add the flag\n- detail"}, "not-an-item"],
        }
        res, ctx, children = self._run_children({"slug": "parent", "labels": labels}, row)

        assert res.status == StageStatus.SUCCESS
        # A checkpoint without intent_branch still names the parent from the plan branch.
        assert res.payload["parent_branch"] == "I-1-parent/_"
        assert [c.intent_data["target_branch"] for c in children] == ["I-1-parent/_"] * 2
        first, second = (c.intent_data for c in children)
        assert (first["slug"], first["description"], first["goal"]) == (
            "sub-intent-1",
            "Add the parser",
            "Add the parser",
        )
        assert (second["slug"], second["goal"]) == ("sub-intent-2", "Add the flag\n- detail")
        assert second["description"] == "Add the flag\n- detail"
        # A row without entropy_budget hands no budget down, so each child resolves its own.
        assert "entropy_budget" not in first
        # Labels are copied, not shared, so a child cannot change its parent's labels.
        assert first["labels"] == labels and first["labels"] is not labels
        assert res.payload["sub_intents"] == row["sub_intents"][:2]
        assert ctx.stage_results["execute"] is res
        assert any(
            line.endswith("] Stage 3: running sub-intent 1/2 'sub-intent-1' through the full pipeline.")
            for line in ctx.logs
        )

    def test_child_payload_prefers_the_intent_branch_and_hands_down_the_budget(self):
        row = {k: v for k, v in self._ROW.items() if k != "plan_branch"}
        res, _ctx, children = self._run_children(
            {"slug": "parent", "labels": "flow"}, row, intent_branch="I-9-parent/_"
        )

        assert res.payload["parent_branch"] == "I-9-parent/_"
        (child,) = (c.intent_data for c in children)
        assert child["entropy_budget"] == 5.0
        assert (child["description"], child["goal"]) == ("Add the parser\n- with tests", "Add the parser")
        # Labels that are not a list are not handed down.
        assert "labels" not in child

    @pytest.mark.parametrize("execute_failed_too", [False, True], ids=["plan-only", "plan-and-execute"])
    def test_failed_child_names_its_own_error_and_final_stage(self, execute_failed_too):
        failed = {
            "intent": StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS),
            "plan": StageResult(stage=FlowStage.PLAN, status=StageStatus.FAILED, error="Plan stage failed: no plan"),
        }
        if execute_failed_too:
            # A child whose re-plan Stage 2 failed holds a failed plan and a failed execute: the first one is named.
            failed["execute"] = StageResult(
                stage=FlowStage.EXECUTE, status=StageStatus.FAILED, error="Execute stage failed: over budget"
            )
        res, _ctx, _children = self._run_children(
            {"slug": "parent"}, self._ROW, child_results=failed, intent_branch="I-1-parent/_"
        )

        assert res.error == (
            "Execute stage failed: sub-intent 1/1 'first' failed at stage 'plan': Plan stage failed: no plan"
        )
        (child,) = res.payload["children"]
        assert child["final_stage"] == ("execute" if execute_failed_too else "plan")
        assert child["status"] == "failed"

    @pytest.mark.parametrize("sub_intents", [5, "abc"], ids=["number", "string"])
    def test_decomposed_row_with_a_non_list_sub_intent_value_fails_stage(self, sub_intents):
        row = {"status": "decomposed", "plan_branch": "I-1-parent/P-2-agent-model/_", "sub_intents": sub_intents}
        res, _ctx, children = self._run_children({"slug": "parent"}, row, intent_branch="I-1-parent/_")

        assert res.status == StageStatus.FAILED
        assert "without any sub-intent list items" in (res.error or "")
        assert children == []

    # --- Child PR reporting and the flow summary ------------------------------------------------------------------

    @pytest.mark.parametrize(
        ("pr", "expected"),
        [
            (
                {"pr_url": "https://github.com/o/r/pull/7", "pr_number": 7, "output": ""},
                ("https://github.com/o/r/pull/7", 7),
            ),
            (
                {
                    "pr_number": 5,
                    "output": "see https://github.com/o/r/compare/x then https://github.com/o/r/pull/5 ok",
                },
                ("https://github.com/o/r/pull/5", 5),
            ),
            ("not a dict", (None, None)),
            (None, (None, None)),
        ],
        ids=["explicit-url", "url-from-output", "not-a-dict", "missing"],
    )
    def test_child_review_pr(self, pr, expected):
        payload = {} if pr is None else {"pr": pr}
        results = {"review": StageResult(stage=FlowStage.REVIEW, status=StageStatus.HALTED_FOR_HUMAN, payload=payload)}

        assert flow_module._child_review_pr(results) == expected
        assert flow_module._child_review_pr({}) == (None, None)

    def test_failed_decomposition_summary_counts_the_listed_items_not_only_the_children_that_ran(self):
        items = [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}, "not-an-item"]
        ran_one = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.FAILED,
            payload={
                "decomposed": True,
                "sub_intents": items,
                "children": [{"slug": "a", "execution_branch": "E-a", "status": "failed"}],
            },
        )
        assert flow_module.format_decomposition_summary(ran_one) == [
            "Decomposed into 3 sub-intent(s); 1 of 3 ran before Stage 3 failed, so no child PR is ready to merge:",
            "  1. a: E-a (failed)",
        ]
        # A depth refusal inside the decomposed run fails before any child runs.
        ran_none = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.FAILED,
            payload={"decomposed": True, "sub_intents": items, "children": []},
        )
        assert flow_module.format_decomposition_summary(ran_none) == [
            "Decomposed into 3 sub-intent(s); 0 of 3 ran before Stage 3 failed, so no child PR is ready to merge:",
        ]
        succeeded = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={
                "decomposed": True,
                "sub_intents": items[:2],
                "children": [{"slug": "a", "status": "s"}, {"slug": "b", "status": "s"}],
            },
        )
        assert flow_module.format_decomposition_summary(succeeded)[0] == (
            "Decomposed into 2 sub-intent(s); merge each child PR into the parent intent branch:"
        )

    def test_format_decomposition_summary_fallbacks(self):
        children = [
            {
                "slug": "a",
                "pr_url": "https://x/pull/1",
                "execution_branch": "E-a",
                "intent_branch": "I-a",
                "status": "s",
            },
            {"slug": "b", "execution_branch": "E-b", "intent_branch": "I-b", "status": "s"},
            {"slug": "c", "intent_branch": "I-c", "status": "s"},
            {"slug": "d", "status": "s"},
            "not-a-child",
        ]
        result = StageResult(
            stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"decomposed": True, "children": children}
        )

        assert flow_module.format_decomposition_summary(result) == [
            "Decomposed into 4 sub-intent(s); merge each child PR into the parent intent branch:",
            "  1. a: https://x/pull/1 (s)",
            "  2. b: E-b (s)",
            "  3. c: I-c (s)",
            "  4. d: no branch (s)",
            "Pending (not yet automated): after the child PRs merge, raise a PR from 'the parent intent branch' to "
            "'its target branch'.",
        ]

    def test_failed_decomposition_summary_nests_a_decomposed_child_without_merge_guidance(self):
        children = [
            {
                "slug": "a",
                "intent_branch": "I-a/_",
                "execution_branch": "E-a",
                "status": "skipped",
                "decomposed": True,
                "children": [{"slug": "x", "pr_url": "https://x/pull/3", "status": "s"}, "not-a-child"],
            },
            {"slug": "b", "execution_branch": "E-b", "status": "failed"},
        ]
        result = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.FAILED,
            payload={"decomposed": True, "sub_intents": [{"slug": "a"}, {"slug": "b"}], "children": children},
        )

        assert flow_module.format_decomposition_summary(result) == [
            "Decomposed into 2 sub-intent(s); 2 of 2 ran before Stage 3 failed, so no child PR is ready to merge:",
            "  1. a: decomposed into 1 sub-intent(s) (skipped):",
            "     1. x: https://x/pull/3 (s)",
            "  2. b: E-b (failed)",
        ]

    def test_decomposition_summary_names_each_decomposed_level_after_its_own_parent(self):
        # The rendering recurses, so a decomposed grandchild points at its own parent, not at the root intent branch.
        grandchild = {
            "slug": "g",
            "intent_branch": "I-g/_",
            "decomposed": True,
            "children": [{"slug": "x", "pr_url": "https://x/pull/9", "status": "s"}],
        }
        child = {"slug": "a", "intent_branch": "I-a/_", "decomposed": True, "children": [grandchild]}
        result = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"decomposed": True, "sub_intents": [{"slug": "a"}], "children": [child], "parent_branch": "I-p/_"},
        )

        assert flow_module.format_decomposition_summary(result)[1:4] == [
            "  1. a: decomposed into 1 sub-intent(s), so it has no PR of its own; merge its child PRs below into "
            "'I-a/_', then raise a PR from 'I-a/_' to 'I-p/_' (not yet automated):",
            "     1. g: decomposed into 1 sub-intent(s), so it has no PR of its own; merge its child PRs below into "
            "'I-g/_', then raise a PR from 'I-g/_' to 'I-a/_' (not yet automated):",
            "        1. x: https://x/pull/9 (s)",
        ]

    def test_successful_decomposition_summary_header_counts_the_listed_items(self):
        # The header counts the dict items the plan listed, independently of how many child summaries are present.
        result = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={
                "decomposed": True,
                "sub_intents": [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}, "not-an-item"],
                "children": [{"slug": "a", "status": "s"}],
            },
        )
        assert flow_module.format_decomposition_summary(result)[0] == (
            "Decomposed into 3 sub-intent(s); merge each child PR into the parent intent branch:"
        )

    @pytest.mark.parametrize("sub_intents", ["abc", 5], ids=["string", "number"])
    def test_decomposition_summary_with_a_non_list_sub_intent_value_counts_the_children(self, sub_intents):
        result = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"decomposed": True, "sub_intents": sub_intents, "children": [{"slug": "a", "status": "s"}]},
        )
        assert flow_module.format_decomposition_summary(result)[0] == (
            "Decomposed into 1 sub-intent(s); merge each child PR into the parent intent branch:"
        )

    # --- Pre-dispatch budget decision -----------------------------------------------------------------------------

    def test_budget_precheck_without_a_plan_branch_reads_nothing(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"})
        with patch("sandbox_executor.flow._resolve_branch_ref") as resolve:
            assert flow_module._plan_budget_decision(ctx) is None
        resolve.assert_not_called()

    def test_budget_precheck_with_an_unresolvable_plan_ref_reads_no_ledger(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"})
        ctx.plan_branch = "I-1-parent/P-2-agent-model/_"
        with (
            patch("sandbox_executor.flow._resolve_branch_ref", return_value=None),
            patch("sandbox_executor.flow._read_ledger_rows") as read_rows,
        ):
            assert flow_module._plan_budget_decision(ctx) is None
        read_rows.assert_not_called()

    def test_budget_precheck_ignores_rows_of_other_plans(self):
        rows = [
            {"plan_id": "P-2-agent-model", "entropy": 2.0, "plan_file": "plans/P-2-agent-model.md"},
            {"plan_id": "P-3-agent-model", "entropy": 12.6, "plan_file": "plans/P-3-agent-model.md"},
        ]
        files = {"plans/P-2-agent-model.md": "# Plan\n", "plans/P-3-agent-model.md": "# Plan\n"}
        assert self._decide(rows, files).decision == "execute"
        assert self._decide(rows[1:], files) is None

    def test_budget_precheck_reads_the_recorded_plan_file_and_the_intent_budget(self):
        rows = [{"plan_id": "P-2-agent-model", "entropy": 9.0, "plan_file": "plans/P-2-agent-model-v2.md"}]
        decision = self._decide(rows, {"plans/P-2-agent-model-v2.md": "# Plan\n" + SUB_INTENT_LIST})
        assert decision.decision == "decomposed"
        # No list and no budget on the row: the intent's budget (5.0, not the default) decides.
        decision = self._decide(rows, {"plans/P-2-agent-model-v2.md": "# Plan\n"})
        assert (decision.decision, decision.entropy_budget) == ("over_budget", 5.0)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(12.6, "12.6"), (5, "5"), (5.0, "5"), (True, "True"), (None, "unknown"), ("lots", "lots")],
        ids=str,
    )
    def test_format_entropy(self, value, expected):
        assert flow_module._format_entropy(value) == expected

    # --- Re-plan loop checkpoints ---------------------------------------------------------------------------------

    @staticmethod
    def _engine_run(
        ctx: FlowContext, plans: list[StageResult | BaseException], executes: list[StageResult]
    ) -> tuple[dict[str, StageResult], list[dict[str, Any]]]:
        """Run the engine with stub stages; return the results and every checkpoint the engine saved, in order."""
        plan_iter, exec_iter = iter(plans), iter(executes)
        saved: list[dict[str, Any]] = []

        def plan(context: FlowContext) -> StageResult:
            result = next(plan_iter)
            if isinstance(result, BaseException):
                raise result
            if result.status == StageStatus.SUCCESS:
                context.plan_branch = f"I-1-parent/P-{len(saved)}-agent-model/_"
            return result

        def snapshot(context: FlowContext, filepath: str | None = None) -> str:
            saved.append(json.loads(json.dumps(context.to_dict())))
            return filepath or "checkpoint.json"

        intent = StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)
        with (
            patch("sandbox_executor.flow.run_intent_stage", return_value=intent),
            patch("sandbox_executor.flow.run_plan_stage", side_effect=plan),
            patch("sandbox_executor.flow.run_execute_stage", side_effect=lambda _c: next(exec_iter)),
            patch("sandbox_executor.flow.save_checkpoint", side_effect=snapshot),
        ):
            results = PipelineEngine(ctx).run()
        return results, saved

    @staticmethod
    def _over_budget() -> StageResult:
        return StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.FAILED,
            error="Execute stage failed: over budget",
            payload={"over_budget": True, "entropy": 12.6, "entropy_budget": 5.0},
        )

    def test_replan_checkpoints_the_failed_replan(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_review=True, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        no_plan = StageResult(stage=FlowStage.PLAN, status=StageStatus.FAILED, error="Plan stage failed: no plan")

        _results, saved = self._engine_run(ctx, [ok, no_plan], [self._over_budget()])

        last = saved[-1]
        assert last["replan_attempts"] == 1
        assert last["plan_branch"] is None
        assert last["stage_results"]["plan"]["status"] == "failed"
        assert sum("Entering stage: PLAN" in line for line in ctx.logs) == 2

    def test_replan_checkpoints_the_new_execute_result(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_review=True, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        other = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.FAILED, error="Execute stage failed: push")

        _results, saved = self._engine_run(ctx, [ok, ok], [self._over_budget(), other])

        last = saved[-1]
        assert last["replan_attempts"] == 1
        assert last["stage_results"]["execute"]["error"] == "Execute stage failed: push"
        assert last["plan_branch"] == ctx.plan_branch is not None
        assert sum("Entering stage: EXECUTE" in line for line in ctx.logs) == 2

    def test_exhausted_replans_checkpoint_the_final_error(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_review=True, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        attempts = MAX_REPLAN_ATTEMPTS + 1

        results, saved = self._engine_run(ctx, [ok] * attempts, [self._over_budget() for _ in range(attempts)])

        error = results["execute"].error
        assert error == (
            f"Execute stage failed: plan entropy 12.6 exceeds the entropy budget 5 after {MAX_REPLAN_ATTEMPTS} "
            "re-plan attempts and the plan lists no sub-intent list."
        )
        assert saved[-1]["stage_results"]["execute"]["error"] == error
        assert saved[-1]["stage_results"]["execute"]["payload"]["replan_attempts"] == MAX_REPLAN_ATTEMPTS
        assert any(line.endswith("] " + error) for line in ctx.logs)

    def test_decomposed_parent_checkpoints_its_skipped_review_and_calibration(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_review=True, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        decomposed = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"decomposed": True})

        results, saved = self._engine_run(ctx, [ok], [decomposed])

        for stage in ("review", "calibrate"):
            assert any(
                line.endswith(f"] Stage '{stage}' skipped: the plan was decomposed into child pipelines.")
                for line in ctx.logs
            )
        assert results["calibrate"].payload == {"reason": "decomposed"}
        assert saved[-1]["stage_results"]["calibrate"]["payload"] == {"reason": "decomposed"}

    def test_undecomposed_success_runs_review_and_calibration_normally(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_review=True, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        plain = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"execution_id": "E-1"})

        results, _saved = self._engine_run(ctx, [ok], [plain])

        assert results["review"].payload == {"skipped_by_flag": True}
        assert results["calibrate"].payload == {"skipped_by_flag": True}

    @pytest.mark.parametrize(
        ("feedback", "expected"),
        [(" \n\t ", None), ("  Re-plan: entropy 12.6  ", "Re-plan: entropy 12.6")],
        ids=["blank", "padded"],
    )
    def test_plan_stage_forwards_only_non_blank_stripped_feedback(self, feedback, expected, monkeypatch):
        monkeypatch.delenv("HOLON_REPLAN_FEEDBACK", raising=False)
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent", "replan_feedback": feedback})
        ctx.intent_branch = "I-1-parent/_"
        seen: list[str | None] = []

        def dispatch(_context: FlowContext, _role: str, _args: list[str]) -> int:
            seen.append(os.environ.get("HOLON_REPLAN_FEEDBACK"))
            return 1

        with (
            patch("sandbox_executor.flow.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow._snapshot_child_branches", return_value=set()),
            patch("sandbox_executor.flow._dispatch_role", side_effect=dispatch),
        ):
            res = run_plan_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert seen == [expected]
        assert "HOLON_REPLAN_FEEDBACK" not in os.environ

    # --- Re-plan request wording, exception paths and the executor's refusal exit code -------------------------------

    @pytest.mark.parametrize(
        ("depth", "asked"),
        [
            (
                MAX_SUB_INTENT_DEPTH - 1,
                "write a smaller plan within the budget, or add an explicit sub-intent list.",
            ),
            (
                MAX_SUB_INTENT_DEPTH,
                "write a smaller single plan within the budget. Do not add a sub-intent list: this intent is at the "
                f"sub-intent depth limit of {MAX_SUB_INTENT_DEPTH}, so a sub-intent list would be refused.",
            ),
        ],
        ids=["below-the-depth-cap", "at-the-depth-cap"],
    )
    def test_replan_request_offers_a_sub_intent_list_only_below_the_depth_cap(self, flow_repos, tmp_path, depth, asked):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, ""), (2.0, "")])
        ctx = _budget_context(host, agents, 5.0, sub_intent_depth=depth)

        results = PipelineEngine(ctx).run()

        # The smaller re-plan runs normally, even for a leaf intent at the cap.
        assert results["execute"].status == StageStatus.SUCCESS
        assert agents.roles() == ["intent-creator", "planner", "planner", "executor"]
        assert (agents.replan_feedback_seen[1] or "").endswith(f"Re-plan attempt 1 of {MAX_REPLAN_ATTEMPTS}: {asked}")
        assert any(
            line.endswith(
                "] Stage 3: plan entropy 12.6 exceeds the entropy budget 5; returning to Stage 2 "
                f"(re-plan attempt 1 of {MAX_REPLAN_ATTEMPTS})."
            )
            for line in ctx.logs
        )

    def test_child_logs_reach_the_parent_when_a_child_pipeline_raises(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, intent_branch="I-1-parent/_")

        def boom(engine: PipelineEngine) -> dict[str, StageResult]:
            engine.context.log("child progress before the crash")
            raise RuntimeError("child crashed")

        with (
            patch.object(PipelineEngine, "run", autospec=True, side_effect=boom),
            pytest.raises(RuntimeError, match="child crashed"),
        ):
            flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", self._ROW, {"execution_id": "E-1"})

        assert any(
            line.startswith("[sub-intent first] ") and line.endswith("child progress before the crash")
            for line in ctx.logs
        )

    def test_replan_feedback_is_removed_when_the_replan_stage_is_interrupted(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_review=True, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)

        # An interrupt is not converted into a failed stage, so it leaves the re-plan loop by its finally clause.
        with pytest.raises(KeyboardInterrupt):
            self._engine_run(ctx, [ok, KeyboardInterrupt()], [self._over_budget()])

        assert ctx.replan_attempts == 1
        assert "replan_feedback" not in ctx.intent_data

    def test_a_replan_stage_that_does_not_succeed_ends_the_run_without_executing(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_review=True, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        skipped = StageResult(stage=FlowStage.PLAN, status=StageStatus.SKIPPED)

        self._engine_run(ctx, [ok, skipped], [self._over_budget()])

        assert sum("Entering stage: EXECUTE" in line for line in ctx.logs) == 1

    def test_replan_feedback_variable_is_restored_when_the_planner_dispatch_raises(
        self, flow_repos, tmp_path, monkeypatch
    ):
        host, origin = flow_repos
        monkeypatch.delenv("HOLON_REPLAN_FEEDBACK", raising=False)
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(2.0, "")])
        ctx = _budget_context(host, agents, 5.0)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        ctx.intent_data["replan_feedback"] = "smaller please"
        seen: list[str | None] = []

        def crash(*_args: Any, **_kwargs: Any) -> int:
            seen.append(os.environ.get("HOLON_REPLAN_FEEDBACK"))
            raise RuntimeError("docker crashed")

        with patch("sandbox_executor.flow._dispatch_role", side_effect=crash), pytest.raises(RuntimeError):
            run_plan_stage(ctx)

        assert seen == ["smaller please"]
        assert "HOLON_REPLAN_FEEDBACK" not in os.environ

    def test_refused_plan_exit_code_with_another_row_fails_the_stage(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(2.0, "")])
        ctx = _budget_context(host, agents, 5.0)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS
        real_execute = agents._execute

        def execute_then_exit_refused(work: Path, args: list[str], kwargs: dict[str, Any]) -> int:
            real_execute(work, args, kwargs)
            return OVER_BUDGET_EXIT_CODE

        # Only an over_budget row explains the refusal exit code; any other row is an executor failure.
        with patch.object(agents, "_execute", side_effect=execute_then_exit_refused):
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert res.error == f"Execute stage failed: executor agent exited with code {OVER_BUDGET_EXIT_CODE}."
        assert res.payload["exit_code"] == OVER_BUDGET_EXIT_CODE
        assert res.payload["execution_branch"] == ctx.execution_branch is not None
        assert "over_budget" not in res.payload

    def test_refused_plan_exit_code_names_itself_when_the_fetch_fails(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, "")])
        ctx = _budget_context(host, agents, 5.0)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

        real_fetch = flow_module._fetch_branch_prefix_error

        def fetch_fails_after_dispatch(repo_dir: str, prefix: str) -> str | None:
            return "network down" if "executor" in agents.roles() else real_fetch(repo_dir, prefix)

        with (
            patch("sandbox_executor.flow._plan_budget_decision", return_value=None),
            patch("sandbox_executor.flow._fetch_branch_prefix_error", side_effect=fetch_fails_after_dispatch),
        ):
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert res.error is not None
        assert res.error.startswith(f"Execute stage failed: the executor agent exited {OVER_BUDGET_EXIT_CODE} but ")
        assert res.error.endswith("could not be fetched to adopt its execution branch: network down")
