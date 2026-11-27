import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.flow import FlowContext, FlowStage, PipelineEngine, StageResult, StageStatus, run_review_stage
from sandbox_executor.flow.admission_state import AdmissionRecord, BlockedBy, admitted_record, blocked_record
from sandbox_executor.flow.checkpoint import load_checkpoint, save_checkpoint
from sandbox_executor.intent_assumption_rules import default_assumptions_fields

_ASSUMPTIONS = default_assumptions_fields("Dry-run pipeline runs in tmp_path.", "Verified by checkpoint tests.")


class TestFlowResumptionAndErrorHandling:
    def test_pipeline_engine_sequential_run(self, tmp_path):
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "e2e-flow", "goal": "End to end test", **_ASSUMPTIONS},
            auto_calibrate=True,
            dry_run=True,
        )

        # why: tmp_path holds no git repository, so a real calibration would fail Stage 5 closed
        report = MagicMock(calibrated_branch=None)
        report.to_dict.return_value = {}
        engine = PipelineEngine(ctx)
        with patch("sandbox_executor.flow.stages.calibrate.run_calibrate", return_value=report):
            results = engine.run()

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
        invalid_intent_payload: dict = {}
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data=invalid_intent_payload,
            dry_run=True,
        )
        engine = PipelineEngine(ctx)
        results = engine.run()
        assert results["intent"].status == StageStatus.FAILED
        assert "plan" not in results

    def test_pipeline_engine_runs_review_in_dry_run(self, tmp_path):
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "review-runs-flow", "goal": "Review always runs", **_ASSUMPTIONS},
            auto_calibrate=True,
            dry_run=True,
        )

        engine = PipelineEngine(ctx)
        results = engine.run()

        assert "review" in results
        assert results["review"].status != StageStatus.SKIPPED
        assert results["review"].payload.get("skipped_by_flag") is None
        assert results["review"].status == StageStatus.HALTED_FOR_HUMAN

    def test_stage4_failure_halts_before_calibration(self, tmp_path):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "review-fails", "goal": "Review fails"},
            intent_branch="I-1-review-fails/_",
            plan_branch="I-1-review-fails/P-2-plan/_",
            execution_branch="I-1-review-fails/P-2-plan/E-3-exec/_",
            dry_run=True,
        )
        failed = StageResult(stage=FlowStage.REVIEW, status=StageStatus.FAILED, error="Review loop stalled")

        with (
            patch("sandbox_executor.flow.pipeline.run_review_stage", return_value=failed) as review,
            patch("sandbox_executor.flow.pipeline.run_calibrate_stage") as calibrate,
        ):
            results = PipelineEngine(ctx, from_stage=FlowStage.REVIEW).run()

        review.assert_called_once()
        calibrate.assert_not_called()
        assert results["review"].status == StageStatus.FAILED
        assert results["review"].error == "Review loop stalled"
        assert "calibrate" not in results
        assert any("Pipeline halted due to failure in stage 'review'" in line for line in ctx.logs)

    def test_legacy_checkpoint_with_skip_review_resumes_into_stage4(self, tmp_path):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "legacy-skip-review", "goal": "Legacy checkpoint"},
            intent_branch="I-1-legacy-skip-review/_",
            plan_branch="I-1-legacy-skip-review/P-2-plan/_",
            execution_branch="I-1-legacy-skip-review/P-2-plan/E-3-exec/_",
            skip_calibrate=True,
            dry_run=True,
        )
        for stage in (FlowStage.INTENT, FlowStage.PLAN, FlowStage.EXECUTE):
            ctx.stage_results[stage.value] = StageResult(stage=stage, status=StageStatus.SUCCESS)
        path = Path(save_checkpoint(ctx))
        data = json.loads(path.read_text(encoding="utf-8"))
        # why: a checkpoint written before --skip-review was removed still carries this key
        data["context"]["skip_review"] = True
        path.write_text(json.dumps(data), encoding="utf-8")

        loaded = load_checkpoint(str(path))
        assert loaded.current_stage == FlowStage.REVIEW
        assert "skip_review" not in loaded.context.to_dict()
        halted = StageResult(stage=FlowStage.REVIEW, status=StageStatus.HALTED_FOR_HUMAN)
        with patch("sandbox_executor.flow.pipeline.run_review_stage", return_value=halted) as review:
            results = PipelineEngine(loaded.context, from_stage=loaded.current_stage, checkpoint_path=str(path)).run()

        review.assert_called_once()
        assert results["review"].status == StageStatus.HALTED_FOR_HUMAN
        assert results["review"].payload.get("skipped_by_flag") is None

    def test_pipeline_engine_skip_calibrate(self, tmp_path):
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "skip-cal-flow", "goal": "Skip calibrate", **_ASSUMPTIONS},
            skip_calibrate=True,
            auto_calibrate=False,
            dry_run=True,
        )

        engine = PipelineEngine(ctx)
        results = engine.run()

        assert "calibrate" in results
        assert results["calibrate"].status == StageStatus.SKIPPED
        assert results["calibrate"].payload.get("skipped_by_flag") is True

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
            intent_data={"slug": "halt-skip-cal", "goal": "Test halt with skip cal", **_ASSUMPTIONS},
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
    operator_facing_admission = data["admission"]
    assert operator_facing_admission == record.to_dict()
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


def test_checkpoint_review_boundary_statuses(tmp_path):
    # why: legacy checkpoints recorded HALTED_FOR_HUMAN, which still counts as completed alongside APPROVED
    for status in (StageStatus.APPROVED, StageStatus.HALTED_FOR_HUMAN):
        ctx = FlowContext(target_worktree=str(tmp_path), intent_data={"slug": f"boundary-{status.value}"})
        ctx.stage_results["intent"] = StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)
        ctx.stage_results["plan"] = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        ctx.stage_results["execute"] = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS)
        ctx.stage_results["review"] = StageResult(stage=FlowStage.REVIEW, status=status)
        path = save_checkpoint(ctx)
        cp = load_checkpoint(path)
        assert "review" in cp.completed_stages
        assert cp.current_stage == FlowStage.CALIBRATE
