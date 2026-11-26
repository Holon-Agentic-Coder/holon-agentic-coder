"""Flow repositories with their scenario commits, built in a handful of git processes and served as templates.

``build_flow_history`` gives what ``tests.ledger_conflict_repos.build_flow_repos`` followed by one ``commit_on`` per
commit gives (a bare ``origin`` and a ``work`` clone on ``FLOW_BRANCH``, every branch pushed), but writes all commits
with one ``git fast-import`` instead of a ``checkout``/``add``/``commit``/``push`` round per commit.

``flow_history_copy`` builds that history once per test run as a read-only template (``tests.repo_templates``) and
copies it per test; the template is named by a hash of what it holds, so equal requests share one build.
``derived_template`` layers the fixed state of a fixture over another template the same way.

Every build here runs from a function-scoped fixture or test body, under the session's hermetic git config and the
autouse guards, and runs git only: no production code.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.ledger_conflict_repos import FLOW_BRANCH
from tests.repo_templates import copy_template, set_user_write, shared_template

Files = Mapping[str, str | bytes | None]
IDENTITY = "Holon Test <test@holon.invalid>"
DEFAULT_BASE = {"README.md": "base\n"}


@dataclass(frozen=True)
class FlowCommit:
    """One ``commit_on``: ``files`` (``None`` deletes a path) committed on ``branch`` and pushed."""

    branch: str
    files: Files
    message: str


@dataclass(frozen=True)
class FlowRepos:
    """The bare ``origin``, the ``work`` clone and the sha of each requested commit, in request order."""

    origin: Path
    work: Path
    shas: tuple[str, ...] = field(default=())


def _git(cwd: Path, *args: str, stdin: bytes | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, input=stdin, check=True, capture_output=True)


def _data(payload: bytes) -> bytes:
    return b"data %d\n" % len(payload) + payload + b"\n"


def _file_ops(files: Files) -> bytes:
    ops = b""
    for rel, text in files.items():
        if text is None:
            ops += b"D " + rel.encode() + b"\n"
            continue
        payload = text if isinstance(text, bytes) else text.encode("utf-8")
        ops += b"M 100644 inline " + rel.encode() + b"\n" + _data(payload)
    return ops


def _fast_import_stream(base_files: Files, commits: Sequence[FlowCommit]) -> bytes:
    """Mark 1 is the base commit on ``main``; mark ``n + 1`` is ``commits[n - 1]`` on its branch."""
    stamp = f"{IDENTITY} {int(time.time())} +0000\n".encode()
    tips = {"main": 1, FLOW_BRANCH: 1}
    stream = b"commit refs/heads/main\nmark :1\nauthor " + stamp + b"committer " + stamp + _data(b"base\n")
    stream += _file_ops(base_files)
    for mark, commit in enumerate(commits, start=2):
        if commit.branch not in tips:
            raise ValueError(f"commit_on needs origin/{commit.branch}; only main and {FLOW_BRANCH} exist")
        stream += f"commit refs/heads/{commit.branch}\nmark :{mark}\n".encode() + b"author " + stamp
        stream += b"committer " + stamp + _data(commit.message.encode() + b"\n")
        stream += f"from :{tips[commit.branch]}\n".encode() + _file_ops(commit.files)
        tips[commit.branch] = mark
    return stream + f"reset refs/heads/{FLOW_BRANCH}\nfrom :{tips[FLOW_BRANCH]}\n\n".encode()


def build_flow_history(root: Path, base_files: Files | None = None, commits: Sequence[FlowCommit] = ()) -> FlowRepos:
    """``build_flow_repos(root, base_files)`` then ``commit_on`` for each of ``commits``, in five git processes."""
    origin, work = root / "origin.git", root / "work"
    _git(root, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(root, "clone", "-q", str(origin), str(work))
    marks = root / "fast-import.marks"
    stream = _fast_import_stream(base_files or DEFAULT_BASE, commits)
    _git(work, "fast-import", "--quiet", f"--export-marks={marks}", stdin=stream)
    shas = dict(line.split() for line in marks.read_text(encoding="utf-8").splitlines())
    marks.unlink()
    _git(work, "push", "-q", "origin", "--all")
    _git(work, "checkout", "-q", "-f", FLOW_BRANCH)
    return FlowRepos(origin, work, tuple(shas[f":{mark}"] for mark in range(2, len(commits) + 2)))


def template_name(prefix: str, *parts: object) -> str:
    """A name unique to what the template holds: equal requests in any test module share one build."""
    return f"{prefix}-{hashlib.sha256(repr(parts).encode()).hexdigest()[:20]}"


def flow_history_copy(
    tmp_path_factory: pytest.TempPathFactory,
    target: Path,
    base_files: Files | None = None,
    commits: Sequence[FlowCommit] = (),
    then: Sequence[str] = (),
) -> FlowRepos:
    """A private copy in ``target`` of the template ``build_flow_history(base_files, commits)``.

    ``then`` is one more git command run in ``work`` before the template is finished (a merge that stops on its
    conflicts, for example); its exit status is not checked, so the caller asserts the state it needs.
    """
    base = dict(base_files or DEFAULT_BASE)

    def build(root: Path) -> tuple[str, ...]:
        repos = build_flow_history(root, base, commits)
        if then:
            subprocess.run(["git", *then], cwd=repos.work, check=False, capture_output=True)
        return repos.shas

    name = template_name("flow-history", sorted(base.items()), list(commits), list(then))
    template, shas = shared_template(tmp_path_factory, name, build)
    copy_template(template, target)
    return FlowRepos(target / "origin.git", target / "work", shas)


def derived_template(
    tmp_path_factory: pytest.TempPathFactory,
    name: str,
    base: Path,
    names: Iterable[str],
    scenario: Callable[[Path], object],
) -> Path:
    """The template ``name``: a copy of ``names`` from the template ``base`` with ``scenario(root)`` applied once.

    ``scenario`` may only run git and write files (the derived template is built once and shared by every test).
    """

    def build(root: Path) -> None:
        copy_template(base, root, names=tuple(names))
        scenario(root)

    return shared_template(tmp_path_factory, name, build)[0]


def replace_with_copy(template: Path, target: Path, names: Iterable[str]) -> Path:
    """Replace the entries ``names`` of ``target`` (an earlier copy of another template) with ``template``'s."""
    names = tuple(names)
    for name in names:
        if os.path.lexists(target / name):
            set_user_write(target / name, enabled=True)
            shutil.rmtree(target / name)
    return copy_template(template, target, names=names)
