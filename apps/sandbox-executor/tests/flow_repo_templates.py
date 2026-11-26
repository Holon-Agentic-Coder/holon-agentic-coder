"""Flow repositories with their scenario commits, built in a handful of git processes and served as templates.

``build_flow_history`` gives what ``tests.ledger_conflict_repos.build_flow_repos`` followed by one ``commit_on`` per
commit gives (a bare ``origin`` and a ``work`` clone on ``FLOW_BRANCH``, every branch pushed), but writes all commits
into ``origin`` with one ``git fast-import`` and clones it, instead of a ``checkout``/``add``/``commit``/``push`` round
per commit.

``flow_history_copy`` builds that history once per test run as a read-only template (``tests.repo_templates``) and
copies it per test; the template is named by a hash of what it holds, so equal requests share one build.
``derived_template`` layers the fixed state of a fixture over another template the same way.

Every build here runs from a function-scoped fixture or test body, under the session's hermetic git config and the
autouse guards, and runs git only: no production code.
"""

from __future__ import annotations

import functools
import hashlib
import os
import re
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


@dataclass(frozen=True)
class GraphCommit:
    """One commit of ``fast_import``: ``files`` applied to the tree of its first parent (an empty tree without one).

    ``parents`` index earlier commits of the same call; a second parent makes a merge commit.
    """

    branch: str
    files: Files
    message: str
    parents: tuple[int, ...] = ()


def fast_import(
    repo: Path, commits: Sequence[GraphCommit], branches: Mapping[str, int] | None = None, identity: str = IDENTITY
) -> tuple[str, ...]:
    """Write ``commits`` into ``repo`` with one ``git fast-import``; each branch ends at its last commit.

    ``branches`` points more branches at commits by index. Returns the sha of each commit, in order. The index and
    the working tree are left alone.
    """
    stamp = f"{identity} {int(time.time())} +0000\n".encode()
    stream = b""
    for mark, commit in enumerate(commits, start=1):
        # A commit without a parent is a root, even on a branch the stream already wrote.
        stream += b"" if commit.parents else f"reset refs/heads/{commit.branch}\n".encode()
        stream += f"commit refs/heads/{commit.branch}\nmark :{mark}\n".encode()
        stream += b"author " + stamp + b"committer " + stamp + _data(commit.message.encode() + b"\n")
        for keyword, parent in zip(("from", "merge"), commit.parents, strict=False):
            stream += f"{keyword} :{parent + 1}\n".encode()
        stream += _file_ops(commit.files)
    for branch, index in (branches or {}).items():
        stream += f"reset refs/heads/{branch}\nfrom :{index + 1}\n\n".encode()
    marks = repo / ".git" / "fast-import.marks" if (repo / ".git").is_dir() else repo / "fast-import.marks"
    _git(repo, "fast-import", "--quiet", f"--export-marks={marks}", stdin=stream)
    shas = dict(line.split() for line in marks.read_text(encoding="utf-8").splitlines())
    marks.unlink()
    return tuple(shas[f":{mark}"] for mark in range(1, len(commits) + 1))


def _flow_graph(base_files: Files, commits: Sequence[FlowCommit]) -> tuple[list[GraphCommit], int]:
    """The base commit on ``main``, then each ``commit_on`` on its branch's tip; and ``FLOW_BRANCH``'s tip."""
    graph = [GraphCommit("main", base_files, "base")]
    tips = {"main": 0, FLOW_BRANCH: 0}
    for commit in commits:
        if commit.branch not in tips:
            raise ValueError(f"commit_on needs origin/{commit.branch}; only main and {FLOW_BRANCH} exist")
        graph.append(GraphCommit(commit.branch, commit.files, commit.message, (tips[commit.branch],)))
        tips[commit.branch] = len(graph) - 1
    return graph, tips[FLOW_BRANCH]


@functools.cache
def _fetch_creates_origin_head() -> bool:
    """Whether ``git fetch`` creates a missing ``origin/HEAD``, as git 2.48 and later do (``followRemoteHEAD``)."""
    version = subprocess.run(["git", "version"], check=True, capture_output=True, text=True).stdout
    match = re.search(r"(\d+)\.(\d+)", version)
    assert match, version
    return (int(match[1]), int(match[2])) >= (2, 48)


def _as_after_build_flow_repos(work: Path, main_sha: str) -> None:
    """Give a ``clone -b FLOW_BRANCH`` of a filled origin the refs and config of ``build_flow_repos``'s clone.

    That clone was made while origin was empty: it has a local ``main`` tracking ``origin/main``, a ``FLOW_BRANCH``
    without an upstream, and an ``origin/HEAD`` only where its last ``fetch`` created one. Plain file edits, so no
    git process is spent on them.
    """
    git_dir = work / ".git"
    (git_dir / "refs" / "heads" / "main").write_text(main_sha + "\n", encoding="utf-8")
    if not _fetch_creates_origin_head():
        (git_dir / "refs" / "remotes" / "origin" / "HEAD").unlink()
    config = (git_dir / "config").read_text(encoding="utf-8")
    flow_section = f'[branch "{FLOW_BRANCH}"]\n\tremote = origin\n\tmerge = refs/heads/{FLOW_BRANCH}\n'
    assert flow_section in config, config
    main_section = '[branch "main"]\n\tremote = origin\n\tmerge = refs/heads/main\n'
    (git_dir / "config").write_text(config.replace(flow_section, main_section), encoding="utf-8")


def build_flow_history(root: Path, base_files: Files | None = None, commits: Sequence[FlowCommit] = ()) -> FlowRepos:
    """``build_flow_repos(root, base_files)`` then ``commit_on`` for each of ``commits``, in three git processes.

    The commits go straight into ``origin`` (one ``fast-import``), and ``work`` is cloned from it on ``FLOW_BRANCH``.
    """
    origin, work = root / "origin.git", root / "work"
    _git(root, "init", "-q", "--bare", "-b", "main", str(origin))
    graph, flow_tip = _flow_graph(base_files or DEFAULT_BASE, commits)
    shas = fast_import(origin, graph, {FLOW_BRANCH: flow_tip})
    _git(root, "clone", "-q", "-b", FLOW_BRANCH, str(origin), str(work))
    main_tip = max(index for index, commit in enumerate(graph) if commit.branch == "main")
    _as_after_build_flow_repos(work, shas[main_tip])
    return FlowRepos(origin, work, shas[1 : len(commits) + 1])


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
