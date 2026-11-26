"""The per-intent flow run lock (flow/run_lock.py): holder record, refusal, per-slug independence and takeover."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from sandbox_executor.flow import process_reaper, run_lock
from sandbox_executor.flow.run_lock import FlowRunLockHeld, acquire_flow_run_lock, release_flow_run_lock, run_lock_path
from sandbox_executor.flow.run_lock_record import RunLockHolder

from tests.flow.helpers import _git


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fresh repository with one commit, private to the test."""
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "commit", "-q", "--allow-empty", "-m", "seed")
    return path


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _record(path: str) -> dict[str, object]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _stale_record(path: str, pid: int, run_id: str = "stale0run") -> RunLockHolder:
    holder = RunLockHolder(pid, socket.gethostname(), "2026-01-01T00:00:00+00:00", "alpha", "/gone", run_id)
    Path(path).write_text(holder.to_json(), encoding="utf-8")
    return holder


def test_acquiring_records_the_holder_in_the_common_git_directory(repo: Path) -> None:
    held = acquire_flow_run_lock(str(repo), "alpha", "run1")

    assert held is not None
    assert held.path == os.path.join(os.path.realpath(repo / ".git"), "holon-flow-run-alpha.lock")
    assert _record(held.path) == {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at": held.holder.started_at,
        "slug": "alpha",
        "worktree": str(repo),
        "run_id": "run1",
    }
    assert held.stale is None


def test_a_linked_worktree_and_the_main_checkout_share_one_lock_file(repo: Path, tmp_path: Path) -> None:
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-q", "--detach", str(linked), "main")

    assert run_lock_path(str(linked), "alpha") == run_lock_path(str(repo), "alpha")
    assert run_lock_path(str(repo / "not-yet-provisioned"), "alpha") == run_lock_path(str(repo), "alpha")


def test_the_slug_never_leaves_the_git_directory(repo: Path) -> None:
    path = run_lock_path(str(repo), "../../etc/x y")

    assert path is not None
    assert os.path.dirname(path) == os.path.realpath(repo / ".git")
    assert os.path.basename(path) == "holon-flow-run-etc-x-y.lock"


def test_a_live_holder_in_another_process_refuses_the_second_run(repo: Path, tmp_path: Path, lock_holder) -> None:
    holder = lock_holder(repo, "alpha")
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-q", "--detach", str(linked), "main")

    with pytest.raises(FlowRunLockHeld) as refused:
        acquire_flow_run_lock(str(linked), "alpha", "run2")

    message = str(refused.value)
    assert f"pid {holder.pid} on {socket.gethostname()}" in message
    assert refused.value.path in message
    assert _record(refused.value.path)["pid"] == holder.pid


def test_different_slugs_are_held_at_the_same_time(repo: Path, lock_holder) -> None:
    holder = lock_holder(repo, "alpha")

    held = acquire_flow_run_lock(str(repo), "beta", "run2")

    assert held is not None
    assert _record(held.path)["pid"] == os.getpid()
    assert _record(str(run_lock_path(str(repo), "alpha")))["pid"] == holder.pid


def test_the_lock_is_re_entrant_within_the_holding_process(repo: Path) -> None:
    first = acquire_flow_run_lock(str(repo), "alpha", "run1")
    again = acquire_flow_run_lock(str(repo), "alpha", "run2")

    assert again == first


def test_a_record_under_a_free_lock_is_taken_over_and_logged(repo: Path, caplog) -> None:
    path = str(run_lock_path(str(repo), "alpha"))
    stale = _stale_record(path, 4242)

    with caplog.at_level(logging.WARNING, logger="sandbox_executor.flow.run_lock"):
        held = acquire_flow_run_lock(str(repo), "alpha", "run1")

    assert held is not None and held.stale == stale
    assert f"Taking over flow run lock {path} from pid 4242" in caplog.text
    assert _record(path)["pid"] == os.getpid()


