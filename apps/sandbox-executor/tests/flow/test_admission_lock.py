"""The per-repository Stage 3 admission lock (flow/admission_lock.py): exclusion, timeout and the admission race."""

from __future__ import annotations

import os
import subprocess
import threading

import pytest
from sandbox_executor.flow.admission_lock import (
    ADMISSION_LOCK_NAME,
    AdmissionLockError,
    admission_lock,
    admission_lock_path,
)

from tests.flow.helpers import _git


def test_lock_path_is_in_the_common_git_directory_of_every_worktree(flow_repos, tmp_path):
    host, _origin = flow_repos
    second = tmp_path / "second"
    _git(host, "worktree", "add", "-q", "--detach", str(second), "main")
    expected = os.path.realpath(host / ".git" / ADMISSION_LOCK_NAME)
    assert os.path.realpath(admission_lock_path(str(host))) == expected
    assert os.path.realpath(admission_lock_path(str(second))) == expected


def test_lock_path_fails_closed_when_git_fails(tmp_path):
    def failing_git(args, cwd):
        return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: not a git repository")

    with pytest.raises(AdmissionLockError, match="git-common-dir"):
        admission_lock_path(str(tmp_path), failing_git)


def test_a_second_holder_times_out_then_acquires_after_release(flow_repos, tmp_path):
    host, _origin = flow_repos
    second = tmp_path / "second"
    _git(host, "worktree", "add", "-q", "--detach", str(second), "main")
    held, release = threading.Event(), threading.Event()

    def hold() -> None:
        with admission_lock(str(host)):
            held.set()
            release.wait(timeout=10)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(timeout=10)
        with (
            pytest.raises(AdmissionLockError, match="held the admission lock"),
            admission_lock(str(second), timeout_seconds=0.2, poll_seconds=0.02),
        ):
            pass
    finally:
        release.set()
        holder.join(timeout=10)
    with admission_lock(str(second), timeout_seconds=5, poll_seconds=0.02) as path:
        assert path.endswith(ADMISSION_LOCK_NAME)
