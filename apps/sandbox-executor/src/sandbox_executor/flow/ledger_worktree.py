"""The ledger step's temporary worktree: created, owned, removed and swept in one place.

The step appends a ledger row in a detached worktree at ``<temp>/holon-flow-ledger-<random>/worktree``. For as long as
that worktree is registered, the step holds an exclusive ``flock`` on ``owner.lock`` beside it (never inside it, so the
lock file is never staged). The kernel releases the lock when the process dies, however it dies, which is what makes
"no live process owns this worktree" decidable for the next run.

Leaving the context removes the worktree, deletes the temp directory, repeats a removal that did not succeed and only
then prunes, so a removal that failed or timed out still ends unregistered. Entering it first clears what a killed run
left behind: registrations whose directory is gone are pruned, and each registered ``holon-flow-ledger-*/worktree``
whose owner lock can be taken (or that has none, as the earlier code wrote none) is removed.

A ``git worktree add`` that is killed or times out leaves its registration ``locked`` with the reason ``initializing``,
which a plain ``remove --force`` refuses and ``prune`` skips. Every removal here therefore passes ``--force`` twice,
and the sweep treats that one lock reason as no lock at all, with or without the directory. Git translates the reason
it writes, so the step's own ``git worktree add`` runs in the ``C`` message locale and the reason is the same under
any locale. A worktree whose owner lock a live step holds, that git marks ``locked`` for any other reason, that is not
detached, or that is outside a ``holon-flow-ledger-*`` directory is never touched. Neither the sweep nor the cleanup
raises. The git runner is always a parameter, so the caller's patched binding is the one used.
"""

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

#: Prefix of the temp directory that holds one ledger worktree.
LEDGER_WORKTREE_PREFIX = "holon-flow-ledger-"
#: Name of the worktree directory inside that temp directory.
LEDGER_WORKTREE_LEAF = "worktree"
#: Name of the owner's lock file inside that temp directory, beside the worktree.
OWNER_LOCK_NAME = "owner.lock"
#: What a git call or a filesystem call may raise during the sweep or the cleanup; logged, never propagated.
TOLERATED_CLEANUP_ERRORS = (RuntimeError, TimeoutError, OSError)
#: How ``git worktree list --porcelain`` reports the lock git itself holds while ``git worktree add`` checks out. Git
#: writes that reason in its message locale, so this is what an add run in the ``C`` locale leaves, and only that.
GIT_INITIALIZING_LOCK = "locked initializing"


def _quiet_git(git: GitRunnerCallable, args: list[str], repo_dir: str) -> subprocess.CompletedProcess[str] | None:
    """Run a best-effort git command in ``repo_dir``; a tolerated error is logged and answered with None."""
    try:
        return git(args, cwd=repo_dir, check=False)
    except TOLERATED_CLEANUP_ERRORS as exc:
        logger.warning("Ledger worktree: 'git %s' failed: %s", " ".join(args[:2]), redact_agent_secrets(str(exc)))
        return None


def _claim_owner_lock(tmp_root: str, create: bool) -> int | None:
    """Take the exclusive owner lock of a ledger temp directory without waiting.

    Returns:
        int | None: The open descriptor holding the lock (the caller closes it, which releases the lock), or None when
        the lock file cannot be opened or another live handle holds the lock.
    """
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
    """True when ``worktree`` is named ``<...>/holon-flow-ledger-*/worktree``."""
    tmp_root = os.path.dirname(worktree)
    return os.path.basename(worktree) == LEDGER_WORKTREE_LEAF and os.path.basename(tmp_root).startswith(
        LEDGER_WORKTREE_PREFIX
    )


