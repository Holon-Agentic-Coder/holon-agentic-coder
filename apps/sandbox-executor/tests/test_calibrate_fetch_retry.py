"""Stage 5 retries a transient origin fetch failure with a bounded backoff: bare-repo origins, fakes, no real sleep."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.calibrate_fetch_retry import (
    FETCH_ATTEMPTS,
    FETCH_BACKOFF_SECONDS,
    _fetch_execution_branch_with_retry,
)
from sandbox_executor.flow.calibrate_rebuild import CalibrationSyncError
from sandbox_executor.flow.calibrate_sync import sync_execution_ref
from sandbox_executor.flow.host_remote import HostRemoteError, fetch_base

from tests.flow.sleep_patch import scoped_sleep_patch
from tests.test_calibrate_sync import EXEC, git, make_repos

TRANSIENT = "ssh: connect to host github.com port 22: Connection timed out"


def transient_error() -> HostRemoteError:
    return HostRemoteError("fetch", EXEC, "origin", TRANSIENT)


class ScriptedFetch:
    """Raise or delegate to the real fetch, one scripted outcome per call."""

    def __init__(self, outcomes: list[HostRemoteError | Exception | None]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def __call__(self, repo_dir: str, branch: str, remote: str) -> None:
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if outcome is not None:
            raise outcome
        fetch_base(repo_dir, branch, remote)


def make_context(host: Path, *, skip_push: bool = False) -> FlowContext:
    return FlowContext(target_worktree=str(host), execution_branch=EXEC, agent="a", model="m", skip_push=skip_push)


def retry_lines(context: FlowContext) -> list[str]:
    return [line for line in context.logs if "retrying in" in line]


def warning_lines(context: FlowContext) -> list[str]:
    return [line for line in context.logs if "Warning: could not fetch origin/" in line]


def run_sync(context: FlowContext, fetch: ScriptedFetch, sleeps: list[float]) -> str | None:
    with (
        patch("sandbox_executor.flow.calibrate_fetch_retry.fetch_base", fetch),
        scoped_sleep_patch("sandbox_executor.flow.calibrate_fetch_retry", sleeps.append),
    ):
        return sync_execution_ref(context)


def test_a_transient_fetch_failure_then_success_lets_sync_complete(tmp_path: Path) -> None:
    repos = make_repos(tmp_path)
    context = make_context(repos.host)
    fetch, sleeps = ScriptedFetch([transient_error(), None]), []
    started = time.monotonic()
    start_sha = run_sync(context, fetch, sleeps)
    assert start_sha == git(repos.origin, "rev-parse", EXEC)
    assert fetch.calls == 2
    assert sleeps == [4.0]
    [line] = retry_lines(context)
    assert "attempt 1/3" in line
    assert TRANSIENT in line
    assert not warning_lines(context)
    assert time.monotonic() - started < 4.0


def test_success_on_the_third_attempt_logs_two_retries(tmp_path: Path) -> None:
    repos = make_repos(tmp_path)
    context = make_context(repos.host)
    fetch, sleeps = ScriptedFetch([transient_error(), transient_error(), None]), []
    assert run_sync(context, fetch, sleeps) == git(repos.origin, "rev-parse", EXEC)
    assert fetch.calls == 3
    assert sleeps == [4.0, 8.0]
    lines = retry_lines(context)
    assert len(lines) == 2
    assert "attempt 1/3" in lines[0]
    assert "attempt 2/3" in lines[1]


def test_exhausted_attempts_with_an_origin_ref_and_push_enabled_raise(tmp_path: Path) -> None:
    repos = make_repos(tmp_path)
    context = make_context(repos.host)
    fetch, sleeps = ScriptedFetch([transient_error()] * 3), []
    with pytest.raises(CalibrationSyncError, match="could not fetch origin/"):
        run_sync(context, fetch, sleeps)
    assert fetch.calls == FETCH_ATTEMPTS
    assert sleeps == list(FETCH_BACKOFF_SECONDS)
    assert len(retry_lines(context)) == 2


def test_exhausted_attempts_without_push_only_warn(tmp_path: Path) -> None:
    repos = make_repos(tmp_path)
    context = make_context(repos.host, skip_push=True)
    fetch, sleeps = ScriptedFetch([transient_error()] * 3), []
    run_sync(context, fetch, sleeps)
    assert fetch.calls == FETCH_ATTEMPTS
    [line] = warning_lines(context)
    assert "sync not confirmed" in line
    assert sum(sleeps) <= 15


def test_exhausted_attempts_without_an_origin_ref_only_warn(tmp_path: Path) -> None:
    repos = make_repos(tmp_path)
    git(repos.host, "update-ref", "-d", f"refs/remotes/origin/{EXEC}")
    context = make_context(repos.host)
    fetch, sleeps = ScriptedFetch([transient_error()] * 3), []
    assert run_sync(context, fetch, sleeps) == git(repos.host, "rev-parse", f"refs/heads/{EXEC}")
    assert fetch.calls == FETCH_ATTEMPTS
    assert len(warning_lines(context)) == 1
    assert sum(sleeps) <= 15


@pytest.mark.parametrize(
    "detail",
    [
        "fatal: couldn't find remote ref refs/heads/x",
        "fatal: 'origin' does not appear to be a git repository",
        "fatal: not a git repository (or any of the parent directories): .git",
        "refusing to fetch: the target branch name is not a plain branch name",
    ],
)
def test_a_permanent_fetch_failure_is_not_retried(tmp_path: Path, detail: str) -> None:
    context = make_context(tmp_path)
    fetch = ScriptedFetch([HostRemoteError("fetch", EXEC, "origin", detail)])
    sleeps: list[float] = []
    with pytest.raises(HostRemoteError) as raised:
        _fetch_execution_branch_with_retry(context, EXEC, fetch=fetch, sleep=sleeps.append)
    assert raised.value.detail == detail
    assert fetch.calls == 1
    assert sleeps == []
    assert not retry_lines(context)


def test_attempt_count_and_total_backoff_are_bounded() -> None:
    assert FETCH_ATTEMPTS == 3
    assert 10 <= sum(FETCH_BACKOFF_SECONDS) <= 15


def test_non_host_remote_errors_propagate_without_retry(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    fetch = ScriptedFetch([RuntimeError("bug")])
    sleeps: list[float] = []
    with pytest.raises(RuntimeError, match="bug"):
        _fetch_execution_branch_with_retry(context, EXEC, fetch=fetch, sleep=sleeps.append)
    assert fetch.calls == 1
    assert sleeps == []
