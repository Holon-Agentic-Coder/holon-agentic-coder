from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.pipeline import PipelineEngine


def _make_engine(tmp_path: Path, **context_kwargs: Any) -> PipelineEngine:
    ctx = FlowContext(repo_dir=str(tmp_path), intent_data={"slug": "registry-test"}, dry_run=True, **context_kwargs)
    return PipelineEngine(ctx)


def test_stage_handlers_match_canonical_stage_order() -> None:
    expected = [
        FlowStage.INTENT,
        FlowStage.PLAN,
        FlowStage.EXECUTE,
        FlowStage.REVIEW,
        FlowStage.CALIBRATE,
    ]
    assert list(PipelineEngine.STAGE_HANDLERS) == expected
    assert expected == PipelineEngine.STAGE_ORDER
    assert list(PipelineEngine.STAGE_HANDLERS) == PipelineEngine.STAGE_ORDER


@pytest.mark.parametrize(
    ("stage", "patch_target"),
    [
        (FlowStage.INTENT, "sandbox_executor.flow.pipeline.run_intent_stage"),
        (FlowStage.PLAN, "sandbox_executor.flow.pipeline.run_plan_stage"),
        (FlowStage.EXECUTE, "sandbox_executor.flow.pipeline.run_execute_stage"),
        (FlowStage.REVIEW, "sandbox_executor.flow.pipeline.run_review_stage"),
        (FlowStage.CALIBRATE, "sandbox_executor.flow.pipeline.run_calibrate_stage"),
    ],
)
def test_execute_stage_dispatches_and_honors_module_patches(
    tmp_path: Path, stage: FlowStage, patch_target: str
) -> None:
    engine = _make_engine(tmp_path)
    expected = StageResult(stage=stage, status=StageStatus.SUCCESS, payload={"patched": stage.value})
    with patch(patch_target, return_value=expected) as mock_stage:
        result = engine.execute_stage(stage)
    assert result is expected
    mock_stage.assert_called_once_with(engine.context)


def test_execute_handler_resumes_partial_once_then_resets(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    engine._resume_partial_pending = True
    first = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"call": 1})
    second = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"call": 2})
    with patch("sandbox_executor.flow.pipeline.run_execute_stage", side_effect=[first, second]) as mock_exec:
        assert engine.execute_stage(FlowStage.EXECUTE) is first
        assert engine._resume_partial_pending is False
        assert engine.execute_stage(FlowStage.EXECUTE) is second
    assert mock_exec.call_args_list[0].args == (engine.context,)
    assert mock_exec.call_args_list[0].kwargs == {"resume_partial": True}
    assert mock_exec.call_args_list[1].args == (engine.context,)
    assert mock_exec.call_args_list[1].kwargs == {}


def test_execute_handler_delegates_to_resume_children_on_recorded_decomposition(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    previous = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"decomposed": True})
    engine.context.stage_results[FlowStage.EXECUTE.value] = previous
    resumed = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"resumed": True})
    with (
        patch("sandbox_executor.flow.pipeline.has_recorded_decomposition", return_value=True) as mock_has,
        patch("sandbox_executor.flow.pipeline.resume_children", return_value=resumed) as mock_resume,
        patch("sandbox_executor.flow.pipeline.run_execute_stage") as mock_exec,
    ):
        result = engine.execute_stage(FlowStage.EXECUTE)
    assert result is resumed
    mock_has.assert_called_once_with(previous, engine.context.plan_branch)
    mock_resume.assert_called_once_with(engine.context, previous)
    mock_exec.assert_not_called()


def test_calibrate_handler_skips_when_flag_is_set(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, skip_calibrate=True)
    with patch("sandbox_executor.flow.pipeline.run_calibrate_stage") as mock_cal:
        result = engine.execute_stage(FlowStage.CALIBRATE)
    assert result.stage == FlowStage.CALIBRATE
    assert result.status == StageStatus.SKIPPED
    assert result.payload == {"skipped_by_flag": True}
    mock_cal.assert_not_called()


def test_overriding_stage_handlers_redirects_execute_stage(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    custom = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS, payload={"custom": True})
    seen: list[PipelineEngine] = []

    def fake_handler(target: PipelineEngine) -> StageResult:
        seen.append(target)
        return custom

    with patch.dict(PipelineEngine.STAGE_HANDLERS, {FlowStage.PLAN: fake_handler}):
        result = engine.execute_stage(FlowStage.PLAN)
    assert result is custom
    assert seen == [engine]


def test_execute_stage_returns_failed_for_unsupported_stage(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    unsupported: Any = "nonexistent_stage"
    result = engine.execute_stage(unsupported)
    assert result.status == StageStatus.FAILED
    assert "Unsupported stage" in (result.error or "")