def _discard_worktree(repo_dir: str, worktree: str, git: GitRunnerCallable) -> None:
    """Unregister a ledger worktree and delete its temp directory; anything that is not one is left on disk.

    The second ``--force`` overrides a lock, so the caller must already know the worktree is its own or a sweep
    candidate: an unfinished ``git worktree add`` leaves a lock that one ``--force`` does not remove. An add killed
    before git wrote the registration's ``HEAD`` leaves one that git refuses to remove while its directory exists and
    that ``prune`` skips as locked, so a removal that did not succeed is repeated once the directory is deleted.
    """
    removal = ["worktree", "remove", "--force", "--force", worktree]
    removed = _quiet_git(git, removal, repo_dir)
    tmp_root = os.path.dirname(worktree)
    if _is_ledger_worktree_path(worktree) and not os.path.islink(tmp_root):
        shutil.rmtree(tmp_root, ignore_errors=True)
        if removed is None or removed.returncode != 0:
            _quiet_git(git, removal, repo_dir)


def _leftover_worktrees(porcelain: str, repo_dir: str) -> list[str]:
    """Parse ``git worktree list --porcelain`` into the registered ledger worktrees a sweep may consider.

    The main worktree (the first block), ``repo_dir`` itself, a worktree on a branch and one somebody locked are never
    candidates; one marked ``prunable`` has no directory and is left to ``git worktree prune``. The lock of an
    unfinished ``git worktree add`` is nobody's: git never prunes such a registration, so it stays a candidate.
    """
    leftovers: list[str] = []
    for block in porcelain.split("\n\n")[1:]:
        lines = block.splitlines()
        path = next((line.removeprefix("worktree ") for line in lines if line.startswith("worktree ")), "")
        attributes = {line.split(" ", 1)[0] for line in lines}
        if "locked" in attributes and GIT_INITIALIZING_LOCK not in lines:
            continue
        if not _is_ledger_worktree_path(path) or "detached" not in attributes or "prunable" in attributes:
            continue
        if os.path.realpath(path) != os.path.realpath(repo_dir):
            leftovers.append(path)
    return leftovers


def _discard_if_unowned(repo_dir: str, worktree: str, git: GitRunnerCallable) -> bool:
    """Discard a leftover ledger worktree unless a live process holds its owner lock; True when it was discarded.

    The lock file decides, not the worktree directory: a step takes the lock before ``git worktree add`` creates the
    directory, and an add that was killed can leave a registration with no directory at all.
    """
    tmp_root = os.path.dirname(worktree)
    if not os.path.lexists(os.path.join(tmp_root, OWNER_LOCK_NAME)):
        # Left by the earlier code, which wrote no lock file, or its temp directory is gone: nothing can still own it.
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
    """Clear the ledger worktree registrations an earlier killed run left in ``repo_dir``'s repository."""
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
    """Yield the path of a detached worktree on ``ref``, owned by this process and gone when the block ends.

    Stale ledger worktrees are cleared first. A failed ``git worktree add`` raises as the runner raises it; whatever
    the block does, the worktree is unregistered and its temp directory deleted before the error propagates. Should
    that removal fail after an add that never finished, the locked registration is left for the next step's sweep.
    """
    _clear_stale_ledger_worktrees(repo_dir, git)
    tmp_root = tempfile.mkdtemp(prefix=LEDGER_WORKTREE_PREFIX)
    worktree = os.path.join(tmp_root, LEDGER_WORKTREE_LEAF)
    owner_lock: int | None = None
    try:
        owner_lock = _claim_owner_lock(tmp_root, create=True)
        if owner_lock is None:
            logger.warning("Ledger worktree: could not take the owner lock in %s; continuing without it.", tmp_root)
        # In the C message locale, so an add killed midway leaves the lock reason the next step's sweep recognises.
        git(["worktree", "add", "--detach", worktree, ref], cwd=repo_dir, env={**os.environ, "LC_ALL": "C"})
        yield worktree
    finally:
        _discard_worktree(repo_dir, worktree, git)
        # After the delete: a removal that failed leaves a registration only a prune of the missing directory clears.
        _quiet_git(git, ["worktree", "prune"], repo_dir)
        if owner_lock is not None:
            with contextlib.suppress(OSError):
                os.close(owner_lock)
