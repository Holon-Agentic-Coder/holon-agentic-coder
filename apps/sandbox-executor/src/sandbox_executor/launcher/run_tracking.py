"""What the role-container launches of the active ``holon flow`` run started: docker clients and container names.

A flow run is active in a process between :func:`start_run` and :func:`end_run` (the flow's run lock starts it). While
one is active, each ``docker run`` gets a ``--name`` and a ``holon.flow.run=<run id>`` label, and its docker client is
tracked until it exits, so ``sandbox_executor.flow.process_reaper`` can stop whatever is left when the run ends. With no
active run (``holon review`` and other direct launches) the docker argv is unchanged and nothing is recorded.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

#: Docker label every container of a flow run carries; its value is the run id.
FLOW_RUN_LABEL = "holon.flow.run"
#: Longest container name this module generates (docker allows more; a short name stays readable in ``docker ps``).
MAX_CONTAINER_NAME_LENGTH = 63
#: Wait after SIGTERM before a docker client interrupted by an exception is killed.
CLIENT_STOP_GRACE_SECONDS = 5.0

_NAME_INVALID = re.compile(r"[^a-zA-Z0-9_.-]+")


@dataclass
class FlowRun:
    """The active flow run of this process and what its launches started."""

    run_id: str
    slug: str
    owner_pid: int
    #: Containers whose docker client has not exited normally yet, so they may still be running.
    container_names: list[str] = field(default_factory=list)
    launched: int = 0
    #: Live docker client pids, each mapped to whether it leads its own process group.
    clients: dict[int, bool] = field(default_factory=dict)
    stale_run_ids: list[str] = field(default_factory=list)


# Re-entrant: the reaper reads the registry from a signal handler that may interrupt this thread while it holds it.
_lock = threading.RLock()
_active: FlowRun | None = None


def start_run(slug: str, run_id: str, stale_run_ids: Sequence[str] = ()) -> FlowRun:
    """Make ``run_id`` this process's active flow run; a run already active stays, gaining any new stale ids."""
    global _active
    with _lock:
        if _active is None or _active.owner_pid != os.getpid():
            _active = FlowRun(run_id=run_id, slug=slug, owner_pid=os.getpid())
        _active.stale_run_ids.extend(stale for stale in stale_run_ids if stale not in _active.stale_run_ids)
        return _active


def active_run() -> FlowRun | None:
    """The flow run this process started, or None (none started, ended, or this is a forked child)."""
    run = _active
    return run if run is not None and run.owner_pid == os.getpid() else None


def end_run() -> None:
    """Forget the active run and everything it recorded."""
    global _active
    with _lock:
        _active = None


def container_identity_args(role: str) -> list[str]:
    """``--name`` and ``--label`` for the next container of the active run; ``[]`` when no flow run is active."""
    run = active_run()
    if run is None:
        return []
    with _lock:
        run.launched += 1
        suffix = _NAME_INVALID.sub("-", f"-{role}-{run.run_id}-{run.launched}")
        prefix = _NAME_INVALID.sub("-", f"holon-{run.slug}")[: MAX_CONTAINER_NAME_LENGTH - len(suffix)]
        name = f"{prefix}{suffix}"
        run.container_names.append(name)
    return ["--name", name, "--label", f"{FLOW_RUN_LABEL}={run.run_id}"]


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (OSError, ValueError):
        return False


def _container_name(cmd: Sequence[str]) -> str | None:
    if "--name" in cmd:
        position = list(cmd).index("--name") + 1
        return cmd[position] if position < len(cmd) else None
    return None


def _stop_client(proc: subprocess.Popen[bytes], own_group: bool) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if own_group:
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    try:
        proc.wait(timeout=CLIENT_STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _set_client(pid: int, own_group: bool | None) -> None:
    run = active_run()
    if run is None:
        return
    with _lock:
        if own_group is None:
            run.clients.pop(pid, None)
        else:
            run.clients[pid] = own_group


def forget_containers(names: Sequence[str]) -> None:
    """Stop listing ``names`` as possibly running (their client exited, or the reaper stopped them)."""
    run = active_run()
    if run is None:
        return
    with _lock:
        run.container_names[:] = [name for name in run.container_names if name not in names]


def take_stale_run_ids() -> list[str]:
    """The run ids of earlier holders whose containers are still to be swept; they are swept once."""
    run = active_run()
    if run is None:
        return []
    with _lock:
        stale, run.stale_run_ids[:] = list(run.stale_run_ids), []
    return stale


def run_tracked(cmd: Sequence[str], env: Mapping[str, str] | None = None) -> int:
    """Run a docker client to completion and return its exit code, tracking it while a flow run is active.

    With no active run this is the plain ``subprocess.run`` launch. During a flow run, without a TTY the client leads
    its own process group, so the reaper can stop it and everything it started as one group; with a TTY it stays in the
    foreground group (a background group reading the terminal would get SIGTTIN). An exception while waiting (a signal,
    ``KeyboardInterrupt``) stops the client before it propagates.
    """
    if active_run() is None:
        return subprocess.run(list(cmd), env=env).returncode
    own_group = not _stdin_is_tty()
    proc = subprocess.Popen(list(cmd), env=env, process_group=0 if own_group else None)
    _set_client(proc.pid, own_group)
    try:
        code = proc.wait()
    except BaseException:
        _stop_client(proc, own_group)
        raise
    finally:
        _set_client(proc.pid, None)
    # The client exited on its own, so its ``--rm`` container is gone with it.
    name = _container_name(cmd)
    forget_containers([name] if name else [])
    return code
