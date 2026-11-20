"""Snapshot helpers behind the suite's ``.holon/`` write guards.

A live ``holon flow`` keeps its checkpoint under ``<worktree>/.holon/flow/``. ``FlowContext.target_worktree``
defaults to ``"."``, so a test that builds a context without pinning ``repo_dir``/``target_worktree`` (or the working
directory) to ``tmp_path`` saves its checkpoint into the checkout pytest runs from and can overwrite the state of the
flow that runs the suite. The fixtures in ``tests/conftest.py`` compare these snapshots before and after each test
and the session.
"""

import os

import pytest

from tests.hermetic_fixtures import describe_index_changes

HOLON_DIR_NAME = ".holon"

#: A snapshot maps a path relative to the first guarded root to ``(size, mtime_ns)``.
Snapshot = dict[str, tuple[int, int]]


def _raise_walk_error(error: OSError) -> None:
    """Re-raise a directory-listing error, which ``os.walk`` would otherwise swallow and so fail open."""
    raise error


def holon_dir_snapshot(root: str, relative_to: str | None = None) -> Snapshot:
    """Return ``(size, mtime_ns)`` per file under ``<root>/.holon/``, keyed by POSIX path relative to ``relative_to``.

    ``relative_to`` defaults to ``root``. Symlinked directories are not followed. A missing ``.holon/`` yields an empty
    mapping; any other ``OSError`` propagates so the guard fails closed.
    """
    base = relative_to or root
    holon_dir = os.path.join(root, HOLON_DIR_NAME)
    snapshot: Snapshot = {}
    if not os.path.isdir(holon_dir):
        return snapshot
    for dirpath, _dirnames, filenames in os.walk(holon_dir, onerror=_raise_walk_error):
        for name in filenames:
            full = os.path.join(dirpath, name)
            st = os.lstat(full)
            snapshot[os.path.relpath(full, base).replace(os.sep, "/")] = (st.st_size, st.st_mtime_ns)
    return snapshot


def guarded_roots(repo_root: str, invocation_dir: str) -> tuple[str, ...]:
    """Return the directories whose ``.holon/`` is guarded: the repository root, then the invocation directory.

    The invocation directory (the session's starting working directory) is added only when it lies inside the
    repository and differs from its root, which covers ``cd apps/sandbox-executor && uv run pytest``. ``~/.holon`` is
    never guarded here: the credential fixtures already isolate it.
    """
    repo = os.path.realpath(repo_root)
    start = os.path.realpath(invocation_dir)
    if start != repo and os.path.commonpath([repo, start]) == repo:
        return (repo, start)
    return (repo,)


def snapshot_roots(roots: tuple[str, ...]) -> Snapshot:
    """Return one combined snapshot of every root's ``.holon/``, keyed relative to the first root."""
    combined: Snapshot = {}
    for root in roots:
        combined.update(holon_dir_snapshot(root, relative_to=roots[0]))
    return combined


def holon_dir_failure(changes: list[str], scope: str) -> str:
    """Return the failure message naming each changed path under ``.holon/`` and how to fix the offending test."""
    return (
        f"{scope} wrote the checkout's .holon/: "
        + ", ".join(changes)
        + " -- pin repo_dir/target_worktree or the working directory to tmp_path to fix this."
    )


def fail_on_holon_dir_changes(roots: tuple[str, ...], before: Snapshot, scope: str) -> None:
    """Re-snapshot ``roots`` and ``pytest.fail`` naming every file created, modified or deleted since ``before``."""
    changes = describe_index_changes(before, snapshot_roots(roots))
    if changes:
        pytest.fail(holon_dir_failure(changes, scope), pytrace=False)
