"""Primary checkouts that the worktree tests start from, built once per test run and copied per test.

Linked worktrees store absolute paths that ``copy_template`` does not rewrite, so a test adds its own ``git worktree
add`` on top of its copy. Each builder runs git only, from a function-scoped fixture, under the environment the
fixture has already set (the session's hermetic git config, or a test's own ``GIT_CONFIG_GLOBAL`` and identity).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

from tests.repo_templates import copy_template, shared_template


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _commit(repo: Path, name: str, text: str, message: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


def build_primary_with_remote(root: Path) -> None:
    """``repo``: a primary checkout on main whose seed commit is pushed to ``remote.git``, with upstream set."""
    remote, main = root / "remote.git", root / "repo"
    _git(root, "init", "-q", "--bare", "-b", "main", str(remote))
    _git(root, "init", "-q", "-b", "main", str(main))
    _commit(main, "README.md", "seed\n", "seed")
    _git(main, "remote", "add", "origin", str(remote))
    _git(main, "push", "-q", "-u", "origin", "main")


def build_seeded_primary(root: Path) -> None:
    """``repo``: a primary checkout on main with a local test identity and one seed commit, and no remote."""
    main = root / "repo"
    _git(root, "init", "-q", "-b", "main", str(main))
    for key, value in (("user.email", "test@holon.com"), ("user.name", "Holon Test"), ("commit.gpgsign", "false")):
        _git(main, "config", key, value)
    _commit(main, "README.md", "seed\n", "seed")


def build_checkout_behind_origin(root: Path) -> None:
    """``origin.git`` cloned from a seed, and a ``checkout`` of it whose origin/main is one commit ahead of main.

    The ``seed`` and ``pusher`` clones only build the state; tests copy ``origin.git`` and ``checkout``.
    """
    seed, origin, checkout, pusher = root / "seed", root / "origin.git", root / "checkout", root / "pusher"
    _git(root, "init", "-q", "-b", "main", str(seed))
    _commit(seed, "README.md", "README.md\n", "Add README.md")
    _git(root, "clone", "-q", "--bare", str(seed), str(origin))
    _git(root, "clone", "-q", str(origin), str(checkout))
    _git(root, "clone", "-q", str(origin), str(pusher))
    _commit(pusher, "ahead.txt", "ahead.txt\n", "Add ahead.txt")
    _git(pusher, "push", "-q", "origin", "main")
    _git(checkout, "fetch", "-q", "origin")


def copy_of(
    tmp_path_factory: pytest.TempPathFactory,
    target: Path,
    name: str,
    build: Callable[[Path], None],
    names: Iterable[str] = (),
) -> Path:
    """Copy ``names`` (all when empty) of the template ``name`` that ``build`` makes once per run into ``target``."""
    return copy_template(shared_template(tmp_path_factory, name, build)[0], target, names=names)
