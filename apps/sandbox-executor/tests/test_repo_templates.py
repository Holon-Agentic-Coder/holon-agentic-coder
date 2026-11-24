"""Tests of ``tests/repo_templates.py``: a copy of a template is the test's own, the template stays as built.

And a template asked for with ``shared_template`` is built once per test run, whoever asks and from which worker.
"""

import os
import stat
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.flow.helpers import _git
from tests.repo_templates import (
    copy_template,
    finish_template,
    remove_sample_hooks,
    set_user_write,
    shared_template,
)


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


def test_template_is_unchanged_by_work_in_a_copy(template, tmp_path):
    refs_before = {name: _refs(template / name) for name in ("origin.git", "work")}
    config_before = (template / "work" / ".git" / "config").read_text(encoding="utf-8")
    copy = copy_template(template, tmp_path / "copy")

    _commit_and_push(copy / "work", "one.txt")

    assert {name: _refs(template / name) for name in ("origin.git", "work")} == refs_before
    assert (template / "work" / ".git" / "config").read_text(encoding="utf-8") == config_before


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the read-only mode, so the premise cannot hold")
def test_template_cannot_be_written(template):
    with pytest.raises(PermissionError):
        (template / "work" / "written.txt").write_text("x", encoding="utf-8")
    with pytest.raises(subprocess.CalledProcessError):
        _git(template / "work", "commit", "-q", "--allow-empty", "-m", "into the template")


def test_objects_of_a_finished_template_are_in_packs_and_all_there(template):
    for git_dir in (template / "origin.git", template / "work" / ".git"):
        entries = sorted(path.name for path in (git_dir / "objects").iterdir())
        assert entries == ["info", "pack"], "no directory of loose objects is left to copy"
        assert len(list((git_dir / "objects" / "pack").glob("*.pack"))) == 1

    _git(template / "work", "fsck", "--full", "--strict")
    assert _git(template / "work", "show", "HEAD:README.md") == "base"
    assert _git(template / "origin.git", "rev-parse", "refs/heads/main") == _git(template / "work", "rev-parse", "HEAD")


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
        set_user_write(root, enabled=True)  # so that pytest can remove tmp_path


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


def _factory(basetemp: Path) -> SimpleNamespace:
    """What ``shared_template`` uses of a ``TempPathFactory``: its base directory, here ``basetemp``."""
    basetemp.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(getbasetemp=lambda: basetemp)


@pytest.fixture
def builds(hermetic_git_config) -> list[Path]:
    """The roots ``_counted_build`` was asked to fill, in order."""
    return []


def _counted_build(builds: list[Path]):
    def build(root: Path) -> SimpleNamespace:
        builds.append(root)
        _build(root)
        return SimpleNamespace(work=root / "work", head=_git(root / "work", "rev-parse", "HEAD"))

    return build


@pytest.fixture
def writable_again(tmp_path):
    """Make every template built under ``tmp_path`` writable after the test, so that pytest can remove it."""
    yield
    set_user_write(tmp_path, enabled=True)


def test_shared_template_is_built_by_the_first_caller_and_finished(monkeypatch, builds, tmp_path, writable_again):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    factory = _factory(tmp_path / "base")

    root, built = shared_template(factory, "one", _counted_build(builds))
    again_root, again_built = shared_template(factory, "one", _counted_build(builds))

    assert builds == [root] and again_root == root, "the second caller gets the tree of the first"
    assert root.parent == tmp_path / "base", "without xdist the template is in the session's own base directory"
    assert built == again_built and built is not again_built, "every caller gets its own copy of the value"
    assert built.work == root / "work" and _git(built.work, "rev-parse", "HEAD") == built.head
    assert not list(root.glob("**/*.sample")), "finished: no sample hook"
    assert not stat.S_IMODE((root / "work").stat().st_mode) & stat.S_IWUSR, "finished: read-only"
    copy = copy_template(root, tmp_path / "copy")
    assert _commit_and_push(copy / "work", "one.txt") == _git(copy / "origin.git", "rev-parse", "refs/heads/main")


def test_two_xdist_workers_share_one_template_in_the_directory_above_their_own(
    monkeypatch, builds, tmp_path, writable_again
):
    workers = [_factory(tmp_path / "run" / f"popen-gw{index}") for index in range(2)]
    roots = []
    for index, worker in enumerate(workers):
        monkeypatch.setenv("PYTEST_XDIST_WORKER", f"gw{index}")
        roots.append(shared_template(worker, "one", _counted_build(builds))[0])

    assert builds == [roots[0]], "the second worker builds nothing"
    assert roots[0] == roots[1] and roots[0].parent == tmp_path / "run"


def test_templates_of_two_names_are_two_builds(monkeypatch, builds, tmp_path, writable_again):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    factory = _factory(tmp_path / "base")

    first = shared_template(factory, "one", _counted_build(builds))[0]
    second = shared_template(factory, "two", _counted_build(builds))[0]

    assert builds == [first, second] and first != second


def test_callers_at_the_same_time_wait_for_the_one_that_builds(monkeypatch, builds, tmp_path, writable_again):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    factory = _factory(tmp_path / "base")
    started, all_started = threading.Semaphore(0), threading.Event()
    results: list[tuple[Path, object]] = []

    def build(root: Path) -> str:
        builds.append(root)
        (root / "built.txt").write_text("built\n", encoding="utf-8")
        # Stay inside until every other caller has started, and a moment longer, so that they arrive at the lock.
        all_started.wait(timeout=10)
        time.sleep(0.05)
        return "value"

    def ask() -> None:
        started.release()
        results.append(shared_template(factory, "one", build))

    callers = [threading.Thread(target=ask) for _ in range(4)]
    for caller in callers:
        caller.start()
    for _ in callers:
        started.acquire()
    all_started.set()
    for caller in callers:
        caller.join(timeout=30)

    # Every caller opens the lock file itself, so the lock holds between threads as it does between worker processes.
    # Without it a second caller would find no finished template and start a build of its own in the same directory.
    assert builds == [tmp_path / "base" / "shared-one"], "one build"
    assert results == [(tmp_path / "base" / "shared-one", "value")] * 4, "and every caller got its template"


def test_a_build_that_fails_leaves_nothing_and_the_next_caller_builds(monkeypatch, builds, tmp_path, writable_again):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    factory = _factory(tmp_path / "base")

    def failing(root: Path) -> None:
        _build(root)
        raise RuntimeError("no template")

    with pytest.raises(RuntimeError, match="no template"):
        shared_template(factory, "one", failing)
    left_behind = sorted(path.name for path in (tmp_path / "base").iterdir())
    root, _built = shared_template(factory, "one", _counted_build(builds))

    assert left_behind == ["shared-one.lock"], "no tree and no marker of a finished build"
    assert builds == [root] and (root / "work" / "README.md").is_file()
