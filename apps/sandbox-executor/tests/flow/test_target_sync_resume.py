"""Adopting a target merge a flow left in progress: finish it, abort and redo it, or leave a foreign merge alone."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.flow.target_sync_merge import SYNC_MERGE_PREFIX, merge_in_progress
from sandbox_executor.flow.target_sync_result import SyncBoundary, SyncRequest
from sandbox_executor.flow.target_sync_resume import adopt_merge_in_progress

from tests.flow.helpers import _git
from tests.ledger_conflict_repos import FLOW_BRANCH, build_flow_repos, commit_on

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
    lines = (work / LEDGER).read_text(encoding="utf-8").splitlines()
    return [row for row in map(json.loads, lines) if row.get("action") == "target_sync"]


@pytest.fixture
def work(tmp_path: Path) -> Path:
    _, work = build_flow_repos(tmp_path, {LEDGER: BASE_ROW, "src/app.py": "x = 0\n"})
    commit_on(work, FLOW_BRANCH, {LEDGER: BASE_ROW + _row("E-flow", 3)}, "flow row")
    return work


def test_no_merge_in_progress_is_nothing_to_adopt(work: Path):
    assert adopt_merge_in_progress(_request(work)) is None


def test_a_ledger_only_merge_of_the_current_target_is_completed_with_one_row(work: Path):
    target = commit_on(work, "main", {LEDGER: BASE_ROW + _row("E-main", 4)}, "target row")
    _interrupted_merge(work, "origin/main")

    adoption = adopt_merge_in_progress(_request(work))

    assert adoption is not None and adoption.action == "completed"
    assert not merge_in_progress(str(work))
    assert _git(work, "rev-parse", "HEAD^2") == target
    assert len(_sync_rows(work)) == 1
    assert _git(work, "ls-remote", "origin", f"refs/heads/{FLOW_BRANCH}").split()[0] == _git(work, "rev-parse", "HEAD")


def test_a_merge_of_an_older_target_is_aborted_for_a_redo(work: Path):
    commit_on(work, "main", {LEDGER: BASE_ROW + _row("E-main", 4)}, "target row")
    commit_on(work, "main", {"docs/more.md": "m\n"}, "target moves again")
    head = _git(work, "rev-parse", "HEAD")
    _interrupted_merge(work, "origin/main~1")

    adoption = adopt_merge_in_progress(_request(work))

    assert adoption is not None and adoption.action == "aborted"
    assert not merge_in_progress(str(work))
    assert _git(work, "rev-parse", "HEAD") == head
    assert _git(work, "status", "--porcelain") == ""


def test_a_code_conflict_with_the_fail_closed_resolver_leaves_a_clean_worktree(work: Path):
    commit_on(work, "main", {"src/app.py": "x = 1\n"}, "target")
    commit_on(work, FLOW_BRANCH, {"src/app.py": "x = 2\n"}, "flow")
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
