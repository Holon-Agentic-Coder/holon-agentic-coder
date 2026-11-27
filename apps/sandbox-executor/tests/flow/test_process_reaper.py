from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sandbox_executor.flow import process_reaper, run_lock
from sandbox_executor.flow.process_reaper import ReapSeams
from sandbox_executor.launcher import run_tracking

from tests.flow.conftest import child_python_env, wait_for_line
from tests.flow.helpers import _git

_FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$FAKE_DOCKER_LOG"
case "$1" in
  run) exec sleep 300 ;;
  ps) echo c0ffee ;;
esac
exit 0
"""

_RUN_LOCK_HOLDER_WITH_TRACKED_AND_UNTRACKED_CHILDREN = """
import json, subprocess, sys, threading, time
from sandbox_executor.flow import run_lock
from sandbox_executor.launcher import run_tracking
run_lock.hold_flow_run_lock(sys.argv[1], "alpha")
cmd = ["docker", "run", *run_tracking.container_identity_args("executor"), "img"]
# why: the docker thread is non-daemon in the signal cases, so interpreter shutdown would wait for it
threading.Thread(target=run_tracking.run_tracked, args=(cmd,), daemon=sys.argv[2] == "exit").start()
sleeper = subprocess.Popen(["sleep", "300"])
deadline = time.monotonic() + 10
while not run_tracking.active_run().clients and time.monotonic() < deadline:
    time.sleep(0.02)
run = run_tracking.active_run()
print(json.dumps({"client": next(iter(run.clients)), "sleep": sleeper.pid, "run_id": run.run_id}), flush=True)
sys.stdin.readline()
sys.exit(1)
"""


def _pid_is_gone_or_zombie(pid: int) -> bool:
    # why: a zombie counts as gone; it is an orphan whose reaper has not collected it yet
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] == "Z"
    except FileNotFoundError:
        return True
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _wait_gone(pids: list[int], timeout: float = 15.0) -> list[int]:
    deadline = time.monotonic() + timeout
    alive = list(pids)
    while alive and time.monotonic() < deadline:
        alive = [pid for pid in alive if not _pid_is_gone_or_zombie(pid)]
        time.sleep(0.05)
    return alive


@pytest.fixture
def fake_docker_logging_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(_FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    log = tmp_path / "docker.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    return log


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    return path


def _run_victim(repo: Path, mode: str, halt) -> tuple[int, dict[str, object]]:
    victim = subprocess.Popen(
        [sys.executable, "-c", _RUN_LOCK_HOLDER_WITH_TRACKED_AND_UNTRACKED_CHILDREN, str(repo), mode],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=child_python_env(),
    )
    pids: dict[str, object] = {}
    try:
        pids = json.loads(wait_for_line(victim))
        halt(victim)
        return victim.wait(timeout=30), pids
    finally:
        for pid in (victim.pid, pids.get("client"), pids.get("sleep")):
            if isinstance(pid, int) and not _pid_is_gone_or_zombie(pid):
                os.kill(pid, signal.SIGKILL)
        victim.wait(timeout=10)


def _send(signum: int):
    return lambda victim: victim.send_signal(signum)


def _finish(victim: subprocess.Popen[str]) -> None:
    assert victim.stdin is not None
    victim.stdin.write("halt\n")
    victim.stdin.flush()


@pytest.mark.parametrize(
    ("mode", "halt", "codes"),
    [
        ("signal", _send(signal.SIGTERM), {143}),
        ("signal", _send(signal.SIGINT), {-signal.SIGINT, 130}),
        ("exit", _finish, {1}),
    ],
    ids=["sigterm", "sigint", "halt"],
)
def test_a_halted_run_leaves_no_child_and_stops_its_container(
    repo, fake_docker_logging_argv, mode, halt, codes
) -> None:
    code, pids = _run_victim(repo, mode, halt)

    assert code in codes
    assert _wait_gone([int(pids["client"]), int(pids["sleep"])]) == []
    log = fake_docker_logging_argv.read_text(encoding="utf-8").splitlines()
    name = f"holon-alpha-executor-{pids['run_id']}-1"
    assert f"run --name {name} --label holon.flow.run={pids['run_id']} img" in log
    assert f"stop --time 10 {name}" in log
    assert f"ps -aq --filter label=holon.flow.run={pids['run_id']}" in log
    assert "rm -f c0ffee" in log
    assert Path(str(run_lock.run_lock_path(str(repo), "alpha"))).read_text(encoding="utf-8") == ""


def test_without_an_active_run_launches_are_plain_and_reaping_calls_nothing() -> None:
    calls: list[list[str]] = []

    assert run_tracking.container_identity_args("executor") == []
    process_reaper.reap_flow_run(ReapSeams(docker=lambda args: calls.append(args), process_table=dict))

    assert calls == []


def test_run_tracked_returns_the_exit_code_and_forgets_a_container_that_exited() -> None:
    run_tracking.start_run("alpha", "run1")
    name = run_tracking.container_identity_args("executor")[1]

    assert run_tracking.run_tracked(["sh", "-c", "exit 3", "--name", name]) == 3

    run = run_tracking.active_run()
    assert run is not None and run.container_names == [] and run.clients == {}


def test_container_names_follow_the_docker_grammar_and_stay_short() -> None:
    run_tracking.start_run("a/very long slug!" * 6, "abc123")

    args = run_tracking.container_identity_args("conflict resolver")

    name = args[1]
    assert len(name) <= run_tracking.MAX_CONTAINER_NAME_LENGTH
    assert name.endswith("-conflict-resolver-abc123-1")
    assert all(ch.isalnum() or ch in "_.-" for ch in name)
    assert args[2:] == ["--label", "holon.flow.run=abc123"]


def test_reaping_never_signals_the_flow_process_its_group_or_init() -> None:
    owner = os.getpid()
    owner_group = os.getpgid(owner)
    run_tracking.start_run("alpha", "run1")
    run = run_tracking.active_run()
    assert run is not None
    run.clients.update({owner_group: True, 1: True, owner: False, 0: False, 999_991: True})
    table = {
        owner: (owner_group, owner_group, 0.0, 0.0),
        999_992: (owner, owner_group, 0.0, 0.0),
        999_993: (999_992, owner_group, 0.0, 0.0),
        999_994: (1, 1, 0.0, 0.0),
    }
    signals: list[tuple[str, int, int]] = []
    seams = ReapSeams(
        docker=lambda args: None,
        kill=lambda pid, sig: signals.append(("kill", pid, sig)),
        killpg=lambda pid, sig: signals.append(("killpg", pid, sig)),
        process_table=lambda: table,
        grace_seconds=0.0,
    )

    process_reaper.reap_flow_run(seams)

    targets = {(how, pid) for how, pid, _sig in signals}
    assert targets == {("killpg", 999_991), ("kill", 999_992), ("kill", 999_993)}
    assert {sig for _how, _pid, sig in signals} <= {signal.SIGTERM, signal.SIGKILL}


def test_start_sweeps_the_containers_of_a_dead_holder_by_its_run_label() -> None:
    calls: list[list[str]] = []

    def docker(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(["docker", *args], 0, "dead1\n" if args[0] == "ps" else "", "")

    process_reaper.start_reaper_run("alpha", "run1", ["oldrun"], ReapSeams(docker=docker))

    assert calls == [["ps", "-aq", "--filter", "label=holon.flow.run=oldrun"], ["rm", "-f", "dead1"]]
    assert signal.getsignal(signal.SIGTERM) is process_reaper._on_signal
    process_reaper.reset_reaper()
    assert signal.getsignal(signal.SIGTERM) is not process_reaper._on_signal
