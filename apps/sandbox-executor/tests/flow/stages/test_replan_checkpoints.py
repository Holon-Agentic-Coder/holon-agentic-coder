"""Tests for the checkpoints and Stage 4 and 5 hand-off of the Stage 2 and 3 re-plan loop, with stubbed stages."""

import json
from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor.budgets import MAX_REPLAN_ATTEMPTS
from sandbox_executor.flow import FlowContext, FlowStage, PipelineEngine, StageResult, StageStatus

from tests.flow.helpers import stage4_halts_at_consensus


class TestReplanLoopCheckpoints:
    """The engine checkpoints every re-plan step and runs Stage 4 and 5 after any successful execution."""

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
            patch("sandbox_executor.flow.pipeline.run_intent_stage", return_value=intent),
            patch("sandbox_executor.flow.pipeline.run_plan_stage", side_effect=plan),
            patch("sandbox_executor.flow.pipeline.run_execute_stage", side_effect=lambda _c: next(exec_iter)),
            patch("sandbox_executor.flow.pipeline.save_checkpoint", side_effect=snapshot),
            stage4_halts_at_consensus(),
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
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        no_plan = StageResult(stage=FlowStage.PLAN, status=StageStatus.FAILED, error="Plan stage failed: no plan")

        _results, saved = self._engine_run(ctx, [ok, no_plan], [self._over_budget()])

        last = saved[-1]
        assert last["replan_attempts"] == 1
        assert last["plan_branch"] is None
        assert last["stage_results"]["plan"]["status"] == "failed"
        assert sum("Entering stage: PLAN" in line for line in ctx.logs) == 2

    def test_replan_checkpoints_the_new_execute_result(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        other = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.FAILED, error="Execute stage failed: push")

        _results, saved = self._engine_run(ctx, [ok, ok], [self._over_budget(), other])

        last = saved[-1]
        assert last["replan_attempts"] == 1
        assert last["stage_results"]["execute"]["error"] == "Execute stage failed: push"
        assert last["plan_branch"] == ctx.plan_branch is not None
        assert sum("Entering stage: EXECUTE" in line for line in ctx.logs) == 2

    def test_exhausted_replans_checkpoint_the_final_error(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_calibrate=True)
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

    def test_decomposed_parent_runs_review_and_calibration_like_an_executed_plan(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        decomposed = StageResult(
            stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"decomposed": True, "integrated": True}
        )

        results, saved = self._engine_run(ctx, [ok], [decomposed])

        # No decomposed skip any more: Stage 4 runs and Stage 5 follows its flag as for any execution.
        assert results["review"].status == StageStatus.HALTED_FOR_HUMAN
        assert results["calibrate"].payload == {"skipped_by_flag": True}
        assert saved[-1]["stage_results"]["calibrate"]["payload"] == {"skipped_by_flag": True}
        assert not any("the plan was decomposed into child pipelines" in line for line in ctx.logs)

    def test_undecomposed_success_runs_review_and_calibration_normally(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        plain = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"execution_id": "E-1"})

        results, _saved = self._engine_run(ctx, [ok], [plain])

        assert results["review"].status == StageStatus.HALTED_FOR_HUMAN
        assert results["calibrate"].payload == {"skipped_by_flag": True}

    def test_replan_feedback_is_removed_when_the_replan_stage_is_interrupted(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)

        # An interrupt is not converted into a failed stage, so it leaves the re-plan loop by its finally clause.
        with pytest.raises(KeyboardInterrupt):
            self._engine_run(ctx, [ok, KeyboardInterrupt()], [self._over_budget()])

        assert ctx.replan_attempts == 1
        assert "replan_feedback" not in ctx.intent_data

    def test_a_replan_stage_that_does_not_succeed_ends_the_run_without_executing(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        skipped = StageResult(stage=FlowStage.PLAN, status=StageStatus.SKIPPED)

        self._engine_run(ctx, [ok, skipped], [self._over_budget()])

        assert sum("Entering stage: EXECUTE" in line for line in ctx.logs) == 1