def test_a_dead_holder_whose_lock_a_fork_still_holds_is_taken_over(repo: Path, caplog) -> None:
    path = str(run_lock_path(str(repo), "alpha"))
    dead = _dead_pid()
    _stale_record(path, dead)
    descriptor = os.open(path, os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    inheritor = subprocess.Popen(["sleep", "30"], pass_fds=(descriptor,))
    os.close(descriptor)
    try:
        with caplog.at_level(logging.WARNING, logger="sandbox_executor.flow.run_lock"):
            held = acquire_flow_run_lock(str(repo), "alpha", "run1")
    finally:
        inheritor.kill()
        inheritor.wait()

    assert held is not None and held.stale is not None and held.stale.pid == dead
    assert f"from pid {dead}" in caplog.text
    assert "its pid is dead" in caplog.text
    assert _record(path)["pid"] == os.getpid()


def test_a_live_pid_is_never_taken_over_even_by_a_record_check(repo: Path, lock_holder) -> None:
    holder = lock_holder(repo, "alpha")
    path = str(run_lock_path(str(repo), "alpha"))

    with pytest.raises(FlowRunLockHeld):
        acquire_flow_run_lock(str(repo), "alpha", "run2")

    assert _record(path)["pid"] == holder.pid


def test_a_holder_on_another_host_is_never_taken_over(repo: Path) -> None:
    path = str(run_lock_path(str(repo), "alpha"))
    remote = RunLockHolder(_dead_pid(), "elsewhere.invalid", "2026-01-01T00:00:00+00:00", "alpha", "/w", "r")
    Path(path).write_text(remote.to_json(), encoding="utf-8")
    descriptor = os.open(path, os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    inheritor = subprocess.Popen(["sleep", "30"], pass_fds=(descriptor,))
    os.close(descriptor)
    try:
        with pytest.raises(FlowRunLockHeld, match=r"on elsewhere\.invalid"):
            acquire_flow_run_lock(str(repo), "alpha", "run1")
    finally:
        inheritor.kill()
        inheritor.wait()


def test_a_clean_release_empties_the_file_so_the_next_run_logs_no_takeover(repo: Path, caplog) -> None:
    held = acquire_flow_run_lock(str(repo), "alpha", "run1")
    assert held is not None
    release_flow_run_lock()

    assert Path(held.path).read_text(encoding="utf-8") == ""
    with caplog.at_level(logging.WARNING, logger="sandbox_executor.flow.run_lock"):
        again = acquire_flow_run_lock(str(repo), "alpha", "run2")
    assert again is not None and again.stale is None
    assert "Taking over" not in caplog.text


def test_a_holder_that_exits_releases_through_its_exit_hook(repo: Path, lock_holder, caplog) -> None:
    holder = lock_holder(repo, "alpha", mode="hold")
    assert holder.stdin is not None
    holder.stdin.close()
    assert holder.wait(timeout=10) == 0

    assert Path(str(run_lock_path(str(repo), "alpha"))).read_text(encoding="utf-8") == ""
    with caplog.at_level(logging.WARNING, logger="sandbox_executor.flow.run_lock"):
        assert acquire_flow_run_lock(str(repo), "alpha", "run2") is not None
    assert "Taking over" not in caplog.text


def test_outside_a_repository_the_run_is_not_locked(tmp_path: Path, caplog) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    with caplog.at_level(logging.WARNING, logger="sandbox_executor.flow.run_lock"):
        assert acquire_flow_run_lock(str(plain), "alpha", "run1") is None
    assert "the flow run lock of 'alpha' is not taken" in caplog.text


def test_hold_refuses_with_exit_1_naming_the_holder_and_the_lock_file(repo: Path, lock_holder, capsys) -> None:
    holder = lock_holder(repo, "alpha")

    with pytest.raises(SystemExit) as exited:
        run_lock.hold_flow_run_lock(str(repo), "alpha")

    assert exited.value.code == 1
    err = capsys.readouterr().err
    assert f"pid {holder.pid}" in err
    assert str(run_lock_path(str(repo), "alpha")) in err


def test_hold_starts_the_reaper_run_with_the_stale_run_id(repo: Path, monkeypatch) -> None:
    started: list[tuple[str, str, list[str]]] = []
    monkeypatch.setattr(
        process_reaper, "start_reaper_run", lambda slug, run_id, stale: started.append((slug, run_id, stale))
    )
    _stale_record(str(run_lock_path(str(repo), "alpha")), 4242, run_id="oldrun")

    assert run_lock.hold_flow_run_lock(str(repo), "alpha") is True

    ((slug, run_id, stale),) = started
    assert (slug, stale) == ("alpha", ["oldrun"])
    assert _record(str(run_lock_path(str(repo), "alpha")))["run_id"] == run_id
