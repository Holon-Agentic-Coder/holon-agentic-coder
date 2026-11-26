"""The host-side attempt guard on real in-progress merges: the conflict-set check, tamper detection and the reset."""

from __future__ import annotations

from collections.abc import Callable
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

from tests.flow.helpers import _git
from tests.flow_repo_templates import FlowCommit, flow_history_copy
from tests.ledger_conflict_repos import FLOW_BRANCH

MERGE = ("merge", "--no-ff", "--no-commit", "origin/main")
BASE = {"app.py": "value = 0\n", "keep.py": "keep = 0\n", "other.py": "other = 0\n", "tests/test_other.py": "x = 0\n"}


Merging = Callable[[dict[str, str | None], dict[str, str | None]], Path]


@pytest.fixture
def merging(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> Merging:
    """Makes a flow worktree part-way through a ``--no-commit`` merge of ``origin/main`` that conflicts.

    The merge state (``MERGE_HEAD``, the conflicted index) is built once per run as a template and copied here.
    """

    def make(ours: dict[str, str | None], theirs: dict[str, str | None]) -> Path:
        commits = (FlowCommit("main", theirs, "target change"), FlowCommit(FLOW_BRANCH, ours, "flow change"))
        work = flow_history_copy(tmp_path_factory, tmp_path, BASE, commits, then=MERGE).work
        assert merge_in_progress(str(work)) and unmerged_paths(str(work))
        return work

    return make


@pytest.fixture
def work(merging: Merging) -> Path:
    return merging({"app.py": "value = 1\n"}, {"app.py": "value = 2\n"})


def _resolve(work: Path) -> None:
    (work / "app.py").write_text("value = 3\n", encoding="utf-8")


def test_in_set_resolution_passes_and_stages(work):
    before = snapshot_merge(str(work))
    _resolve(work)
    check = check_attempt(str(work), ["app.py"], before)
    assert check.ok and check.reason == ""
    assert stage_resolution(str(work), ["app.py"]) == ""
    assert unmerged_paths(str(work)) == []


def test_edit_outside_the_set_is_out_of_set_and_reverted(work):
    before = snapshot_merge(str(work))
    _resolve(work)
    (work / "other.py").write_text("changed\n", encoding="utf-8")
    check = check_attempt(str(work), ["app.py"], before)
    assert not check.ok and not check.tampered and check.out_of_set == ("other.py",)
    assert reset_attempt(str(work), ["app.py"], check) == ""
    assert (work / "other.py").read_text(encoding="utf-8") == BASE["other.py"]
    assert "<<<<<<< " in (work / "app.py").read_text(encoding="utf-8")


def test_existing_test_edit_outside_the_set_is_allowed_staged_and_reverted_on_reset(work):
    before = snapshot_merge(str(work))
    _resolve(work)
    (work / "tests/test_other.py").write_text("x = 1\n", encoding="utf-8")
    check = check_attempt(str(work), ["app.py"], before)
    assert check.ok and check.out_of_set == () and check.test_edits == ("tests/test_other.py",)
    assert reset_attempt(str(work), ["app.py"], check) == ""
    assert (work / "tests/test_other.py").read_text(encoding="utf-8") == BASE["tests/test_other.py"]
    assert "<<<<<<< " in (work / "app.py").read_text(encoding="utf-8")

    # On a passing attempt, both the conflict path and the edited test file are staged cleanly.
    _resolve(work)
    (work / "tests/test_other.py").write_text("x = 2\n", encoding="utf-8")
    check2 = check_attempt(str(work), ["app.py"], before)
    assert check2.ok and check2.test_edits == ("tests/test_other.py",)
    assert stage_resolution(str(work), ["app.py"], check2.test_edits) == ""
    assert unmerged_paths(str(work)) == []
    assert "tests/test_other.py" in _git(work, "diff", "--cached", "--name-only").splitlines()


def test_new_untracked_file_is_removed_and_older_ones_are_kept(work):
    (work / "notes.txt").write_text("mine\n", encoding="utf-8")
    before = snapshot_merge(str(work))
    _resolve(work)
    (work / "stray.py").write_text("x\n", encoding="utf-8")
    check = check_attempt(str(work), ["app.py"], before)
    assert check.new_untracked == ("stray.py",)
    assert reset_attempt(str(work), ["app.py"], check) == ""
    assert not (work / "stray.py").exists() and (work / "notes.txt").exists()


def test_save_attempt_diff_sanitises_label_and_stays_inside_artefact_dir(work):
    # One copy for every label: the labels are data the save does not branch on (identical lines and arcs per label).
    # The plain label also carries test_saved_diff_artefacts_are_not_new_files: the saved diffs are no new files.
    before = snapshot_merge(str(work))
    _resolve(work)
    artefact_dir = (work / ARTEFACT_DIR).resolve()
    for label in ["attempt-1", "abc-attempt-1-anthropic/claude-opus-5-5", "../../escape", "../a/b..c"]:
        saved = save_attempt_diff(str(work), ["app.py"], label)
        saved_path = Path(saved).resolve()
        assert saved_path.parent == artefact_dir and saved_path.suffix == ".diff", label
        assert (artefact_dir / ".gitignore").read_text(encoding="utf-8") == "*\n", label
        assert "value = 3" in saved_path.read_text(encoding="utf-8"), label
        assert check_attempt(str(work), ["app.py"], before).ok, label
        if label == "attempt-1":
            assert saved.endswith(f"{ARTEFACT_DIR}/attempt-1.diff")


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
def test_reset_restores_the_merge_state_of_each_conflict_kind(merging, ours, theirs, path, expect):
    work = merging(ours, theirs)
    assert unmerged_paths(str(work)) == [path]
    before = snapshot_merge(str(work))
    (work / path).unlink()
    check = check_attempt(str(work), [path], before)
    assert reset_attempt(str(work), [path], check) == ""
    assert unmerged_paths(str(work)) == [path]
    assert expect in (work / path).read_text(encoding="utf-8")
    assert check_attempt(str(work), [path], before).tampered is False


def test_deletion_is_staged_as_a_resolution(merging):
    work = merging({"keep.py": "keep = 1\n"}, {"keep.py": None})
    before = snapshot_merge(str(work))
    (work / "keep.py").unlink()
    assert check_attempt(str(work), ["keep.py"], before).ok
    assert stage_resolution(str(work), ["keep.py"]) == ""
    assert unmerged_paths(str(work)) == [] and "keep.py" not in _git(work, "ls-files")
