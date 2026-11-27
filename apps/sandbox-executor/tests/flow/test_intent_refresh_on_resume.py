import argparse
import copy
import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor import intent_refresh
from sandbox_executor.cli.commands.flow import _build_flow_context, _resolve_checkpoint_and_intent
from sandbox_executor.flow import FlowContext, FlowStage, PipelineEngine, StageResult, StageStatus
from sandbox_executor.flow.checkpoint import load_checkpoint, save_checkpoint
from sandbox_executor.intent_refresh import REFRESHED_INTENT_FIELDS, SELF_EXPLAINING_SENTENCE, refresh_intent_text
from sandbox_executor.launcher.coherence import FlowProxyChoice

from tests.self_doc_scanner import count_lines, find_disallowed_comments, find_docstrings_and_string_statements

SLUG = "resume-refreshes-intent"
IDENTITY = {"slug": SLUG, "target_branch": "main", "intent_id": "I-1", "parent_intent_id": "I-0"}
BRANCHES = {
    "intent_branch": f"I-1-{SLUG}/_",
    "plan_branch": f"I-1-{SLUG}/P-2-CLA-CO5.5/_",
    "execution_branch": f"I-1-{SLUG}/P-2-CLA-CO5.5/E-3-CLA-CO5.5/_",
}
STALE_TEXT = {
    "goal": "Refresh the intent.",
    "description": "old description",
    "rationale": "old rationale",
    "scope": ["old.py"],
    "acceptance_criteria": ["old criterion"],
}
CURRENT_TEXT = {
    "goal": f"Refresh the intent. {SELF_EXPLAINING_SENTENCE}",
    "description": "new description",
    "rationale": "new rationale",
    "scope": ["new.py"],
    "acceptance_criteria": ["new criterion"],
}
_NO_PROXY = FlowProxyChoice(spawn_sidecar=False, attach_running_proxy=False)
_REFRESH_LOGGER = "sandbox_executor.intent_refresh"


def _stale_context(worktree: Path) -> FlowContext:
    context = FlowContext(
        target_worktree=str(worktree),
        intent_data={**IDENTITY, **STALE_TEXT, "replan_feedback": "keep me"},
        dry_run=True,
        **BRANCHES,
    )
    for name in ("intent", "plan", "execute"):
        context.stage_results[name] = StageResult(stage=FlowStage.from_str(name), status=StageStatus.SUCCESS)
    context.stage_results["intent"].payload = {"intent": {**IDENTITY, **STALE_TEXT}}
    return context


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def checkpoint(worktree: Path) -> Path:
    return Path(save_checkpoint(_stale_context(worktree)))


def _intent_file(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "intent.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _args(intent_file: Path | None, worktree: Path, checkpoint: Path | None) -> argparse.Namespace:
    return argparse.Namespace(
        intent_file=str(intent_file) if intent_file else None,
        from_stage="review",
        checkpoint=str(checkpoint) if checkpoint else None,
        target_worktree=str(worktree),
        agent=None,
        model=None,
        dry_run=True,
        skip_push=True,
        skip_calibrate=False,
        allow_overlap=False,
        max_review_iterations=None,
        ensemble_size=None,
        token_reduce=False,
        mitm_web=False,
    )


def test_refresh_replaces_the_intent_text_and_keeps_identity_and_stage_keys(tmp_path, worktree, caplog):
    context = _stale_context(worktree)
    other_identity = {"slug": "other", "branch": "other/_", "intent_id": "I-9", "parent_intent_id": "I-8"}
    path = _intent_file(tmp_path, {**other_identity, **CURRENT_TEXT})

    with caplog.at_level(logging.INFO, logger=_REFRESH_LOGGER):
        changed = refresh_intent_text(context, str(path))

    assert changed == list(REFRESHED_INTENT_FIELDS)
    assert {field: context.intent_data[field] for field in REFRESHED_INTENT_FIELDS} == CURRENT_TEXT
    assert {key: context.intent_data.get(key) for key in IDENTITY} == IDENTITY
    assert "branch" not in context.intent_data
    assert context.intent_data["replan_feedback"] == "keep me"
    assert {name: getattr(context, name) for name in BRANCHES} == BRANCHES
    refreshed = [record.getMessage() for record in caplog.records if record.name == _REFRESH_LOGGER]
    assert refreshed == [
        f"Resume refreshed intent {field} from {path}: the checkpoint held an older text."
        for field in REFRESHED_INTENT_FIELDS
    ]

    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_REFRESH_LOGGER):
        assert refresh_intent_text(context, str(path)) == []
    assert [record for record in caplog.records if record.name == _REFRESH_LOGGER] == []


