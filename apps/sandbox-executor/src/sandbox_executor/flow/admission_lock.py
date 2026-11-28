from __future__ import annotations

import fcntl
import glob
import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager

from sandbox_executor.flow.in_flight import GitRunner
from sandbox_executor.flow.run_lock import RUN_LOCK_PREFIX
from sandbox_executor.flow.run_lock_record import RunLockHolder
from sandbox_executor.git import run_git

# why: the common git dir is shared by every worktree and never holds a tracked path
ADMISSION_LOCK_NAME = "holon-admission.lock"
# why: a holder's discovery makes up to ~50 gh pr diff calls under the lock
ADMISSION_LOCK_TIMEOUT_SECONDS = 120.0
ADMISSION_LOCK_POLL_SECONDS = 0.1


class AdmissionLockError(RuntimeError):
    pass


def _host_git(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return run_git(args, cwd=cwd, check=False)


def admission_lock_path(repo_dir: str, run_git: GitRunner | None = None) -> str:
    result = (run_git or _host_git)(["rev-parse", "--git-common-dir"], repo_dir)
    common_dir = (result.stdout or "").strip()
    if result.returncode != 0 or not common_dir:
        detail = (result.stderr or "").strip()[:300] or "no output"
        raise AdmissionLockError(f"'git rev-parse --git-common-dir' failed: {detail}")
    return os.path.join(os.path.abspath(os.path.join(repo_dir, common_dir)), ADMISSION_LOCK_NAME)


def _acquire(descriptor: int, path: str, timeout_seconds: float, poll_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    # why: the kernel drops a dead holder's flock, so a crashed flow never wedges admission (one host only)
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


def _read_lock_text(descriptor: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while chunk := os.read(descriptor, 4096):
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


def _live_holder_worktree(path: str) -> str | None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return None
    try:
        try:
            # why: a non-blocking shared flock probes an exclusive runner lock without mutating or creating files
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = RunLockHolder.from_text(_read_lock_text(descriptor))
            # why: a forked child can inherit a dead runner's flock fd, so dead local pids count as parked
            if holder is None or holder.is_dead_here():
                return None
            return os.path.realpath(holder.worktree)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return None
    finally:
        os.close(descriptor)


def _live_runner_worktrees(repo_dir: str, run_git: GitRunner | None = None) -> frozenset[str]:
    common_dir = os.path.dirname(admission_lock_path(repo_dir, run_git))
    pattern = os.path.join(glob.escape(common_dir), f"{RUN_LOCK_PREFIX}*.lock")
    return frozenset(worktree for path in glob.glob(pattern) if (worktree := _live_holder_worktree(path)) is not None)
