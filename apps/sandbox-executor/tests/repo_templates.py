"""Build fixture git repositories once and hand every test its own copy.

Three rules of use:

- A template is never written to after it is built: ``finish_template`` makes the tree read-only, so a test that
  commits in or pushes to the template fails loudly.
- A test only works in its own copy, made by ``copy_template``. The copy's remotes and settings that named the
  template (a remote URL, ``core.hooksPath``) name the copy, without a git process.
- A session fixture asks ``shared_template`` for its template. The template is then built once per test run: under
  xdist the worker that asks first builds it in the directory all workers share, and the others use that tree.
- A build that runs production code is asked for by a function-scoped fixture instead: a session fixture is set up
  before the autouse guards of the first test, and the build would run without them.
"""

import contextlib
import fcntl
import os
import pickle
import shutil
import stat
import subprocess
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest


def set_user_write(root: Path, enabled: bool) -> None:
    """Add or strip the owner's write bit on every directory and file under ``root``."""
    for dirpath, _dirnames, filenames in os.walk(root):
        for path in [dirpath, *(os.path.join(dirpath, name) for name in filenames)]:
            if os.path.islink(path):
                continue
            mode = os.stat(path).st_mode
            os.chmod(path, mode | stat.S_IWUSR if enabled else mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def remove_sample_hooks(root: Path) -> None:
    """Delete git's ``hooks/*.sample`` files under ``root``: they are never run, so no copy needs them."""
    for sample in root.glob("**/hooks/*.sample"):
        sample.unlink()


def pack_objects(root: Path) -> None:
    """Put the objects of every repository under ``root`` into one pack each, as a cloned repository has them.

    A loose object is a directory and a file that every copy creates again; a pack is three files for all of them.
    """
    for git_dir in list(_git_dirs(root)):
        command = ["git", "--git-dir", str(git_dir), "repack", "-a", "-d", "-n", "-q"]
        subprocess.run(command, check=True, capture_output=True)


def finish_template(root: Path) -> Path:
    """Drop the sample hooks of a built template, pack its objects and make its tree read-only; returns ``root``."""
    remove_sample_hooks(root)
    pack_objects(root)
    set_user_write(root, enabled=False)
    return root


@contextlib.contextmanager
def _one_builder(lock_file: Path) -> Iterator[None]:
    """Hold the exclusive lock on ``lock_file``: one thread of one process is inside at a time."""
    with open(lock_file, "w", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def shared_template(
    tmp_path_factory: pytest.TempPathFactory, name: str, build: Callable[[Path], Any]
) -> tuple[Path, Any]:
    """The template ``name`` of this test run and what its ``build`` returned; built by the first caller only.

    ``build(root)`` fills the empty directory ``root`` and may return a value that ``pickle`` can store (paths below
    ``root``, results of the work done in it). The template is then finished with ``finish_template``. Every later
    caller of the run, in this process or in another xdist worker, gets the same ``root`` and its own copy of the
    value. A build that fails leaves nothing behind, so the next caller builds again and fails the same way.

    Args:
        tmp_path_factory: The session's factory; under xdist its base directory is a child of the one all workers
            share, and the template is built there.
        name: Name of the template, unique in the test run.
        build: Builds the repositories in the directory it is given.

    Returns:
        The read-only template root and the value of ``build``.
    """
    base = tmp_path_factory.getbasetemp()
    shared = base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base
    root, built = shared / f"shared-{name}", shared / f"shared-{name}.built"
    with _one_builder(shared / f"shared-{name}.lock"):
        if not built.exists():
            root.mkdir()
            try:
                value = pickle.dumps(build(root))
                finish_template(root)
            except BaseException:
                set_user_write(root, enabled=True)
                shutil.rmtree(root)
                raise
            # The marker appears complete or not at all, and only after the template is finished.
            unfinished = built.with_suffix(".unfinished")
            unfinished.write_bytes(value)
            unfinished.replace(built)
        return root, pickle.loads(built.read_bytes())


def _git_dirs(root: Path) -> Iterable[Path]:
    """Every git directory under ``root``: a bare repository itself, or the ``.git`` of a clone."""
    for dirpath, dirnames, filenames in os.walk(root):
        if "HEAD" in filenames and "config" in filenames and "objects" in dirnames:
            dirnames.clear()
            yield Path(dirpath)


def _reroot_git_dir(git_dir: Path, template: Path, target: Path) -> None:
    """Make one copied git directory name ``target`` wherever it named ``template``."""
    if (git_dir / "worktrees").is_dir():
        raise ValueError(
            f"{template} has a linked worktree ({git_dir.relative_to(target)}/worktrees): it stores absolute paths "
            "that copy_template does not rewrite, so build this repository per test instead"
        )
    # FETCH_HEAD names the URL fetched from, which is a template path.
    (git_dir / "FETCH_HEAD").unlink(missing_ok=True)
    config = git_dir / "config"
    text = config.read_text(encoding="utf-8")
    # The longer spelling first: where the temporary directory is a symlink, one spelling ends with the other.
    template_roots = sorted({str(template), os.path.realpath(template)}, key=len, reverse=True)
    for template_root in template_roots:
        text = text.replace(template_root, str(target))
    config.write_text(text, encoding="utf-8")
    reread = config.read_text(encoding="utf-8")
    assert not any(root in reread for root in template_roots), f"{config} still names the shared template {template}"


def copy_template(template: Path, target: Path, names: Iterable[str] = ()) -> Path:
    """Copy the repositories of ``template`` into ``target`` and re-point them at the copy; returns ``target``.

    Args:
        template: Root of a template finished with ``finish_template``.
        target: Directory the copy goes to; created when missing. Entries it already has are left alone.
        names: Top-level directories of the template to copy; all of them when empty.

    Raises:
        ValueError: The template has a linked worktree.
    """
    target.mkdir(parents=True, exist_ok=True)
    for name in names or sorted(os.listdir(template)):
        copied = target / name
        shutil.copytree(template / name, copied, symlinks=True)
        set_user_write(copied, enabled=True)
        for git_dir in _git_dirs(copied):
            _reroot_git_dir(git_dir, template, target)
    return target
