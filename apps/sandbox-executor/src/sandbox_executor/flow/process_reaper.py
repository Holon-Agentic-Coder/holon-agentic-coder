"""Ends what a ``holon flow`` run started when it exits, halts, fails or is signalled.

:func:`start_reaper_run` (called once the run lock is held) makes the run active in ``launcher.run_tracking``, so its
role containers are named, labelled and tracked, and turns SIGTERM and SIGINT into a reap followed by
``SystemExit(143)`` or ``KeyboardInterrupt``. Reaping first, in the signal handler, matters: stage worker threads block
interpreter shutdown until their docker clients exit, so an ``atexit`` reap alone would never run.
:func:`reap_flow_run` (also run at exit, before the run lock is released) stops the run's containers by name and run
label, sends SIGTERM and then SIGKILL to the tracked docker clients' process groups, and does the same to any other
process still descended from the flow process. It never signals pid 0 or 1, the flow process itself or the flow's own
process group, and does nothing in a forked child.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import FrameType
from typing import Any

from sandbox_executor import turns
from sandbox_executor.launcher import run_tracking
from sandbox_executor.launcher.run_tracking import FLOW_RUN_LABEL

logger = logging.getLogger(__name__)

#: Wait after SIGTERM before the processes still alive get SIGKILL.
REAP_GRACE_SECONDS = 5.0
#: Seconds ``docker stop`` gives a container before docker kills it.
CONTAINER_STOP_SECONDS = 10
DOCKER_TIMEOUT_SECONDS = 60
_POLL_SECONDS = 0.05
_HANDLED_SIGNALS = (signal.SIGTERM, signal.SIGINT)

DockerRunner = Callable[[list[str]], "subprocess.CompletedProcess[str] | None"]


def _host_docker(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    """``docker <args>``; None when docker is not installed or the call fails to run."""
    if not shutil.which("docker"):
        return None
    try:
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=DOCKER_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("docker %s failed while reaping the flow run: %s", args[0], exc)
        return None


@dataclass(frozen=True)
class ReapSeams:
    """How the reaper reaches docker, signals processes, reads the process table and waits."""

    docker: DockerRunner = _host_docker
    kill: Callable[[int, int], None] = os.kill
    killpg: Callable[[int, int], None] = os.killpg
    process_table: Callable[[], turns.ProcessTable | None] = turns.process_table
    grace_seconds: float = REAP_GRACE_SECONDS


_previous_handlers: dict[int, Any] = {}
_reaping = threading.Lock()


def start_reaper_run(slug: str, run_id: str, stale_run_ids: Sequence[str] = (), seams: ReapSeams | None = None) -> None:
    """Make ``run_id`` this process's flow run, install the signal handlers and sweep a dead holder's containers."""
    run_tracking.start_run(slug, run_id, stale_run_ids)
    if threading.current_thread() is threading.main_thread():
        for signum in _HANDLED_SIGNALS:
            if signum not in _previous_handlers:
                _previous_handlers[signum] = signal.signal(signum, _on_signal)
    swept = _sweep_labels(run_tracking.take_stale_run_ids(), seams or ReapSeams())
    if swept:
        logger.info("Removed %d container(s) left by an earlier holder of the flow run lock of '%s'.", swept, slug)


def reset_reaper() -> None:
    """End the active run without reaping and restore the signal handlers (tests, and a run that ends in-process)."""
    run_tracking.end_run()
    if threading.current_thread() is threading.main_thread():
        for signum, previous in _previous_handlers.items():
            signal.signal(signum, previous if previous is not None else signal.SIG_DFL)
        _previous_handlers.clear()


def _on_signal(signum: int, frame: FrameType | None) -> None:
    del frame
    reap_flow_run()
    if signum == signal.SIGINT:
        raise KeyboardInterrupt
    raise SystemExit(128 + signum)


def reap_flow_run(seams: ReapSeams | None = None) -> None:
    """Stop every container and process the active run started; a no-op without one, or while already reaping."""
    run = run_tracking.active_run()
    if run is None or not _reaping.acquire(blocking=False):
        return
    seams = seams or ReapSeams()
    try:
        containers = _stop_containers(run, seams)
        processes = _end_processes(dict(run.clients), run.owner_pid, seams)
        processes += _end_processes(dict.fromkeys(_descendants(run.owner_pid, seams), False), run.owner_pid, seams)
    finally:
        _reaping.release()
    if containers or processes:
        logger.info("Reaped %d process(es) and %d container(s) for flow run %s", processes, containers, run.run_id)


def _stop_containers(run: run_tracking.FlowRun, seams: ReapSeams) -> int:
    names = list(run.container_names)
    stopped = 0
    if names:
        result = seams.docker(["stop", "--time", str(CONTAINER_STOP_SECONDS), *names])
        stopped = len(names) if result is not None and result.returncode == 0 else 0
        run_tracking.forget_containers(names)
    own = [run.run_id] if run.launched else []
    return stopped + _sweep_labels([*own, *run_tracking.take_stale_run_ids()], seams)


def _sweep_labels(run_ids: Sequence[str], seams: ReapSeams) -> int:
    """Force-remove every container labelled with one of ``run_ids`` (an exact label value match)."""
    removed = 0
    for run_id in run_ids:
        listed = seams.docker(["ps", "-aq", "--filter", f"label={FLOW_RUN_LABEL}={run_id}"])
        container_ids = (listed.stdout or "").split() if listed is not None and listed.returncode == 0 else []
        if container_ids:
            seams.docker(["rm", "-f", *container_ids])
            removed += len(container_ids)
    return removed


def _descendants(owner_pid: int, seams: ReapSeams) -> list[int]:
    table = seams.process_table() or {}
    children: dict[int, list[int]] = {}
    for pid, (ppid, *_rest) in table.items():
        children.setdefault(ppid, []).append(pid)
    found: list[int] = []
    stack = list(children.get(owner_pid, ()))
    while stack:
        pid = stack.pop()
        if pid not in found and pid != owner_pid:
            found.append(pid)
            stack.extend(children.get(pid, ()))
    return found


def _signal(pid: int, own_group: bool, signum: int, seams: ReapSeams) -> bool:
    """Send ``signum`` to ``pid`` (its group when it leads one); False once nothing is left to receive it."""
    try:
        (seams.killpg if own_group else seams.kill)(pid, signum)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _alive(pid: int, own_group: bool, seams: ReapSeams) -> bool:
    with contextlib.suppress(ChildProcessError, OSError):
        os.waitpid(pid, os.WNOHANG)
    return _signal(pid, own_group, 0, seams)


def _end_processes(targets: dict[int, bool], owner_pid: int, seams: ReapSeams) -> int:
    """SIGTERM each target, wait out the grace period, then SIGKILL whatever is still alive; returns how many."""
    owner_group = os.getpgid(owner_pid)
    targets = {pid: own for pid, own in targets.items() if pid > 1 and pid not in (owner_pid, owner_group)}
    signalled = {pid: own for pid, own in targets.items() if _signal(pid, own, signal.SIGTERM, seams)}
    deadline = time.monotonic() + seams.grace_seconds
    remaining = dict(signalled)
    while remaining and time.monotonic() < deadline:
        time.sleep(_POLL_SECONDS)
        remaining = {pid: own for pid, own in remaining.items() if _alive(pid, own, seams)}
    for pid, own in remaining.items():
        _signal(pid, own, signal.SIGKILL, seams)
    return len(signalled)
