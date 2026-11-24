"""Tests of ``tests/repo_templates.py``: a copy of a template is the test's own, the template stays as built."""

import subprocess
from pathlib import Path

import pytest

from tests.flow.helpers import _git, _set_user_write
from tests.repo_templates import copy_template, finish_template, remove_sample_hooks


def _build(root: Path) -> Path:
    """A bare origin with one pushed commit on ``main`` and a clone of it, under ``root``."""
    root.mkdir(exist_ok=True)
    origin, work = root / "origin.git", root / "work"
    _git(root, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(root, "clone", "-q", str(origin), str(work))
    (work / "README.md").write_text("base\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "base")
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(work, "fetch", "-q", "origin")
    return root


@pytest.fixture(scope="module")
def template(tmp_path_factory, hermetic_git_config) -> Path:
    root = _build(tmp_path_factory.mktemp("repo-template"))
    # A setting that names a path inside the template, as the bare origin of test_executor_run_salvage.py has.
    _git(root / "origin.git", "config", "core.hooksPath", str(root / "origin.git" / "hooks"))
    return finish_template(root)


def _refs(repo: Path) -> str:
    return _git(repo, "for-each-ref", "--format=%(refname) %(objectname)")


def _commit_and_push(work: Path, name: str) -> str:
    (work / name).write_text(f"{name}\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", name)
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    return _git(work, "rev-parse", "HEAD")


def test_copied_clone_pushes_to_the_copied_origin(template, tmp_path):
    copy = copy_template(template, tmp_path / "copy")

    assert copy == tmp_path / "copy"
    assert _git(copy / "work", "remote", "get-url", "origin") == str(copy / "origin.git")
    assert not (copy / "work" / ".git" / "FETCH_HEAD").exists()
    pushed = _commit_and_push(copy / "work", "one.txt")
    assert _git(copy / "origin.git", "rev-parse", "refs/heads/main") == pushed
    _git(copy / "work", "fetch", "-q", "origin")
    assert _git(copy / "work", "rev-parse", "refs/remotes/origin/main") == pushed


def test_template_is_unchanged_by_work_in_a_copy_and_cannot_be_written(template, tmp_path):
    refs_before = {name: _refs(template / name) for name in ("origin.git", "work")}
    config_before = (template / "work" / ".git" / "config").read_text(encoding="utf-8")
    copy = copy_template(template, tmp_path / "copy")

    _commit_and_push(copy / "work", "one.txt")

    assert {name: _refs(template / name) for name in ("origin.git", "work")} == refs_before
    assert (template / "work" / ".git" / "config").read_text(encoding="utf-8") == config_before
    with pytest.raises(PermissionError):
        (template / "work" / "written.txt").write_text("x", encoding="utf-8")
    with pytest.raises(subprocess.CalledProcessError):
        _git(template / "work", "commit", "-q", "--allow-empty", "-m", "into the template")


def test_two_copies_do_not_see_each_others_commits(template, tmp_path):
    first = copy_template(template, tmp_path / "first")
    second = copy_template(template, tmp_path / "second")
    base = _git(template / "origin.git", "rev-parse", "refs/heads/main")

    pushed = _commit_and_push(first / "work", "one.txt")
    _git(second / "work", "fetch", "-q", "origin")

    assert pushed != base
    assert _git(second / "origin.git", "rev-parse", "refs/heads/main") == base
    assert _git(second / "work", "rev-parse", "refs/remotes/origin/main") == base


def test_setting_that_names_a_template_path_names_the_copy(template, tmp_path):
    copy = copy_template(template, tmp_path / "copy")

    assert _git(template / "origin.git", "config", "core.hooksPath") == str(template / "origin.git" / "hooks")
    assert _git(copy / "origin.git", "config", "core.hooksPath") == str(copy / "origin.git" / "hooks")


def test_only_the_named_directories_are_copied_into_an_existing_target(template, tmp_path):
    (tmp_path / "home").mkdir()

    copy = copy_template(template, tmp_path, names=("origin.git",))

    assert sorted(path.name for path in copy.iterdir()) == ["home", "origin.git"]
    assert _git(copy / "origin.git", "rev-parse", "refs/heads/main") == _git(
        template / "origin.git", "rev-parse", "refs/heads/main"
    )


def test_template_with_a_linked_worktree_is_refused(tmp_path):
    root = _build(tmp_path / "template")
    _git(root / "work", "worktree", "add", "-q", str(root / "linked"), "-b", "linked")
    finish_template(root)

    try:
        with pytest.raises(ValueError, match="linked worktree"):
            copy_template(root, tmp_path / "copy")
    finally:
        _set_user_write(root, enabled=True)  # so that pytest can remove tmp_path


def test_no_sample_hook_is_left_after_the_removal(tmp_path):
    root = _build(tmp_path / "template")
    hooks = [root / "origin.git" / "hooks", root / "work" / ".git" / "hooks"]
    for directory in hooks:
        directory.mkdir(exist_ok=True)
        (directory / "pre-commit.sample").write_text("#!/bin/sh\n", encoding="utf-8")
        (directory / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")

    remove_sample_hooks(root)

    assert not list(root.glob("**/*.sample"))
    assert all((directory / "pre-commit").is_file() for directory in hooks), "only the samples are removed"
