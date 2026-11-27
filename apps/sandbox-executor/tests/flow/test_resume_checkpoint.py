import argparse
import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor.agent_defaults import LEGACY_DEFAULT_AGENT, LEGACY_DEFAULT_MODEL, default_model_for
from sandbox_executor.cli.commands._flow_resume import RUN_SWITCHES, discover_resume_checkpoint
from sandbox_executor.cli.commands.flow import _build_flow_context, _resolve_checkpoint_and_intent
from sandbox_executor.flow import FlowContext, FlowStage, PipelineEngine, StageResult, StageStatus
from sandbox_executor.flow.checkpoint import load_checkpoint, save_checkpoint
from sandbox_executor.intent_refresh import SELF_EXPLAINING_SENTENCE
from sandbox_executor.launcher.coherence import FlowProxyChoice

_NO_PROXY = FlowProxyChoice(spawn_sidecar=False, attach_running_proxy=False)

SLUG = "resume-keeps-checkpoint-intact"
INTENT_BRANCH = f"I-1-{SLUG}/_"
PLAN_BRANCH = f"I-1-{SLUG}/P-2-CLA-CO5.5/_"
EXECUTION_BRANCH = f"I-1-{SLUG}/P-2-CLA-CO5.5/E-3-CLA-CO5.5/_"
CALIBRATED_BRANCH = f"I-1-{SLUG}/P-2-CLA-CO5.5/E-3-CLA-CO5.5/calibrated"
STAGES = ("intent", "plan", "execute", "review", "calibrate")


def _intent_data(**extra: str) -> dict[str, str]:
    return {"slug": SLUG, "goal": f"Keep the checkpoint. {SELF_EXPLAINING_SENTENCE}", "target_branch": "main", **extra}


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
def completed_checkpoint_at_default_path(worktree: Path) -> Path:
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


def test_from_stage_without_checkpoint_loads_the_intent_checkpoint(
    completed_checkpoint_at_default_path, intent_file, worktree
):
    args = _resume_args(intent_file, worktree)
    context, intent_data, _agent, _model = _resolve_checkpoint_and_intent(args)

    assert args.checkpoint == str(completed_checkpoint_at_default_path)
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
    completed_checkpoint_at_default_path, worktree, from_stage, explicit_path
):
    before = completed_checkpoint_at_default_path.read_bytes()
    fresh = FlowContext(target_worktree=str(worktree), intent_data=_intent_data(), dry_run=True)

    with (
        patch("sandbox_executor.flow.pipeline.run_review_stage") as review,
        patch("sandbox_executor.flow.pipeline.run_calibrate_stage") as calibrate,
    ):
        results = PipelineEngine(
            fresh,
            from_stage=from_stage,
            checkpoint_path=str(completed_checkpoint_at_default_path) if explicit_path else None,
        ).run()

    review.assert_not_called()
    calibrate.assert_not_called()
    assert results[from_stage].status == StageStatus.FAILED
    assert "no execution_branch" in results[from_stage].error
    assert "The checkpoint was left unchanged." in results[from_stage].error
    assert completed_checkpoint_at_default_path.read_bytes() == before


@pytest.mark.parametrize(
    "case",
    [
        (StageStatus.SUCCESS, StageStatus.SUCCESS, "completed", STAGES),
        (StageStatus.APPROVED, StageStatus.SUCCESS, "completed", STAGES),
        (StageStatus.HALTED_FOR_HUMAN, StageStatus.SUCCESS, "completed", STAGES),
        (StageStatus.SUCCESS, StageStatus.FAILED, "calibrate", STAGES[:4]),
    ],
)
def test_a_resume_from_review_keeps_earlier_stages_and_the_current_stage(
    completed_checkpoint_at_default_path, intent_file, worktree, case
):
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

    saved = load_checkpoint(str(completed_checkpoint_at_default_path))
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


