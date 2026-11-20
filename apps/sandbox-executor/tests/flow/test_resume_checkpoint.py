"""A ``--from-stage`` resume keeps the intent's checkpoint intact: it is loaded, never clobbered, and stays complete."""

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.cli.commands._flow_resume import discover_resume_checkpoint
from sandbox_executor.cli.commands.flow import _resolve_checkpoint_and_intent
from sandbox_executor.flow import FlowContext, FlowStage, PipelineEngine, StageResult, StageStatus
from sandbox_executor.flow.checkpoint import load_checkpoint, save_checkpoint

SLUG = "resume-keeps-checkpoint-intact"
INTENT_BRANCH = f"I-1-{SLUG}/_"
PLAN_BRANCH = f"I-1-{SLUG}/P-2-CLA-CO5.5/_"
EXECUTION_BRANCH = f"I-1-{SLUG}/P-2-CLA-CO5.5/E-3-CLA-CO5.5/_"
CALIBRATED_BRANCH = f"I-1-{SLUG}/P-2-CLA-CO5.5/E-3-CLA-CO5.5/calibrated"
STAGES = ("intent", "plan", "execute", "review", "calibrate")


def _intent_data(**extra: str) -> dict[str, str]:
    return {"slug": SLUG, "goal": "Keep the checkpoint", "target_branch": "main", **extra}


def _completed_context(worktree: Path, intent_data: dict[str, str] | None = None) -> FlowContext:
    context = FlowContext(
        target_worktree=str(worktree),
        intent_data=intent_data or _intent_data(),
        intent_branch=INTENT_BRANCH,
        plan_branch=PLAN_BRANCH,
        execution_branch=EXECUTION_BRANCH,
        calibrated_branch=CALIBRATED_BRANCH,
        dry_run=True,
    )
    for name in STAGES:
        context.stage_results[name] = StageResult(stage=FlowStage.from_str(name), status=StageStatus.SUCCESS)
    return context


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def checkpoint(worktree: Path) -> Path:
    """A completed checkpoint at the intent's default path in the worktree."""
    path = Path(save_checkpoint(_completed_context(worktree)))
    assert path == worktree / ".holon" / "flow" / f"checkpoint-{SLUG}.json"
    return path


@pytest.fixture
def intent_file(tmp_path: Path) -> Path:
    path = tmp_path / "intent.json"
    path.write_text(json.dumps(_intent_data()), encoding="utf-8")
    return path


def _resume_args(intent_file: Path, worktree: Path, from_stage: str = "review") -> argparse.Namespace:
    return argparse.Namespace(
        intent_file=str(intent_file),
        from_stage=from_stage,
        checkpoint=None,
        target_worktree=str(worktree),
        agent=None,
        model=None,
    )


def _result(stage: FlowStage, status: StageStatus):
    return lambda context: StageResult(stage=stage, status=status)


def test_from_stage_without_checkpoint_loads_the_intent_checkpoint(checkpoint, intent_file, worktree):
    args = _resume_args(intent_file, worktree)
    context, intent_data, _agent, _model = _resolve_checkpoint_and_intent(args)

    assert args.checkpoint == str(checkpoint)
    assert args.from_stage == "review"
    assert intent_data == {}
    assert context.execution_branch == EXECUTION_BRANCH
    assert set(context.stage_results) == set(STAGES)

    seen: list[str | None] = []

    def fake_review(ctx):
        seen.append(ctx.execution_branch)
        return StageResult(stage=FlowStage.REVIEW, status=StageStatus.SUCCESS)

    with (
        patch("sandbox_executor.flow.pipeline.run_review_stage", side_effect=fake_review),
        patch(
            "sandbox_executor.flow.pipeline.run_calibrate_stage",
            side_effect=_result(FlowStage.CALIBRATE, StageStatus.SUCCESS),
        ),
    ):
        PipelineEngine(context, from_stage=args.from_stage, checkpoint_path=args.checkpoint).run()

    assert seen == [EXECUTION_BRANCH]


