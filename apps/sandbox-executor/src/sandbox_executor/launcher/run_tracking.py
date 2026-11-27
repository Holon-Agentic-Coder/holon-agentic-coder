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

FLOW_RUN_LABEL = "holon.flow.run"
# why: docker allows longer names, but a short one stays readable in docker ps.
MAX_CONTAINER_NAME_LENGTH = 63
CLIENT_STOP_GRACE_SECONDS = 5.0

_NAME_INVALID = re.compile(r"[^a-zA-Z0-9_.-]+")


@dataclass
class FlowRun:
    run_id: str
    slug: str
    owner_pid: int
    # why: a container stays listed until its client exits normally, because until then it may still be running.
    container_names: list[str] = field(default_factory=list)
    launched: int = 0
    # why: the reaper must know per client pid whether to signal its own process group or the pid alone.
    clients: dict[int, bool] = field(default_factory=dict)
    stale_run_ids: list[str] = field(default_factory=list)


# why: the reaper reads the registry from a signal handler that may interrupt this thread while it holds the lock.
_lock = threading.RLock()
# why: with no active run (holon review and other direct launches) the docker argv must stay unchanged.
_active: FlowRun | None = None


def start_run(slug: str, run_id: str, stale_run_ids: Sequence[str] = ()) -> FlowRun:
    global _active
    with _lock:
        if _active is None or _active.owner_pid != os.getpid():
            _active = FlowRun(run_id=run_id, slug=slug, owner_pid=os.getpid())
        _active.stale_run_ids.extend(stale for stale in stale_run_ids if stale not in _active.stale_run_ids)
        return _active


def active_run() -> FlowRun | None:
    # why: a forked child inherits the registry but must not act on the parent's run.
    run = _active
    return run if run is not None and run.owner_pid == os.getpid() else None


def end_run() -> None:
    global _active
    with _lock:
        _active = None


def container_identity_args(role: str) -> list[str]:
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
    run = active_run()
    if run is None:
        return
    with _lock:
        run.container_names[:] = [name for name in run.container_names if name not in names]


def take_stale_run_ids() -> list[str]:
    run = active_run()
    if run is None:
        return []
    with _lock:
        # why: the ids are handed out once so a later sweep does not repeat the earlier one.
        stale, run.stale_run_ids[:] = list(run.stale_run_ids), []
    return stale


def run_tracked(cmd: Sequence[str], env: Mapping[str, str] | None = None) -> int:
    if active_run() is None:
        return subprocess.run(list(cmd), env=env).returncode
    # why: a background group reading a TTY gets SIGTTIN; with no TTY a group lets the reaper stop all children.
    own_group = not _stdin_is_tty()
    proc = subprocess.Popen(list(cmd), env=env, process_group=0 if own_group else None)
    _set_client(proc.pid, own_group)
    try:
        code = proc.wait()
    except BaseException:
        # why: a signal or KeyboardInterrupt while waiting must not leave the client running.
        _stop_client(proc, own_group)
        raise
    finally:
        _set_client(proc.pid, None)
    # why: the client exited by itself, so its --rm container is already gone.
    name = _container_name(cmd)
    forget_containers([name] if name else [])
    return code
