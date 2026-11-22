"""Abandoned git lock removal and filesystem ref reads for repairing a damaged workspace repository."""

import contextlib
import os
import time

_LOCK_AGE_ENV = "HOLON_GIT_LOCK_AGE_SECONDS"
_DEFAULT_LOCK_AGE_SECONDS = 60.0
#: Lock paths that may belong to another live git process and are therefore never removed here.
_PROTECTED_LOCK_NAMES = ("config.lock", "packed-refs.lock", "shallow.lock", "HEAD.lock")


def _lock_age_limit() -> float:
    """Returns the minimum age in seconds after which a git lock file counts as abandoned."""
    raw = os.getenv(_LOCK_AGE_ENV)
    try:
        limit = float(raw) if raw else _DEFAULT_LOCK_AGE_SECONDS
    except (TypeError, ValueError):
        limit = _DEFAULT_LOCK_AGE_SECONDS
    return limit if limit > 0 else _DEFAULT_LOCK_AGE_SECONDS


def _remove_abandoned_locks(repo_dir: str) -> tuple[list[str], list[str]]:
    """Deletes only lock files that can be shown to be abandoned.

    The candidate set is deliberately narrow: ``.git/index.lock`` plus ``*.lock`` under ``.git/refs``
    and ``.git/logs``. Locks whose name is known to be written by long-lived plumbing
    (``config.lock``, ``packed-refs.lock``, ``shallow.lock``, ``HEAD.lock``) are never touched, and no
    lock is removed while it is younger than ``HOLON_GIT_LOCK_AGE_SECONDS``: a young lock can belong
    to a git process that is still running in this same workspace, and deleting it corrupts that
    transaction.

    Returns:
        A tuple of (removed_paths, still_held_paths).
    """
    limit = _lock_age_limit()
    git_dir = os.path.join(repo_dir, ".git")
    removed: list[str] = []
    still_held: list[str] = []
    if not os.path.isdir(git_dir):
        return removed, still_held

    candidates = [os.path.join(git_dir, "index.lock")]
    for subtree in ("refs", "logs"):
        root_dir = os.path.join(git_dir, subtree)
        for root, _, files in os.walk(root_dir):
            candidates.extend(os.path.join(root, name) for name in files if name.endswith(".lock"))

    for lock in candidates:
        if not os.path.isfile(lock) or os.path.basename(lock) in _PROTECTED_LOCK_NAMES:
            continue
        try:
            age = time.time() - os.path.getmtime(lock)
        except OSError:
            continue
        if age < limit:
            still_held.append(lock)
            continue
        with contextlib.suppress(Exception):
            os.remove(lock)
            removed.append(lock)
    return removed, still_held


def _resolve_local_ref(git_dir: str, refname: str) -> str | None:
    """Reads a local ref straight from the filesystem.

    Used while ``.git/HEAD`` itself is damaged: git refuses to run at all in that state
    ('fatal: not a git repository'), so the tip has to be found without shelling out.
    """
    loose = os.path.join(git_dir, "refs", "heads", *refname.split("/"))
    with contextlib.suppress(Exception):
        with open(loose) as rf:
            sha = rf.read().strip()
        if len(sha) >= 40:
            return sha
    packed = os.path.join(git_dir, "packed-refs")
    with contextlib.suppress(Exception), open(packed) as pf:
        for line in pf:
            line = line.strip()
            if not line or line.startswith(("#", "^")):
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1] == f"refs/heads/{refname}":
                return parts[0]
    return None
