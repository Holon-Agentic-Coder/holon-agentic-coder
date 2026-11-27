from __future__ import annotations

import atexit
import contextlib
import fcntl
import logging
import os
import re
import sys
import uuid
from dataclasses import dataclass

from sandbox_executor.flow import process_reaper
from sandbox_executor.flow.run_lock_record import RunLockHolder
from sandbox_executor.git import run_git

logger = logging.getLogger(__name__)

# why: every worktree shares the common git directory, so one lock file serialises a slug per repository and host
RUN_LOCK_PREFIX = "holon-flow-run-"
_SLUG_INVALID = re.compile(r"[^A-Za-z0-9_-]+")


class FlowRunLockHeld(RuntimeError):
    def __init__(self, slug: str, path: str, holder: RunLockHolder | None) -> None:
        who = holder.describe() if holder is not None else "an unidentified process"
        super().__init__(
            f"another holon flow run holds intent '{slug}': {who} (lock file {path}). Wait for it to finish or stop "
            "it; a lock whose holder has died is taken over automatically."
        )
        self.path = path
        self.holder = holder


@dataclass(frozen=True)
class HeldRunLock:
    path: str
    holder: RunLockHolder
    stale: RunLockHolder | None


_held: dict[str, tuple[int, HeldRunLock]] = {}
_exit_hook_registered = False


def _nearest_existing_dir(path: str) -> str:
    current = os.path.abspath(path)
    while not os.path.isdir(current) and os.path.dirname(current) != current:
        current = os.path.dirname(current)
    return current


def run_lock_path(target_worktree: str, slug: str) -> str | None:
    probe = _nearest_existing_dir(target_worktree)
    try:
        result = run_git(["rev-parse", "--git-common-dir"], cwd=probe, check=False)
    except (OSError, TimeoutError):
        return None
    common_dir = (result.stdout or "").strip()
    if result.returncode != 0 or not common_dir:
        return None
    name = _SLUG_INVALID.sub("-", slug).strip("-") or "task"
    return os.path.join(os.path.realpath(os.path.join(probe, common_dir)), f"{RUN_LOCK_PREFIX}{name}.lock")


def _read_holder(descriptor: int) -> RunLockHolder | None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks = []
    while chunk := os.read(descriptor, 4096):
        chunks.append(chunk)
    return RunLockHolder.from_text(b"".join(chunks).decode("utf-8", errors="replace"))


def _write_holder(descriptor: int, holder: RunLockHolder) -> None:
    os.ftruncate(descriptor, 0)
    os.pwrite(descriptor, holder.to_json().encode("utf-8"), 0)
    os.fsync(descriptor)


def _same_file(descriptor: int, path: str) -> bool:
    try:
        return os.stat(path).st_ino == os.fstat(descriptor).st_ino
    except FileNotFoundError:
        return False


def _try_lock(descriptor: int, path: str) -> bool:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    # why: a contender may have unlinked and recreated the path between our open and our flock
    if _same_file(descriptor, path):
        return True
    fcntl.flock(descriptor, fcntl.LOCK_UN)
    return False


def _log_takeover(path: str, stale: RunLockHolder, why: str) -> None:
    message = "Taking over flow run lock %s from pid %s on %s (started %s): %s"
    logger.warning(message, path, stale.pid, stale.host, stale.started_at, why)


def _locked(descriptor: int, path: str, holder: RunLockHolder, stale: RunLockHolder | None) -> HeldRunLock:
    # why: a clean release empties the file, so a record under a free lock means its holder died without releasing
    unreleased = _read_holder(descriptor)
    if unreleased is not None:
        _log_takeover(path, unreleased, "the holder exited without releasing it")
        stale = unreleased
    _write_holder(descriptor, holder)
    held = HeldRunLock(path=path, holder=holder, stale=stale)
    _held[path] = (descriptor, held)
    return held


def acquire_flow_run_lock(target_worktree: str, slug: str, run_id: str) -> HeldRunLock | None:
    path = run_lock_path(target_worktree, slug)
    if path is None:
        logger.warning("No git directory for '%s'; the flow run lock of '%s' is not taken.", target_worktree, slug)
        return None
    # why: re-entrant, since a second flock on a new descriptor would block against our own lock
    if path in _held:
        return _held[path][1]
    holder = RunLockHolder.current(slug, target_worktree, run_id)
    held = _attempt(slug, path, holder, None)
    if isinstance(held, HeldRunLock):
        return held
    # why: contenders for a dead holder's lock take it over one at a time under a guard lock and re-check the file
    # why: there, so none can unlink a lock file another has just created and locked.
    guard = os.open(f"{path}.takeover", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(guard, fcntl.LOCK_EX)
        stale = _attempt(slug, path, holder, None)
        if isinstance(stale, HeldRunLock):
            return stale
        _log_takeover(path, stale, "its pid is dead but a process it started still holds the lock")
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        retaken = _attempt(slug, path, holder, stale)
        if isinstance(retaken, HeldRunLock):
            return retaken
        raise FlowRunLockHeld(slug, path, retaken)
    finally:
        os.close(guard)


def _attempt(slug: str, path: str, holder: RunLockHolder, stale: RunLockHolder | None) -> HeldRunLock | RunLockHolder:
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    if _try_lock(descriptor, path):
        return _locked(descriptor, path, holder, stale)
    # why: a fork that inherited the flock keeps it held after its recorded holder dies; only a dead local holder is
    # why: taken over, never a live, remote or unidentified one
    current = _read_holder(descriptor)
    dead = current is not None and current.is_dead_here() and _same_file(descriptor, path)
    os.close(descriptor)
    if current is None or not dead:
        raise FlowRunLockHeld(slug, path, current)
    return current


def held_run_lock_paths() -> list[str]:
    return list(_held)


def release_flow_run_lock(path: str | None = None) -> None:
    for lock_path in [path] if path is not None else list(_held):
        entry = _held.pop(lock_path, None)
        if entry is None:
            continue
        descriptor = entry[0]
        with contextlib.suppress(OSError):
            os.ftruncate(descriptor, 0)
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _reap_then_release() -> None:
    try:
        process_reaper.reap_flow_run()
    finally:
        release_flow_run_lock()


def hold_flow_run_lock(target_worktree: str, slug: str) -> bool:
    global _exit_hook_registered
    try:
        held = acquire_flow_run_lock(target_worktree, slug, uuid.uuid4().hex[:12])
    except FlowRunLockHeld as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
        return False
    if held is None:
        return True
    stale_run_ids = [held.stale.run_id] if held.stale is not None and held.stale.run_id else []
    process_reaper.start_reaper_run(slug, held.holder.run_id, stale_run_ids)
    if not _exit_hook_registered:
        atexit.register(_reap_then_release)
        _exit_hook_registered = True
    return True
