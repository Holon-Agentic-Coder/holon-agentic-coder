from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator

from sandbox_executor.git import GitRunnerCallable
from sandbox_executor.redaction import redact_agent_secrets

logger = logging.getLogger(__name__)

LEDGER_WORKTREE_PREFIX = "holon-flow-ledger-"
LEDGER_WORKTREE_LEAF = "worktree"
# why: the owner lock lives beside the worktree, never inside it, so the lock file is never staged.
OWNER_LOCK_NAME = "owner.lock"
TOLERATED_CLEANUP_ERRORS = (RuntimeError, TimeoutError, OSError)
# why: git writes the lock reason in its message locale, so only what an add run in the C locale leaves matches.
GIT_INITIALIZING_LOCK = "locked initializing"


def _quiet_git(git: GitRunnerCallable, args: list[str], repo_dir: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return git(args, cwd=repo_dir, check=False)
    except TOLERATED_CLEANUP_ERRORS as exc:
        logger.warning("Ledger worktree: 'git %s' failed: %s", " ".join(args[:2]), redact_agent_secrets(str(exc)))
        return None


def _claim_owner_lock(tmp_root: str, create: bool) -> int | None:
    flags = os.O_RDWR | (os.O_CREAT if create else 0)
    try:
        descriptor = os.open(os.path.join(tmp_root, OWNER_LOCK_NAME), flags, 0o644)
    except OSError:
        return None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(descriptor)
        return None
    return descriptor


def _is_ledger_worktree_path(worktree: str) -> bool:
    tmp_root = os.path.dirname(worktree)
    return os.path.basename(worktree) == LEDGER_WORKTREE_LEAF and os.path.basename(tmp_root).startswith(
        LEDGER_WORKTREE_PREFIX
    )


def _discard_worktree(repo_dir: str, worktree: str, git: GitRunnerCallable) -> None:
    # why: an unfinished worktree add leaves a lock that a single --force does not override.
    removal = ["worktree", "remove", "--force", "--force", worktree]
    removed = _quiet_git(git, removal, repo_dir)
    tmp_root = os.path.dirname(worktree)
    if _is_ledger_worktree_path(worktree) and not os.path.islink(tmp_root):
        shutil.rmtree(tmp_root, ignore_errors=True)
        # why: git refuses to remove a HEAD-less registration while its directory exists, and prune skips it as locked.
        if removed is None or removed.returncode != 0:
            _quiet_git(git, removal, repo_dir)


def _leftover_worktrees(porcelain: str, repo_dir: str) -> list[str]:
    leftovers: list[str] = []
    for block in porcelain.split("\n\n")[1:]:
        lines = block.splitlines()
        path = next((line.removeprefix("worktree ") for line in lines if line.startswith("worktree ")), "")
        attributes = {line.split(" ", 1)[0] for line in lines}
        # why: git never prunes the lock of an unfinished worktree add, so that lock is nobody's and stays a candidate.
        if "locked" in attributes and GIT_INITIALIZING_LOCK not in lines:
            continue
        if not _is_ledger_worktree_path(path) or "detached" not in attributes or "prunable" in attributes:
            continue
        if os.path.realpath(path) != os.path.realpath(repo_dir):
            leftovers.append(path)
    return leftovers


def _discard_if_unowned(repo_dir: str, worktree: str, git: GitRunnerCallable) -> bool:
    tmp_root = os.path.dirname(worktree)
    # why: the lock file decides, not the directory: the lock is taken before the add creates the directory.
    if not os.path.lexists(os.path.join(tmp_root, OWNER_LOCK_NAME)):
        # why: the earlier code wrote no lock file, or the temp directory is gone, so nothing can still own it.
        _discard_worktree(repo_dir, worktree, git)
        return True
    descriptor = _claim_owner_lock(tmp_root, create=False)
    if descriptor is None:
        return False
    try:
        _discard_worktree(repo_dir, worktree, git)
    finally:
        os.close(descriptor)
    return True


def _clear_stale_ledger_worktrees(repo_dir: str, git: GitRunnerCallable) -> None:
    _quiet_git(git, ["worktree", "prune"], repo_dir)
    listing = _quiet_git(git, ["worktree", "list", "--porcelain"], repo_dir)
    if listing is None or listing.returncode != 0 or not isinstance(listing.stdout, str):
        return
    discarded = [
        path for path in _leftover_worktrees(listing.stdout, repo_dir) if _discard_if_unowned(repo_dir, path, git)
    ]
    if discarded:
        logger.info("Removed %d stale ledger worktree(s): %s", len(discarded), ", ".join(discarded))
        _quiet_git(git, ["worktree", "prune"], repo_dir)


@contextlib.contextmanager
def _ledger_worktree(repo_dir: str, ref: str, git: GitRunnerCallable) -> Iterator[str]:
    _clear_stale_ledger_worktrees(repo_dir, git)
    tmp_root = tempfile.mkdtemp(prefix=LEDGER_WORKTREE_PREFIX)
    worktree = os.path.join(tmp_root, LEDGER_WORKTREE_LEAF)
    owner_lock: int | None = None
    try:
        owner_lock = _claim_owner_lock(tmp_root, create=True)
        if owner_lock is None:
            logger.warning("Ledger worktree: could not take the owner lock in %s; continuing without it.", tmp_root)
        # why: in the C message locale, an add killed midway leaves the lock reason the next sweep recognises.
        git(["worktree", "add", "--detach", worktree, ref], cwd=repo_dir, env={**os.environ, "LC_ALL": "C"})
        yield worktree
    finally:
        _discard_worktree(repo_dir, worktree, git)
        # why: after the delete, since a failed removal leaves a registration only pruning a gone directory clears.
        _quiet_git(git, ["worktree", "prune"], repo_dir)
        if owner_lock is not None:
            with contextlib.suppress(OSError):
                os.close(owner_lock)
