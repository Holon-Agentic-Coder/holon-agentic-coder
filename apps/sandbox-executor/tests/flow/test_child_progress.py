"""Tests for per-child progress: naming, the next-action decision table, origin reconciliation and checkpoint keys."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sandbox_executor.flow.checkpoint import save_checkpoint
from sandbox_executor.flow.child_progress import (
    FAILED,
    IN_PROGRESS,
    MERGED,
    PENDING,
    PROGRESS_KEY,
    REVIEW_DONE,
    child_checkpoint_path,
    has_recorded_decomposition,
    initial_progress,
    next_child_action,
    reconcile_with_origin,
)
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus

from tests.flow.fake_gh import FakeGitHub
from tests.flow.helpers import _git

PLAN = "I-1-root/P-2-agent-model/_"
ITEMS = [{"slug": "add-the-parser"}, {"slug": "Add the CLI flag!"}, {}]


def test_initial_progress_names_each_child_under_the_plan_branch():
    progress = initial_progress(ITEMS, PLAN)

    assert [e["intent_branch"] for e in progress] == [
        "I-1-root/P-2-agent-model--1-add-the-parser/_",
        "I-1-root/P-2-agent-model--2-add-the-cli-flag/_",
        "I-1-root/P-2-agent-model--3-sub-intent-3/_",
    ]
    assert [e["index"] for e in progress] == [1, 2, 3]
    assert {e["status"] for e in progress} == {PENDING}
    assert progress[2]["slug"] == "sub-intent-3"


def _entry(**facts):
    return {**initial_progress([{"slug": "x"}], PLAN)[0], **facts}


@pytest.mark.parametrize(
    ("facts", "checkpoint", "expected"),
    [
        ({}, False, "dispatch"),
        ({"status": MERGED, "pr_number": 7}, False, "skip"),
        ({"status": REVIEW_DONE, "pr_number": 7}, False, "merge"),
        ({"status": REVIEW_DONE}, False, "resume"),
        ({"execution_branch": "I-1/P-2--1-x/P-3/E-4/_"}, False, "resume"),
        ({"pr_number": 7}, False, "resume"),
        ({"pr_url": "https://example.test/pull/7"}, False, "resume"),
        ({"intent_branch_on_origin": True}, False, "resume"),
        ({"origin_lookup_failed": True}, False, "resume"),
        ({"status": IN_PROGRESS}, False, "resume"),
        ({"status": FAILED}, False, "resume"),
        ({}, True, "resume"),
    ],
    ids=[
        "nothing-anywhere",
        "merged",
        "review-done-with-pr",
        "review-done-without-pr",
        "execution-branch-no-pr",
        "pr-number",
        "pr-url",
        "intent-branch",
        "lookup-failed",
        "in-progress",
        "failed",
        "checkpoint",
    ],
)
def test_next_child_action_decision_table(facts, checkpoint, expected):
    assert next_child_action(_entry(**facts), checkpoint_exists=checkpoint) == expected


def test_has_recorded_decomposition_needs_both_the_flag_and_progress():
    def execute(payload):
        return StageResult(stage=FlowStage.EXECUTE, status=StageStatus.FAILED, payload=payload)

    assert has_recorded_decomposition(execute({"decomposed": True, PROGRESS_KEY: [_entry()]})) is True
    assert has_recorded_decomposition(execute({"decomposed": True, PROGRESS_KEY: []})) is False
    assert has_recorded_decomposition(execute({"decomposed": True})) is False
    assert has_recorded_decomposition(execute({PROGRESS_KEY: [_entry()]})) is False
    assert has_recorded_decomposition(None) is False


def test_child_checkpoint_path_is_the_path_save_checkpoint_writes_and_distinct_per_child(tmp_path):
    parent = FlowContext(repo_dir=str(tmp_path), token_reduce=False)
    paths = []
    for entry in initial_progress(ITEMS[:2], PLAN):
        child = FlowContext(
            repo_dir=str(tmp_path),
            token_reduce=False,
            intent_data={
                "slug": entry["slug"],
                "branch": entry["intent_branch"].removesuffix("/_"),
                "parent_intent_id": PLAN,
            },
        )
        assert save_checkpoint(child) == child_checkpoint_path(parent, entry["intent_branch"])
        paths.append(child_checkpoint_path(parent, entry["intent_branch"]))
    assert len(set(paths)) == 2


@pytest.fixture
def origin_with_child(tmp_path: Path) -> tuple[Path, Path, str]:
    """A bare origin with child 1's intent, plan and execution branches, plus a host clone."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    work = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True, capture_output=True)
    _git(work, "remote", "add", "origin", str(origin))
    (work / "README.md").write_text("seed\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "seed")
    intent = initial_progress([{"slug": "x"}], PLAN)[0]["intent_branch"]
    plan = f"{intent.removesuffix('/_')}/P-3-agent-model/_"
    execution = f"{plan.removesuffix('/_')}/E-4-agent-model/_"
    for branch in ("main", PLAN, intent, plan, execution):
        _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    host = tmp_path / "host"
    subprocess.run(["git", "clone", "-q", str(origin), str(host)], check=True, capture_output=True)
    return origin, host, execution


def test_reconcile_fills_in_the_execution_branch_and_pr_from_origin(origin_with_child, tmp_path):
    origin, host, execution = origin_with_child
    gh = FakeGitHub(origin, tmp_path)
    number = gh.open_pr(execution, PLAN)
    progress = [_entry(status=IN_PROGRESS)]

    notes = reconcile_with_origin(str(host), progress, PLAN, runner=gh)

    assert notes == []
    assert progress[0]["execution_branch"] == execution
    assert progress[0]["pr_number"] == number
    assert progress[0]["pr_url"].endswith(f"/pull/{number}")
    assert next_child_action(progress[0]) == "resume"


def test_reconcile_promotes_a_merged_pr_and_origin_overrides_a_stale_checkpoint(origin_with_child, tmp_path):
    origin, host, execution = origin_with_child
    gh = FakeGitHub(origin, tmp_path)
    number = gh.open_pr(execution, PLAN)
    gh.prs[number].update(state="MERGED", oid="abc123")
    progress = [_entry(status=REVIEW_DONE, execution_branch="I-1-root/stale/E-1-a-b/_", pr_number=99)]

    notes = reconcile_with_origin(str(host), progress, PLAN, runner=gh)

    assert progress[0]["status"] == MERGED
    assert progress[0]["merge_commit"] == "abc123"
    assert progress[0]["execution_branch"] == execution
    assert progress[0]["pr_number"] == number
    assert any("execution_branch" in note for note in notes)
    assert any("pr_number" in note for note in notes)
    assert next_child_action(progress[0]) == "skip"


def test_a_pr_merged_into_another_base_is_not_promoted(origin_with_child, tmp_path):
    origin, host, execution = origin_with_child
    gh = FakeGitHub(origin, tmp_path)
    number = gh.open_pr(execution, "main")
    gh.prs[number].update(state="MERGED", oid="abc123")
    progress = [_entry(status=REVIEW_DONE)]

    notes = reconcile_with_origin(str(host), progress, PLAN, runner=gh)

    assert progress[0]["status"] == REVIEW_DONE
    assert progress[0]["pr_number"] == number
    assert progress[0].get("merge_commit") is None
    assert any("not promoted" in note and "'main'" in note for note in notes)
    assert next_child_action(progress[0]) == "merge"


def test_an_execution_branch_without_a_pr_resumes_and_a_failed_lookup_never_dispatches(origin_with_child, tmp_path):
    origin, host, execution = origin_with_child
    gh = FakeGitHub(origin, tmp_path)
    progress = [_entry()]

    reconcile_with_origin(str(host), progress, PLAN, runner=gh)
    assert progress[0]["execution_branch"] == execution and progress[0]["pr_number"] is None
    assert next_child_action(progress[0]) == "resume"

    def gh_down(args, cwd):
        return subprocess.CompletedProcess(["gh", *args], 1, "", "HTTP 502")

    fresh = [_entry()]
    reconcile_with_origin(str(host), fresh, PLAN, runner=gh_down)
    assert fresh[0]["origin_lookup_failed"] is True
    assert next_child_action(fresh[0]) == "resume"


def test_a_child_with_nothing_on_origin_is_dispatched_and_a_merged_entry_is_not_queried(origin_with_child, tmp_path):
    origin, host, _ = origin_with_child
    gh = FakeGitHub(origin, tmp_path)
    nothing = initial_progress([{"slug": "x"}, {"slug": "never-started"}], PLAN)[1]
    merged = _entry(status=MERGED, pr_number=5)

    reconcile_with_origin(str(host), [nothing, merged], PLAN, runner=gh)

    assert next_child_action(nothing) == "dispatch"
    assert nothing["intent_branch_on_origin"] is False
    assert next_child_action(merged) == "skip"
    assert gh.calls == []


def test_a_failed_origin_fetch_marks_the_entry_for_resume(tmp_path):
    progress = [_entry()]
    reconcile_with_origin(str(tmp_path), progress, PLAN, runner=FakeGitHub(tmp_path, tmp_path))
    assert progress[0]["origin_lookup_failed"] is True
    assert next_child_action(progress[0]) == "resume"