@pytest.mark.parametrize(
    "goal",
    ["Do it. CODE IS SELF-EXPLAINING: no docstrings.", "Do it. Code is\n    self-explaining: no docstrings."],
)
def test_the_sentence_check_tolerates_case_and_line_wrapping(goal):
    assert intent_refresh.goal_states_self_explaining_code(goal)


def test_a_resume_whose_intent_goal_lacks_the_sentence_is_refused(tmp_path, worktree, checkpoint, capsys):
    before = checkpoint.read_bytes()
    path = _intent_file(tmp_path, {**IDENTITY, **STALE_TEXT})

    with pytest.raises(SystemExit) as exit_info:
        _resolve_checkpoint_and_intent(_args(path, worktree, checkpoint))

    assert exit_info.value.code == 1
    stderr = capsys.readouterr().err
    assert str(path) in stderr
    assert SELF_EXPLAINING_SENTENCE in stderr
    assert checkpoint.read_bytes() == before


def test_a_malformed_intent_file_is_refused_with_a_contract_error(tmp_path):
    path = tmp_path / "intent.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(intent_refresh.IntentContractError, match="is not valid JSON"):
        intent_refresh.read_intent_file(str(path))


def test_a_resume_without_an_intent_file_keeps_the_stored_intent(worktree, checkpoint):
    context, *_ = _resolve_checkpoint_and_intent(_args(None, worktree, checkpoint))

    assert context.intent_data == {**IDENTITY, **STALE_TEXT, "replan_feedback": "keep me"}


@pytest.mark.parametrize("explicit_checkpoint", [False, True])
def test_both_resume_paths_hand_the_refreshed_goal_to_the_stages(tmp_path, worktree, checkpoint, explicit_checkpoint):
    path = _intent_file(tmp_path, {**IDENTITY, **CURRENT_TEXT})
    args = _args(path, worktree, checkpoint if explicit_checkpoint else None)

    context, intent_data, agent, model = _resolve_checkpoint_and_intent(args)
    assert args.checkpoint == str(checkpoint)
    with patch("sandbox_executor.launcher.coherence.resolve_flow_proxy_choice", return_value=_NO_PROXY):
        context = _build_flow_context(args, context, intent_data, agent, model)
    assert context.intent_data["goal"] == CURRENT_TEXT["goal"]

    seen: list[str] = []

    def review(ctx: FlowContext) -> StageResult:
        seen.append(ctx.intent_data["goal"])
        return StageResult(stage=FlowStage.REVIEW, status=StageStatus.SUCCESS)

    calibrate = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS)
    with (
        patch("sandbox_executor.flow.pipeline.run_review_stage", side_effect=review),
        patch("sandbox_executor.flow.pipeline.run_calibrate_stage", return_value=calibrate),
    ):
        PipelineEngine(context, from_stage=args.from_stage, checkpoint_path=args.checkpoint).run()

    assert seen == [CURRENT_TEXT["goal"]]


def _goal_copies(context: FlowContext) -> list[str]:
    return [context.intent_data["goal"], context.stage_results["intent"].payload["intent"]["goal"]]


def test_a_resumed_checkpoint_refreshes_both_goal_copies(tmp_path, checkpoint):
    context = load_checkpoint(str(checkpoint)).context
    assert _goal_copies(context) == [STALE_TEXT["goal"]] * 2

    refresh_intent_text(context, str(_intent_file(tmp_path, {**IDENTITY, **CURRENT_TEXT})))

    assert _goal_copies(context) == [CURRENT_TEXT["goal"]] * 2


@pytest.mark.parametrize("payload", [None, {}, {"intent": {"slug": SLUG}}, {"intent": "not a dict"}])
def test_a_checkpoint_without_an_intent_stage_goal_still_refreshes_the_intent_data(tmp_path, worktree, payload):
    context = _stale_context(worktree)
    if payload is None:
        del context.stage_results["intent"]
    else:
        context.stage_results["intent"].payload = copy.deepcopy(payload)

    changed = refresh_intent_text(context, str(_intent_file(tmp_path, {**IDENTITY, **CURRENT_TEXT})))

    assert changed == list(REFRESHED_INTENT_FIELDS)
    assert getattr(context.stage_results.get("intent"), "payload", None) == payload


@pytest.mark.parametrize(("path", "line_cap"), [(Path(intent_refresh.__file__), 200), (Path(__file__), 400)])
def test_the_new_files_are_self_explaining(path, line_cap):
    source = path.read_text(encoding="utf-8")

    assert find_docstrings_and_string_statements(source) == []
    assert [text for _line, text in find_disallowed_comments(source) if not text.startswith("# why:")] == []
    assert count_lines(source) <= line_cap
