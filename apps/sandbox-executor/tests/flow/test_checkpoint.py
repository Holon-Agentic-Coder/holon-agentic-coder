"""Tests for checkpointed resumption from a stage and for engine error handling."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.flow import FlowContext, FlowStage, PipelineEngine, StageResult, StageStatus, run_review_stage
from sandbox_executor.flow.admission_state import AdmissionRecord, BlockedBy, admitted_record, blocked_record
from sandbox_executor.flow.checkpoint import load_checkpoint, save_checkpoint


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


BLOCKED = blocked_record(
    None,
    (
        BlockedBy("pull_request", ("src/app.py",), number=7, branch="I-2-o/P-3-x/E-4-y/_"),
        BlockedBy("local_flow", ("docs/",), plan_branch="I-5-l/P-6-z/_", worktree="/w/second"),
    ),
    "2026-11-20T01:00:00.000Z",
)


@pytest.mark.parametrize(
    "record", [BLOCKED, admitted_record(), admitted_record([{"flow": "pull request #7", "files": ["src/app.py"]}])]
)
def test_checkpoint_round_trips_the_admission_record(tmp_path, record):
    context = FlowContext(target_worktree=str(tmp_path), intent_data={"slug": "adm"}, admission=record.to_dict())
    path = save_checkpoint(context)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["admission"] == record.to_dict()  # the operator-facing top-level object
    assert AdmissionRecord.from_dict(load_checkpoint(path).context.admission) == record


def test_admission_record_shapes():
    assert BLOCKED.to_dict() == {
        "state": "blocked",
        "blocked_since": "2026-11-20T01:00:00.000Z",
        "blocked_by": [
            {"kind": "pull_request", "number": 7, "branch": "I-2-o/P-3-x/E-4-y/_", "files": ["src/app.py"]},
            {"kind": "local_flow", "plan_branch": "I-5-l/P-6-z/_", "worktree": "/w/second", "files": ["docs/"]},
        ],
    }
    assert admitted_record().to_dict() == {"state": "admitted"}
    assert admitted_record([]).to_dict() == {"state": "admitted", "override": True, "override_conflicts": []}


def test_checkpoint_without_admission_still_loads(tmp_path):
    context = FlowContext(target_worktree=str(tmp_path), intent_data={"slug": "old"})
    path = save_checkpoint(context)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data["context"].pop("admission")
    assert "admission" not in data
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    assert load_checkpoint(path).context.admission is None


def test_top_level_admission_is_read_when_the_context_lacks_it(tmp_path):
    path = save_checkpoint(FlowContext(target_worktree=str(tmp_path), intent_data={"slug": "top"}))
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data["admission"] = {"state": "admitted"}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    assert load_checkpoint(path).context.admission == {"state": "admitted"}


@pytest.mark.parametrize("value", [None, "blocked", [], {"state": "waiting"}, {"blocked_by": []}])
def test_unknown_admission_values_parse_as_none(value):
    assert AdmissionRecord.from_dict(value) is None


def test_blocked_record_keeps_the_first_blocked_since_and_replaces_blocked_by():
    newer = (BlockedBy("pull_request", ("lib/",), number=9, branch="b"),)
    again = blocked_record(BLOCKED, newer, "2026-11-20T02:00:00.000Z")
    assert again.blocked_since == "2026-11-20T01:00:00.000Z" and again.blocked_by == newer
    fresh = blocked_record(admitted_record(), newer, "2026-11-20T02:00:00.000Z")
    assert fresh.blocked_since == "2026-11-20T02:00:00.000Z"