def test_discovery_needs_from_stage_and_respects_an_explicit_checkpoint(
    completed_checkpoint_at_default_path, intent_file, worktree
):
    assert discover_resume_checkpoint(_resume_args(intent_file, worktree, from_stage=None)) is None
    explicit = _resume_args(intent_file, worktree)
    explicit.checkpoint = str(worktree / "other.json")
    assert discover_resume_checkpoint(explicit) is None
    assert discover_resume_checkpoint(_resume_args(intent_file, worktree)) == str(completed_checkpoint_at_default_path)


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


def _dry_review_resume_args(intent_file: Path, worktree: Path, **flags: Any) -> argparse.Namespace:
    args = _resume_args(intent_file, worktree)
    defaults = {
        "dry_run": True,
        "skip_push": True,
        "skip_calibrate": False,
        "allow_overlap": False,
        "max_review_iterations": None,
        "ensemble_size": None,
        "token_reduce": False,
        "mitm_web": False,
    }
    for name, value in {**defaults, **flags}.items():
        setattr(args, name, value)
    return args


def _resume_with_recording_stages(args: argparse.Namespace) -> tuple[FlowContext, list[tuple[str, str, str]]]:
    context, intent_data, agent, model = _resolve_checkpoint_and_intent(args)
    with patch("sandbox_executor.launcher.coherence.resolve_flow_proxy_choice", return_value=_NO_PROXY):
        context = _build_flow_context(args, context, intent_data, agent, model)
    seen: list[tuple[str, str, str]] = []

    def stage(flow_stage: FlowStage):
        def run(ctx: FlowContext) -> StageResult:
            seen.append((flow_stage.value, ctx.agent, ctx.model))
            return StageResult(stage=flow_stage, status=StageStatus.SUCCESS)

        return run

    with (
        patch("sandbox_executor.flow.pipeline.run_review_stage", side_effect=stage(FlowStage.REVIEW)),
        patch("sandbox_executor.flow.pipeline.run_calibrate_stage", side_effect=stage(FlowStage.CALIBRATE)),
    ):
        PipelineEngine(context, from_stage=args.from_stage, checkpoint_path=args.checkpoint).run()
    return context, seen


def _save_runner_checkpoint(worktree: Path, **attributes: Any) -> Path:
    context = _completed_context(worktree)
    for name, value in attributes.items():
        setattr(context, name, value)
    return Path(save_checkpoint(context))


def test_a_stored_skip_calibrate_is_dropped_and_logged_when_the_resume_does_not_pass_it(worktree, intent_file, caplog):
    _save_runner_checkpoint(worktree, skip_calibrate=True, auto_calibrate=False)
    args = _dry_review_resume_args(intent_file, worktree)

    with caplog.at_level(logging.WARNING, logger="sandbox_executor.cli.commands._flow_resume"):
        context, seen = _resume_with_recording_stages(args)

    assert [role for role, _agent, _model in seen] == ["review", "calibrate"]
    dropped = "Resume dropped the stored skip_calibrate from the checkpoint"
    assert dropped in caplog.text
    assert any(dropped in line for line in context.logs)
    assert "dropped the stored dry_run" not in caplog.text
    assert (context.skip_calibrate, context.auto_calibrate) == (False, True)
    assert load_checkpoint(args.checkpoint).context.skip_calibrate is False


def test_a_legacy_checkpoint_skip_review_never_skips_review_on_resume(worktree, intent_file):
    path = _save_runner_checkpoint(worktree)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["context"]["skip_review"] = True
    path.write_text(json.dumps(data), encoding="utf-8")

    context, seen = _resume_with_recording_stages(_dry_review_resume_args(intent_file, worktree))

    assert [role for role, _agent, _model in seen] == ["review", "calibrate"]
    assert not hasattr(context, "skip_review")
    assert "skip_review" not in RUN_SWITCHES


