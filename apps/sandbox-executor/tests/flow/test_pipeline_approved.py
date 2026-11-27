from unittest.mock import patch

from sandbox_executor.flow import FlowContext, FlowStage, PipelineEngine, StageResult, StageStatus


def test_pipeline_runs_stage5_after_approved(tmp_path):
    ctx = FlowContext(repo_dir=str(tmp_path), intent_data={"slug": "appr", "goal": "Appr"}, auto_calibrate=True)
    review = StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.APPROVED,
        payload={"consensus": {"approved": True, "iterations": 1}, "approved": True},
    )
    calibrate = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS)

    def fake_review(context):
        context.stage_results["review"] = review
        return review

    with (
        patch("sandbox_executor.flow.pipeline.run_review_stage", side_effect=fake_review),
        patch("sandbox_executor.flow.pipeline.run_calibrate_stage", return_value=calibrate) as mock_cal,
    ):
        engine = PipelineEngine(ctx, from_stage="review")
        engine.run()
    assert review.status == StageStatus.APPROVED
    mock_cal.assert_called_once()
    assert any("Pipeline finished at the merge boundary: review approved" in log for log in ctx.logs)
