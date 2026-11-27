from unittest.mock import patch

from sandbox_executor.flow import FlowContext, FlowStage, PipelineEngine, StageResult, StageStatus

from tests.flow.helpers import SteppingClock
from tests.flow.timed_dispatch import dispatch_timed_execute


class TestUnlimitedStageBudgets:
    def test_unlimited_execute_stage_never_breaches_and_records_the_sentinel(self, tmp_path):
        from sandbox_executor.budgets import load_budgets, stage_budget_handdown_env

        ctx = FlowContext(repo_dir=str(tmp_path), intent_data={"slug": "u", "goal": "U"}, dry_run=True)
        budgets = load_budgets({"HOLON_STAGE_BUDGET_EXECUTE_MINUTES": " UnLimited "})
        handed_down = []

        def run_execute(context):
            handed_down.append(stage_budget_handdown_env())
            return StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={})

        engine = PipelineEngine(ctx, budgets=budgets, clock=SteppingClock(step=73.2 * 60))
        with patch("sandbox_executor.flow.pipeline.run_execute_stage", side_effect=run_execute):
            result = engine.timed_execute_stage(FlowStage.EXECUTE)
        engine.after_stage(FlowStage.EXECUTE, result)
        assert result.status == StageStatus.SUCCESS
        assert result.budget_seconds is None
        assert "budget_breach" not in result.payload and "budget_overrun" not in result.payload
        assert result.payload["stage_budget"] == "unlimited"
        assert handed_down == [{"HOLON_STAGE_BUDGET_SECONDS": "unlimited"}]
        logs = "\n".join(ctx.logs)
        assert "execute: ok (73.2 min / unlimited)" in logs
        assert "WARNING: Stage 'execute' runs with no wall-clock ceiling" in logs
        restored_from_checkpoint = StageResult.from_dict(result.to_dict())
        assert restored_from_checkpoint.summary_line() == "execute: ok (73.2 min / unlimited)"
        assert restored_from_checkpoint.payload["stage_budget"] == "unlimited"

    def test_unlimited_review_iteration_makes_the_review_stage_unlimited(self, tmp_path):
        from sandbox_executor.budgets import load_budgets

        ctx = FlowContext(repo_dir=str(tmp_path), intent_data={"slug": "r", "goal": "R"}, dry_run=True)
        budgets = load_budgets({"HOLON_STAGE_BUDGET_REVIEW_ITERATION_MINUTES": "unlimited"})
        engine = PipelineEngine(ctx, budgets=budgets)
        passes = StageResult(stage=FlowStage.REVIEW, status=StageStatus.SUCCESS, payload={"consensus": {"passes": 3}})
        assert engine.stage_budget_seconds(FlowStage.REVIEW, passes) is None
        assert engine.stage_budget_seconds(FlowStage.EXECUTE) == 2700

    def test_unlimited_execute_budget_reaches_the_dispatched_executor_container(self, tmp_path):
        result, seen = dispatch_timed_execute(
            tmp_path, {"HOLON_STAGE_BUDGET_EXECUTE_MINUTES": "unlimited"}, exit_code=0
        )
        assert seen[0][3] == {"HOLON_STAGE_BUDGET_SECONDS": "unlimited"}
        assert result.budget_seconds is None
        assert result.payload["stage_budget"] == "unlimited"
