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
    remote, main = root / "remote.git", root / "repo"
    _git(root, "init", "-q", "--bare", "-b", "main", str(remote))
    _git(root, "init", "-q", "-b", "main", str(main))
    _commit(main, "README.md", "seed\n", "seed")
    _git(main, "remote", "add", "origin", str(remote))
    _git(main, "push", "-q", "-u", "origin", "main")


def build_seeded_primary(root: Path) -> None:
    main = root / "repo"
    _git(root, "init", "-q", "-b", "main", str(main))
    for key, value in (("user.email", "test@holon.com"), ("user.name", "Holon Test"), ("commit.gpgsign", "false")):
        _git(main, "config", key, value)
    _commit(main, "README.md", "seed\n", "seed")


def build_checkout_behind_origin(root: Path) -> None:
    # why: the seed and pusher clones only build the state; tests copy origin.git and checkout
    seed, origin, checkout, pusher = root / "seed", root / "origin.git", root / "checkout", root / "pusher"
    _git(root, "init", "-q", "-b", "main", str(seed))
    _commit(seed, "README.md", "README.md\n", "Add README.md")
    _git(root, "clone", "-q", "--bare", str(seed), str(origin))
    _git(root, "clone", "-q", str(origin), str(checkout))
    _git(root, "clone", "-q", str(origin), str(pusher))
    _commit(pusher, "ahead.txt", "ahead.txt\n", "Add ahead.txt")
    _git(pusher, "push", "-q", "origin", "main")
    _git(checkout, "fetch", "-q", "origin")


# why: copy_template cannot rewrite a linked worktree's absolute paths, so each test adds its own on top of the copy
def copy_of(
    tmp_path_factory: pytest.TempPathFactory,
    target: Path,
    name: str,
    build: Callable[[Path], None],
    names: Iterable[str] = (),
) -> Path:
    return copy_template(shared_template(tmp_path_factory, name, build)[0], target, names=names)
