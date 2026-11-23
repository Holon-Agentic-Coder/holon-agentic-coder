"""The host-side attempt guard on real in-progress merges: the conflict-set check, tamper detection and the reset."""

from __future__ import annotations

from pathlib import Path

import pytest
from sandbox_executor.flow.conflict_resolver_attempt import (
    ARTEFACT_DIR,
    check_attempt,
    reset_attempt,
    save_attempt_diff,
    snapshot_merge,
    stage_resolution,
)
from sandbox_executor.flow.target_sync_merge import merge_in_progress, unmerged_paths
from sandbox_executor.git import run_git

from tests.flow.helpers import _git
from tests.ledger_conflict_repos import FLOW_BRANCH, build_flow_repos, commit_on

BASE = {"app.py": "value = 0\n", "keep.py": "keep = 0\n", "other.py": "other = 0\n", "tests/test_other.py": "x = 0\n"}


def _merging(tmp_path: Path, ours: dict[str, str | None], theirs: dict[str, str | None]) -> Path:
    """A flow worktree part-way through a ``--no-commit`` merge of ``origin/main`` that conflicts."""
    _, work = build_flow_repos(tmp_path, BASE)
    commit_on(work, "main", theirs, "target change")
    commit_on(work, FLOW_BRANCH, ours, "flow change")
    _git(work, "fetch", "-q", "origin")
    run_git(["merge", "--no-ff", "--no-commit", "origin/main"], cwd=str(work), check=False)
    assert merge_in_progress(str(work)) and unmerged_paths(str(work))
    return work


@pytest.fixture
def work(tmp_path: Path) -> Path:
    return _merging(tmp_path, {"app.py": "value = 1\n"}, {"app.py": "value = 2\n"})


def _resolve(work: Path) -> None:
    (work / "app.py").write_text("value = 3\n", encoding="utf-8")


def test_in_set_resolution_passes_and_stages(work):
    before = snapshot_merge(str(work))
    _resolve(work)
    check = check_attempt(str(work), ["app.py"], before)
    assert check.ok and check.reason == ""
    assert stage_resolution(str(work), ["app.py"]) == ""
    assert unmerged_paths(str(work)) == []


@pytest.mark.parametrize("rel", ["other.py", "tests/test_other.py"])
def test_edit_outside_the_set_is_out_of_set_and_reverted(work, rel):
    before = snapshot_merge(str(work))
    _resolve(work)
    (work / rel).write_text("changed\n", encoding="utf-8")
    check = check_attempt(str(work), ["app.py"], before)
    assert not check.ok and not check.tampered and check.out_of_set == (rel,)
    assert reset_attempt(str(work), ["app.py"], check) == ""
    assert (work / rel).read_text(encoding="utf-8") == BASE[rel]
    assert "<<<<<<< " in (work / "app.py").read_text(encoding="utf-8")


def test_new_untracked_file_is_removed_and_older_ones_are_kept(work):
    (work / "notes.txt").write_text("mine\n", encoding="utf-8")
    before = snapshot_merge(str(work))
    _resolve(work)
    (work / "stray.py").write_text("x\n", encoding="utf-8")
    check = check_attempt(str(work), ["app.py"], before)
    assert check.new_untracked == ("stray.py",)
    assert reset_attempt(str(work), ["app.py"], check) == ""
    assert not (work / "stray.py").exists() and (work / "notes.txt").exists()


def test_saved_diff_artefacts_are_not_new_files(work):
    before = snapshot_merge(str(work))
    _resolve(work)
    saved = save_attempt_diff(str(work), ["app.py"], "attempt-1")
    assert saved.endswith(f"{ARTEFACT_DIR}/attempt-1.diff") and "value = 3" in Path(saved).read_text(encoding="utf-8")
    assert check_attempt(str(work), ["app.py"], before).ok


@pytest.mark.parametrize(
    "write",
    [
        lambda work: _git(work, "add", "other.py"),
        lambda work: _git(work, "add", "app.py"),
        lambda work: _git(work, "commit", "-q", "-am", "agent commit"),
        lambda work: _git(work, "merge", "--abort"),
    ],
    ids=["add-other", "add-conflict-path", "commit", "merge-abort"],
)
def test_git_writes_are_tampering(work, write):
    before = snapshot_merge(str(work))
    _resolve(work)
    (work / "other.py").write_text("changed\n", encoding="utf-8")
    write(work)
    check = check_attempt(str(work), ["app.py"], before)
    assert check.tampered and not check.ok and check.reason


def test_markers_left_behind_fail_the_attempt(work):
    before = snapshot_merge(str(work))
    check = check_attempt(str(work), ["app.py"], before)
    assert check.markers == ("app.py",) and not check.ok


@pytest.mark.parametrize(
    ("ours", "theirs", "path", "expect"),
    [
        ({"app.py": "value = 1\n"}, {"app.py": "value = 2\n"}, "app.py", "<<<<<<< "),
        ({"new.py": "a = 1\n"}, {"new.py": "a = 2\n"}, "new.py", "<<<<<<< "),
        ({"keep.py": "keep = 1\n"}, {"keep.py": None}, "keep.py", "keep = 1\n"),
        ({"keep.py": None}, {"keep.py": "keep = 2\n"}, "keep.py", "keep = 2\n"),
    ],
    ids=["content", "add-add", "modify-delete", "delete-modify"],
)
def test_reset_restores_the_merge_state_of_each_conflict_kind(tmp_path, ours, theirs, path, expect):
    work = _merging(tmp_path, ours, theirs)
    assert unmerged_paths(str(work)) == [path]
    before = snapshot_merge(str(work))
    (work / path).unlink()
    check = check_attempt(str(work), [path], before)
    assert reset_attempt(str(work), [path], check) == ""
    assert unmerged_paths(str(work)) == [path]
    assert expect in (work / path).read_text(encoding="utf-8")
    assert check_attempt(str(work), [path], before).tampered is False


def test_deletion_is_staged_as_a_resolution(tmp_path):
    work = _merging(tmp_path, {"keep.py": "keep = 1\n"}, {"keep.py": None})
    before = snapshot_merge(str(work))
    (work / "keep.py").unlink()
    assert check_attempt(str(work), ["keep.py"], before).ok
    assert stage_resolution(str(work), ["keep.py"]) == ""
    assert unmerged_paths(str(work)) == [] and "keep.py" not in _git(work, "ls-files")