def test_only_the_switches_passed_to_this_run_take_effect(worktree, intent_file):
    _save_runner_checkpoint(worktree, skip_calibrate=True, auto_calibrate=False)
    args = _dry_review_resume_args(intent_file, worktree, skip_calibrate=True)

    context, seen = _resume_with_recording_stages(args)

    assert [role for role, _agent, _model in seen] == ["review"]
    assert {name: getattr(context, name) for name in RUN_SWITCHES} == {
        "dry_run": True,
        "skip_push": True,
        "skip_calibrate": True,
    }


@pytest.mark.parametrize("model_from", ["flag", "env"])
def test_agent_on_resume_overrides_the_checkpoint_runner_in_the_resumed_context_and_checkpoint(
    worktree, intent_file, monkeypatch, caplog, model_from
):
    _save_runner_checkpoint(worktree, agent="antigravity-agent", model=default_model_for("antigravity-agent"))
    claude_model = default_model_for("claude-agent")
    args = _dry_review_resume_args(intent_file, worktree)
    args.agent = "claude-agent"
    if model_from == "flag":
        args.model = claude_model
    else:
        monkeypatch.setenv("HOLON_MODEL", claude_model)

    with caplog.at_level(logging.INFO, logger="sandbox_executor.cli.commands._flow_resume"):
        _context, seen = _resume_with_recording_stages(args)

    assert seen == [("review", "claude-agent", claude_model), ("calibrate", "claude-agent", claude_model)]
    assert "Agent runner claude-agent from --agent overrides antigravity-agent" in caplog.text
    model_source = "flag (--model)" if model_from == "flag" else "env (HOLON_MODEL)"
    assert f"Model {claude_model} from {model_source} overrides" in caplog.text
    saved = load_checkpoint(args.checkpoint).context
    assert (saved.agent, saved.model) == ("claude-agent", claude_model)


def test_a_resume_without_agent_or_model_keeps_the_checkpoint_runner(worktree, intent_file, monkeypatch):
    monkeypatch.setenv("HOLON_AGENT", "claude-agent")
    monkeypatch.setenv("HOLON_MODEL", default_model_for("claude-agent"))
    stored_model = default_model_for("antigravity-agent")
    _save_runner_checkpoint(worktree, agent="antigravity-agent", model=stored_model)
    args = _dry_review_resume_args(intent_file, worktree)

    assert _resolve_checkpoint_and_intent(_dry_review_resume_args(intent_file, worktree))[2:] == (
        "antigravity-agent",
        stored_model,
    )
    _context, seen = _resume_with_recording_stages(args)

    assert seen == [("review", "antigravity-agent", stored_model), ("calibrate", "antigravity-agent", stored_model)]
    saved = load_checkpoint(args.checkpoint).context
    assert (saved.agent, saved.model) == ("antigravity-agent", stored_model)


def test_a_checkpoint_without_switches_or_attribution_still_loads_and_resumes(worktree, intent_file):
    legacy_context = _completed_context(worktree).to_dict()
    for key in ("schema_version", "agent", "model", "dry_run", *RUN_SWITCHES):
        legacy_context.pop(key, None)
    path = worktree / ".holon" / "flow" / f"checkpoint-{SLUG}.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"intent_slug": SLUG, "current_stage": "review", "completed_stages": [], "context": legacy_context}),
        encoding="utf-8",
    )

    loaded = load_checkpoint(str(path)).context
    assert not any(getattr(loaded, name) for name in RUN_SWITCHES)
    assert (loaded.agent, loaded.model) == (LEGACY_DEFAULT_AGENT, LEGACY_DEFAULT_MODEL)

    _context, seen = _resume_with_recording_stages(_dry_review_resume_args(intent_file, worktree))

    assert seen == [
        ("review", LEGACY_DEFAULT_AGENT, LEGACY_DEFAULT_MODEL),
        ("calibrate", LEGACY_DEFAULT_AGENT, LEGACY_DEFAULT_MODEL),
    ]
