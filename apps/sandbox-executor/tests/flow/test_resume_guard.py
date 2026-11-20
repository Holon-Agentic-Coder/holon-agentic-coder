"""The pre-run resume guard refuses a resume missing its start stage's branch only when a checkpoint would be lost."""

from pathlib import Path

import pytest
from sandbox_executor.flow import FlowContext, FlowStage
from sandbox_executor.flow.checkpoint import save_checkpoint
from sandbox_executor.flow.resume_guard import REQUIRED_BRANCH, _resume_context_error

ALL_BRANCHES = {
    "intent_branch": "I-1-x/_",
    "plan_branch": "I-1-x/P-2-y/_",
    "execution_branch": "I-1-x/P-2-y/E-3-z/_",
}


def _context(worktree: Path, **kwargs) -> FlowContext:
    return FlowContext(target_worktree=str(worktree), intent_data={"slug": "x"}, **kwargs)


@pytest.fixture
def existing_checkpoint(tmp_path: Path) -> str:
    return save_checkpoint(_context(tmp_path))


@pytest.mark.parametrize(
    ("stage", "field"),
    [
        (FlowStage.PLAN, "intent_branch"),
        (FlowStage.EXECUTE, "plan_branch"),
        (FlowStage.REVIEW, "execution_branch"),
        (FlowStage.CALIBRATE, "execution_branch"),
    ],
)
def test_each_resumed_stage_needs_its_branch(tmp_path, existing_checkpoint, stage, field):
    assert REQUIRED_BRANCH[stage] == field
    missing = {name: value for name, value in ALL_BRANCHES.items() if name != field}

    error = _resume_context_error(_context(tmp_path, **missing), stage)

    assert error is not None
    assert f"Cannot resume at stage '{stage.value}'" in error
    assert f"no {field}" in error
    assert existing_checkpoint in error
    assert _resume_context_error(_context(tmp_path, **ALL_BRANCHES), stage) is None


def test_a_run_from_intent_or_without_from_stage_needs_nothing(tmp_path, existing_checkpoint):
    assert _resume_context_error(_context(tmp_path), FlowStage.INTENT) is None
    assert _resume_context_error(_context(tmp_path), None) is None


def test_no_existing_checkpoint_means_nothing_to_protect(tmp_path):
    assert _resume_context_error(_context(tmp_path), FlowStage.REVIEW) is None
    assert _resume_context_error(_context(tmp_path), FlowStage.REVIEW, str(tmp_path / "absent.json")) is None


def test_an_explicit_checkpoint_path_is_the_one_protected(tmp_path):
    explicit = tmp_path / "elsewhere.json"
    explicit.write_text("{}", encoding="utf-8")

    error = _resume_context_error(_context(tmp_path), FlowStage.REVIEW, str(explicit))

    assert error is not None
    assert str(explicit) in error


def test_skipped_stages_defer_to_the_first_stage_that_runs(tmp_path, existing_checkpoint):
    assert _resume_context_error(_context(tmp_path, skip_review=True, skip_calibrate=True), FlowStage.REVIEW) is None
    error = _resume_context_error(_context(tmp_path, skip_review=True), FlowStage.REVIEW)
    assert error is not None
    assert "stage 'calibrate'" in error