@pytest.mark.parametrize("from_stage", ["review", "calibrate"])
@pytest.mark.parametrize("explicit_path", [False, True])
def test_a_resume_that_cannot_start_leaves_the_checkpoint_byte_for_byte(
    checkpoint, worktree, from_stage, explicit_path
):
    before = checkpoint.read_bytes()
    fresh = FlowContext(target_worktree=str(worktree), intent_data=_intent_data(), dry_run=True)

    with (
        patch("sandbox_executor.flow.pipeline.run_review_stage") as review,
        patch("sandbox_executor.flow.pipeline.run_calibrate_stage") as calibrate,
    ):
        results = PipelineEngine(
            fresh, from_stage=from_stage, checkpoint_path=str(checkpoint) if explicit_path else None
        ).run()

    review.assert_not_called()
    calibrate.assert_not_called()
    assert results[from_stage].status == StageStatus.FAILED
    assert "no execution_branch" in results[from_stage].error
    assert "The checkpoint was left unchanged." in results[from_stage].error
    assert checkpoint.read_bytes() == before


@pytest.mark.parametrize(
    "case",
    [
        (StageStatus.SUCCESS, StageStatus.SUCCESS, "completed", STAGES),
        (StageStatus.HALTED_FOR_HUMAN, StageStatus.SUCCESS, "completed", STAGES),
        (StageStatus.SUCCESS, StageStatus.FAILED, "calibrate", STAGES[:4]),
    ],
)
def test_a_resume_from_review_keeps_earlier_stages_and_the_current_stage(checkpoint, intent_file, worktree, case):
    review_status, calibrate_status, current_stage, completed = case
    args = _resume_args(intent_file, worktree)
    context, *_ = _resolve_checkpoint_and_intent(args)

    with (
        patch("sandbox_executor.flow.pipeline.run_review_stage", side_effect=_result(FlowStage.REVIEW, review_status)),
        patch(
            "sandbox_executor.flow.pipeline.run_calibrate_stage",
            side_effect=_result(FlowStage.CALIBRATE, calibrate_status),
        ),
    ):
        PipelineEngine(context, from_stage=args.from_stage, checkpoint_path=args.checkpoint).run()

    saved = load_checkpoint(str(checkpoint))
    assert saved.current_stage == FlowStage.from_str(current_stage)
    assert sorted(saved.completed_stages) == sorted(completed)
    assert saved.context.intent_branch == INTENT_BRANCH
    assert saved.context.plan_branch == PLAN_BRANCH
    assert saved.context.execution_branch == EXECUTION_BRANCH


def test_without_a_checkpoint_the_resume_starts_from_a_fresh_context(intent_file, worktree):
    args = _resume_args(intent_file, worktree)
    args.agent, args.model = "claude", "claude-opus-5-5"

    assert discover_resume_checkpoint(args) is None
    context, intent_data, _agent, _model = _resolve_checkpoint_and_intent(args)

    assert context is None
    assert args.checkpoint is None
    assert intent_data["slug"] == SLUG


def test_discovery_needs_from_stage_and_respects_an_explicit_checkpoint(checkpoint, intent_file, worktree):
    assert discover_resume_checkpoint(_resume_args(intent_file, worktree, from_stage=None)) is None
    explicit = _resume_args(intent_file, worktree)
    explicit.checkpoint = str(worktree / "other.json")
    assert discover_resume_checkpoint(explicit) is None
    assert discover_resume_checkpoint(_resume_args(intent_file, worktree)) == str(checkpoint)


def test_a_sub_intent_checkpoint_is_found_under_its_branch_key(tmp_path, worktree):
    sub_intent = _intent_data(parent_intent_id="I-0", branch=f"I-1-{SLUG}/I-4-{SLUG}/_")
    saved = save_checkpoint(_completed_context(worktree, sub_intent))
    intent_file = tmp_path / "sub-intent.json"
    intent_file.write_text(json.dumps(sub_intent), encoding="utf-8")

    found = discover_resume_checkpoint(_resume_args(intent_file, worktree))

    assert found == saved
    assert Path(saved).name == f"checkpoint-I-1-{SLUG}-I-4-{SLUG}.json"


def test_the_dedicated_flow_worktree_is_probed_after_the_target(tmp_path, intent_file):
    target = tmp_path / "repo"
    target.mkdir()
    dedicated = tmp_path / f"flow-{SLUG}"
    dedicated.mkdir()
    saved = save_checkpoint(_completed_context(dedicated))

    assert discover_resume_checkpoint(_resume_args(intent_file, target)) == saved
