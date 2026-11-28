"""Adopting a target merge a flow left in progress: finish it, abort and redo it, or leave a foreign merge alone."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from sandbox_executor.flow.context import FlowContext, FlowStage, StageStatus
from sandbox_executor.flow.target_sync_hooks import at_flow_start, at_stage_boundary
from sandbox_executor.flow.target_sync_merge import SYNC_MERGE_PREFIX, merge_in_progress
from sandbox_executor.flow.target_sync_result import SyncBoundary, SyncRequest
from sandbox_executor.flow.target_sync_resume import adopt_merge_in_progress
from sandbox_executor.ledger import read_merged_rows

from tests.flow.helpers import _git
from tests.flow_repo_templates import FlowCommit, FlowRepos, flow_history_copy
from tests.ledger_conflict_repos import FLOW_BRANCH, commit_on

LEDGER = "holon-knowledge/ledger/executions.jsonl"
BASE_ROW = json.dumps({"execution_id": "E-base", "created_at": "2026-11-01T00:00:00Z"}) + "\n"


def _row(execution_id: str, day: int) -> str:
    return json.dumps({"execution_id": execution_id, "created_at": f"2026-11-{day:02d}T00:00:00Z"}) + "\n"


def _interrupted_merge(work: Path, ref: str, message: str = f"{SYNC_MERGE_PREFIX}origin/main") -> None:
    args = ["git", "merge", "--no-ff", "--no-commit", *(["-m", message] if message else []), ref]
    proc = subprocess.run(args, cwd=work, capture_output=True, text=True)
    assert proc.returncode in (0, 1), proc.stderr
    assert merge_in_progress(str(work))


def _request(work: Path) -> SyncRequest:
    return SyncRequest(str(work), FLOW_BRANCH, "main", "review", SyncBoundary.FLOW_START, str(work / LEDGER), "E-3-z")


def _sync_rows(work: Path) -> list[dict]:
    return [row for row in read_merged_rows(str(work / LEDGER)) if row.get("action") == "target_sync"]


CODE_CONFLICT = (
    FlowCommit("main", {"src/app.py": "x = 1\n"}, "target"),
    FlowCommit(FLOW_BRANCH, {"src/app.py": "x = 2\n"}, "flow"),
)


@pytest.fixture
def repos_with(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> Callable[..., FlowRepos]:
    """Makes the flow clone with its own ledger row and then ``commits`` pushed: a copy of a template built once."""

    def make(*commits: FlowCommit) -> FlowRepos:
        flow_row = FlowCommit(FLOW_BRANCH, {LEDGER: BASE_ROW + _row("E-flow", 3)}, "flow row")
        return flow_history_copy(
            tmp_path_factory, tmp_path, {LEDGER: BASE_ROW, "src/app.py": "x = 0\n"}, (flow_row, *commits)
        )

    return make


@pytest.fixture
def work(repos_with) -> Path:
    return repos_with().work


def test_no_merge_in_progress_is_nothing_to_adopt(work: Path):
    assert adopt_merge_in_progress(_request(work)) is None


def test_a_ledger_only_merge_of_the_current_target_is_completed_with_one_row(repos_with):
    repos = repos_with(FlowCommit("main", {LEDGER: BASE_ROW + _row("E-main", 4)}, "target row"))
    work, target = repos.work, repos.shas[-1]
    _interrupted_merge(work, "origin/main")

    adoption = adopt_merge_in_progress(_request(work))

    assert adoption is not None and adoption.action == "completed"
    assert not merge_in_progress(str(work))
    assert _git(work, "rev-parse", "HEAD^2") == target
    assert len(_sync_rows(work)) == 1
    assert _git(work, "ls-remote", "origin", f"refs/heads/{FLOW_BRANCH}").split()[0] == _git(work, "rev-parse", "HEAD")


def test_a_merge_of_an_older_target_is_aborted_for_a_redo(repos_with):
    work = repos_with(
        FlowCommit("main", {LEDGER: BASE_ROW + _row("E-main", 4)}, "target row"),
        FlowCommit("main", {"docs/more.md": "m\n"}, "target moves again"),
    ).work
    head = _git(work, "rev-parse", "HEAD")
    _interrupted_merge(work, "origin/main~1")

    adoption = adopt_merge_in_progress(_request(work))

    assert adoption is not None and adoption.action == "aborted"
    assert not merge_in_progress(str(work))
    assert _git(work, "rev-parse", "HEAD") == head
    assert _git(work, "status", "--porcelain") == ""


def test_a_code_conflict_with_the_fail_closed_resolver_leaves_a_clean_worktree(repos_with):
    work = repos_with(*CODE_CONFLICT).work
    _interrupted_merge(work, "origin/main")

    adoption = adopt_merge_in_progress(_request(work))

    assert adoption is not None and adoption.action == "aborted"
    assert not merge_in_progress(str(work))
    assert _git(work, "status", "--porcelain") == ""


def test_a_merge_of_another_branch_is_left_untouched(work: Path):
    _git(work, "checkout", "-q", "-b", "operator-branch", "origin/main")
    _git(work, "commit", "-q", "--allow-empty", "-m", "operator work")
    _git(work, "checkout", "-q", FLOW_BRANCH)
    _interrupted_merge(work, "operator-branch")

    adoption = adopt_merge_in_progress(_request(work))

    assert adoption is not None and adoption.action == "refused"
    assert "not from origin/main" in adoption.error
    assert merge_in_progress(str(work))


def test_a_hand_merge_of_a_branch_already_in_the_target_is_left_untouched(work: Path):
    _git(work, "checkout", "-q", "-b", "landed", "origin/main")
    commit_on(work, "landed", {"docs/landed.md": "l\n"}, "landed work")
    _git(work, "push", "-q", "origin", "landed:main")
    _git(work, "checkout", "-q", FLOW_BRANCH)
    _interrupted_merge(work, "landed", message="")

    adoption = adopt_merge_in_progress(_request(work))

    assert adoption is not None and adoption.action == "refused"
    assert "not started by the target sync" in adoption.error
    assert merge_in_progress(str(work))


def test_markers_staged_before_the_interruption_block_the_resumed_merge(repos_with):
    work = repos_with(*CODE_CONFLICT).work
    head = _git(work, "rev-parse", "HEAD")
    _interrupted_merge(work, "origin/main")
    _git(work, "add", "src/app.py")  # a code resolver staged the file, markers and all, then the flow was interrupted

    adoption = adopt_merge_in_progress(_request(work))

    assert adoption is not None and adoption.action == "aborted"
    assert "conflict markers remain in: src/app.py" in adoption.error
    assert _git(work, "rev-parse", "HEAD") == head


def test_stage3_resume_syncs_plan_branch_with_prior_failed_execution_branch(repos_with):
    work = repos_with(FlowCommit("main", {"docs/main-advance.md": "m\n"}, "main advances")).work
    prior_failed = "I-1-x/P-2/E-failed/_"
    _git(work, "push", "-q", "origin", f"refs/remotes/origin/{FLOW_BRANCH}:refs/heads/{prior_failed}")
    _git(work, "fetch", "-q", "origin")
    prior_tip_before = _git(work, "rev-parse", f"origin/{prior_failed}")
    main_sha = _git(work, "rev-parse", "origin/main")
    ctx = FlowContext(
        target_worktree=str(work),
        intent_data={"target_branch": "main"},
        plan_branch=FLOW_BRANCH,
        execution_branch=prior_failed,
    )

    assert at_flow_start(ctx, FlowStage.EXECUTE) is None
    assert at_stage_boundary(ctx, FlowStage.EXECUTE, SyncBoundary.STAGE_START) is None
    plan_tip = _git(work, "rev-parse", f"origin/{FLOW_BRANCH}")
    assert subprocess.run(["git", "merge-base", "--is-ancestor", main_sha, plan_tip], cwd=work).returncode == 0
    assert _git(work, "rev-parse", f"origin/{prior_failed}") == prior_tip_before
    assert not merge_in_progress(str(work))
    assert _git(work, "status", "--porcelain") == ""


def test_stage3_resume_conflict_on_plan_branch_blocks_cleanly_with_prior_execution_branch(repos_with):
    work = repos_with(*CODE_CONFLICT).work
    prior_failed = "I-1-x/P-2/E-failed/_"
    _git(work, "push", "-q", "origin", f"refs/remotes/origin/{FLOW_BRANCH}:refs/heads/{prior_failed}")
    _git(work, "fetch", "-q", "origin")
    old_plan_tip = _git(work, "rev-parse", f"origin/{FLOW_BRANCH}")
    ctx = FlowContext(
        target_worktree=str(work),
        intent_data={"target_branch": "main"},
        plan_branch=FLOW_BRANCH,
        execution_branch=prior_failed,
        dispatcher=lambda *_a, **_k: 1,
    )

    failure = at_flow_start(ctx, FlowStage.EXECUTE)

    assert failure is not None and failure.status == StageStatus.FAILED
    assert _git(work, "rev-parse", f"origin/{FLOW_BRANCH}") == old_plan_tip
    assert not merge_in_progress(str(work))
    assert _git(work, "status", "--porcelain") == ""
