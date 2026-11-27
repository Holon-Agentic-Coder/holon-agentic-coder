import json
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.flow import FlowContext, FlowStage
from sandbox_executor.flow.checkpoint import save_checkpoint
from sandbox_executor.flow.resume_guard import REQUIRED_BRANCH, _resume_context_error, _resume_sync_request
from sandbox_executor.flow.target_sync_merge import SYNC_MERGE_PREFIX, merge_in_progress

from tests.ledger_conflict_repos import FLOW_BRANCH, build_flow_repos, commit_on

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


def test_a_review_resume_checks_stage_4_even_when_calibration_is_skipped(tmp_path, existing_checkpoint):
    error = _resume_context_error(_context(tmp_path, skip_calibrate=True), FlowStage.REVIEW)
    assert error is not None
    assert "stage 'review'" in error


def test_a_skipped_calibration_resume_has_no_stage_to_check(tmp_path, existing_checkpoint):
    assert _resume_context_error(_context(tmp_path, skip_calibrate=True), FlowStage.CALIBRATE) is None


def _flow_worktree_midway_through_ledger_only_merge(tmp_path: Path) -> Path:
    ledger = "holon-knowledge/ledger/executions.jsonl"
    base = json.dumps({"execution_id": "E-base"}) + "\n"
    _, work = build_flow_repos(tmp_path, {ledger: base})
    commit_on(work, "main", {ledger: base + json.dumps({"execution_id": "E-main"}) + "\n"}, "target")
    commit_on(work, FLOW_BRANCH, {ledger: base + json.dumps({"execution_id": "E-flow"}) + "\n"}, "flow")
    message = f"{SYNC_MERGE_PREFIX}origin/main"
    subprocess.run(
        ["git", "merge", "--no-ff", "--no-commit", "-m", message, "origin/main"], cwd=work, capture_output=True
    )
    return work


def _merging(work: Path) -> bool:
    return merge_in_progress(str(work))


def test_a_resume_adopts_a_target_merge_before_the_guard_passes(tmp_path):
    work = _flow_worktree_midway_through_ledger_only_merge(tmp_path)
    assert _merging(work)
    context = _context(work, **ALL_BRANCHES)

    assert _resume_context_error(context, FlowStage.REVIEW) is None
    assert not _merging(work)


def test_a_dry_run_resume_reports_the_merge_without_adopting_it(tmp_path):
    work = _flow_worktree_midway_through_ledger_only_merge(tmp_path)
    context = _context(work, dry_run=True, **ALL_BRANCHES)

    assert _resume_context_error(context, FlowStage.REVIEW) is None
    assert _merging(work)


def test_a_run_from_stage_one_never_adopts(tmp_path):
    work = _flow_worktree_midway_through_ledger_only_merge(tmp_path)

    assert _resume_context_error(_context(work), None) is None
    assert _merging(work)


def test_an_execution_branch_of_only_placeholders_falls_back_to_the_checked_out_branch(tmp_path):
    work = _flow_worktree_midway_through_ledger_only_merge(tmp_path)
    context = _context(work, **{**ALL_BRANCHES, "execution_branch": "_"})

    request = _resume_sync_request(context, FlowStage.REVIEW)

    assert request is not None and request.execution_id == "E-3-z"


def test_a_resume_refused_for_its_branch_never_adopts_the_merge(tmp_path, existing_checkpoint, monkeypatch):
    adopted: list[FlowStage] = []
    monkeypatch.setattr(
        "sandbox_executor.flow.resume_guard.resume_merge_error", lambda _ctx, stage: adopted.append(stage)
    )

    assert _resume_context_error(_context(tmp_path), FlowStage.REVIEW) is not None
    assert adopted == []
    assert _resume_context_error(_context(tmp_path, **ALL_BRANCHES), FlowStage.REVIEW) is None
    assert adopted == [FlowStage.REVIEW]
