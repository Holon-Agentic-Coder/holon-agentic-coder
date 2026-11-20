"""The per-repository Stage 3 admission lock: one exclusive ``flock`` on ``<git-common-dir>/holon-admission.lock``.

Every worktree of a repository shares the common git directory, so admission checks of all of them on one host are
serialised. The lock is held from discovery until the admission state is written, so two overlapping flows cannot both
be admitted in the same window. The wait is bounded (fail closed with :class:`AdmissionLockError`), and the kernel
releases the lock when a holder dies, so a crashed flow never wedges admission. Cross-host flows are not serialised.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager

from sandbox_executor.flow.in_flight import GitRunner
from sandbox_executor.git import run_git

#: Name of the lock file in the repository's common git directory (never a tracked path).
ADMISSION_LOCK_NAME = "holon-admission.lock"
#: Longest wait for another flow's admission check (discovery makes at most ~50 ``gh pr diff`` calls).
ADMISSION_LOCK_TIMEOUT_SECONDS = 120.0
ADMISSION_LOCK_POLL_SECONDS = 0.1


class AdmissionLockError(RuntimeError):
    """The admission lock could not be located or taken in time; admission refuses fail-closed with this reason."""


def _host_git(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return run_git(args, cwd=cwd, check=False)


def admission_lock_path(repo_dir: str, run_git: GitRunner | None = None) -> str:
    """``<git rev-parse --git-common-dir>/holon-admission.lock``, resolved against ``repo_dir``."""
    result = (run_git or _host_git)(["rev-parse", "--git-common-dir"], repo_dir)
    common_dir = (result.stdout or "").strip()
    if result.returncode != 0 or not common_dir:
        detail = (result.stderr or "").strip()[:300] or "no output"
        raise AdmissionLockError(f"'git rev-parse --git-common-dir' failed: {detail}")
    return os.path.join(os.path.abspath(os.path.join(repo_dir, common_dir)), ADMISSION_LOCK_NAME)


def _acquire(descriptor: int, path: str, timeout_seconds: float, poll_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise AdmissionLockError(
                    f"another flow held the admission lock {path} for more than {timeout_seconds:g} s"
                ) from None
            time.sleep(poll_seconds)


@contextmanager
def admission_lock(
    repo_dir: str,
    run_git: GitRunner | None = None,
    timeout_seconds: float = ADMISSION_LOCK_TIMEOUT_SECONDS,
    poll_seconds: float = ADMISSION_LOCK_POLL_SECONDS,
) -> Iterator[str]:
    """Hold the repository's admission lock for the ``with`` block; yields the lock file path."""
    path = admission_lock_path(repo_dir, run_git)
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        raise AdmissionLockError(f"the admission lock {path} could not be opened: {exc}") from exc
    try:
        _acquire(descriptor, path, timeout_seconds, poll_seconds)
        try:
            yield path
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
