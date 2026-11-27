import json
from typing import Any
from unittest.mock import patch

from sandbox_executor.budgets import MAX_REPLAN_ATTEMPTS
from sandbox_executor.cli import main as cli_main
from sandbox_executor.flow import FlowContext, FlowStage, PipelineEngine, StageResult, StageStatus


class TestAllowOverlapCLI:
    def test_cli_flow_allow_overlap_reaches_the_context(self, tmp_path, monkeypatch):
        intent_file = tmp_path / "intent.json"
        intent_file.write_text(json.dumps({"slug": "cli-test", "goal": "CLI flow test"}))
        monkeypatch.setenv("HOLON_AGENT", "antigravity-agent")
        monkeypatch.setenv("HOLON_MODEL", "gemini-3.8-flash-medium")
        contexts = []
        for extra in ([], ["--allow-overlap"]):
            argv = ["holon", "flow", str(intent_file), "--dry-run", "--json", *extra]
            with (
                patch("sandbox_executor.cli.run_flow_pipeline", return_value={"status": "ok"}) as mock_pipeline,
                patch("sys.exit"),
                patch("sys.argv", argv),
            ):
                cli_main()
            contexts.append(mock_pipeline.call_args[1]["context"])
        assert [ctx.allow_overlap for ctx in contexts] == [False, True]


class TestTouchSetViolationReturnsToStage2SharingReplanCounter:
    @staticmethod
    def _violation() -> StageResult:
        return StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.FAILED,
            error="Execute stage failed: outside the touch set",
            payload={"touch_set_violation": ["lib/extra.py"], "replan_requested": True},
        )

    @staticmethod
    def _run(execute_results: list[StageResult]) -> tuple[FlowContext, dict[str, StageResult], list[Any]]:
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_calibrate=True)
        feedback: list[Any] = []

        def plan(context: FlowContext) -> StageResult:
            feedback.append(context.intent_data.get("replan_feedback"))
            context.plan_branch = f"I-1-parent/P-{len(feedback)}-a-b/_"
            return StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)

        results_iter = iter(execute_results)
        intent = StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)
        with (
            patch("sandbox_executor.flow.pipeline.run_intent_stage", return_value=intent),
            patch("sandbox_executor.flow.pipeline.run_plan_stage", side_effect=plan),
            patch("sandbox_executor.flow.pipeline.run_execute_stage", side_effect=lambda _c: next(results_iter)),
            patch("sandbox_executor.flow.pipeline.save_checkpoint", return_value="checkpoint.json"),
        ):
            results = PipelineEngine(ctx).run()
        return ctx, results, feedback

    def test_violation_returns_to_stage_2_once(self):
        ok = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS)
        ctx, results, feedback = self._run([self._violation(), ok])
        assert results["execute"].status == StageStatus.SUCCESS
        assert ctx.replan_attempts == 1
        assert feedback[0] is None
        assert "I-1-parent/P-1-a-b/_" in feedback[1] and "lib/extra.py" in feedback[1]
        assert "widen the '## Touch Set' section" in feedback[1]
        assert "replan_feedback" not in ctx.intent_data

    def test_exhausted_violation_names_the_files(self):
        _ctx, results, feedback = self._run([self._violation() for _ in range(MAX_REPLAN_ATTEMPTS + 1)])
        execute = results["execute"]
        assert len(feedback) == 1 + MAX_REPLAN_ATTEMPTS
        assert execute.error == (
            "Execute stage failed: the execution changed files outside the plan's touch set (lib/extra.py) after "
            f"{MAX_REPLAN_ATTEMPTS} re-plan attempts."
        )
        assert execute.payload["replan_attempts"] == MAX_REPLAN_ATTEMPTS
