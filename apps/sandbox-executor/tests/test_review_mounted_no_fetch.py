from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sandbox_executor import git as git_module
from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.flow import _execution_effective_diff, git_refs
from sandbox_executor.review_target import DiffVerdict, classify_review_diff

PLAN = "I-1-x/P-2-y/_"
EXEC = "I-1-x/P-2-y/E-3-z/_"
NETWORK_SUBCOMMANDS = {"fetch", "pull", "ls-remote", "clone"}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)


@pytest.fixture
def repo_with_host_fetched_refs(tmp_path: Path) -> Path:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _commit(origin, "base.txt")
    _git(origin, "branch", PLAN)
    _git(origin, "checkout", "-q", "-b", EXEC)
    _commit(origin, "work.txt")
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "fetch", "-q", "origin", "+refs/heads/*:refs/remotes/origin/*")
    return work


# why: a mounted role container has no ssh, so any ssh use or network fetch would fail there and fails the test outright
@pytest.fixture
def fetch_attempts(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    attempts: list[list[str]] = []
    real_run = subprocess.run

    def guarded(argv, *args, **kwargs):
        words = [str(word) for word in argv]
        assert "ssh" not in words
        if NETWORK_SUBCOMMANDS & set(words):
            attempts.append(words)
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(git_module.subprocess, "run", guarded)
    return attempts


@pytest.fixture
def mounted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLON_TARGET_WORKTREE", SANDBOX_WORKSPACE_MOUNT)


@pytest.fixture
def unmounted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOLON_TARGET_WORKTREE", raising=False)


@pytest.mark.usefixtures("mounted")
def test_mounted_compares_prefetched_refs_without_fetching(
    repo_with_host_fetched_refs: Path, fetch_attempts: list[list[str]]
):
    has_diff, detail = _execution_effective_diff(str(repo_with_host_fetched_refs), EXEC)
    assert has_diff is True
    assert "on origin" in detail
    assert fetch_attempts == []


@pytest.mark.usefixtures("mounted")
@pytest.mark.parametrize("missing", [PLAN, EXEC])
def test_mounted_missing_ref_names_branch_and_host(
    repo_with_host_fetched_refs: Path, fetch_attempts: list[list[str]], missing: str
):
    _git(repo_with_host_fetched_refs, "update-ref", "-d", f"refs/remotes/origin/{missing}")
    has_diff, detail = _execution_effective_diff(str(repo_with_host_fetched_refs), EXEC)
    assert has_diff is None
    assert f"'{missing}'" in detail
    assert "is not fetched from origin" in detail
    assert "host must fetch" in detail
    assert fetch_attempts == []


@pytest.mark.usefixtures("mounted")
def test_mounted_reports_plan_branch_first_when_both_missing(
    repo_with_host_fetched_refs: Path, fetch_attempts: list[list[str]]
):
    _git(repo_with_host_fetched_refs, "update-ref", "-d", f"refs/remotes/origin/{PLAN}")
    _git(repo_with_host_fetched_refs, "update-ref", "-d", f"refs/remotes/origin/{EXEC}")
    _has_diff, detail = _execution_effective_diff(str(repo_with_host_fetched_refs), EXEC)
    assert f"'{PLAN}'" in detail
    assert fetch_attempts == []


@pytest.mark.usefixtures("mounted")
def test_mounted_without_origin_keeps_local_fallback(tmp_path: Path):
    solo_repo = tmp_path / "solo"
    solo_repo.mkdir()
    _git(solo_repo, "init", "-q", "-b", "main")
    _commit(solo_repo, "base.txt")
    _git(solo_repo, "branch", PLAN)
    _git(solo_repo, "checkout", "-q", "-b", EXEC)
    _commit(solo_repo, "work.txt")
    has_diff, detail = _execution_effective_diff(str(solo_repo), EXEC)
    assert has_diff is True
    assert "no 'origin' remote" in detail


@pytest.mark.usefixtures("unmounted")
def test_host_still_fetches_plan_and_execution(repo_with_host_fetched_refs: Path, fetch_attempts: list[list[str]]):
    has_diff, _detail = _execution_effective_diff(str(repo_with_host_fetched_refs), EXEC)
    assert has_diff is True
    assert len(fetch_attempts) == 1
    assert f"+refs/heads/{PLAN}:refs/remotes/origin/{PLAN}" in fetch_attempts[0]
    assert f"+refs/heads/{EXEC}:refs/remotes/origin/{EXEC}" in fetch_attempts[0]


@pytest.mark.usefixtures("unmounted")
def test_host_failed_fetch_is_unverifiable(repo_with_host_fetched_refs: Path):
    _git(
        repo_with_host_fetched_refs, "remote", "set-url", "origin", str(repo_with_host_fetched_refs / "does-not-exist")
    )
    has_diff, detail = _execution_effective_diff(str(repo_with_host_fetched_refs), EXEC)
    assert has_diff is None
    assert "from origin failed" in detail


@pytest.mark.usefixtures("mounted")
def test_review_entry_path_in_mounted_workspace_never_fetches(
    repo_with_host_fetched_refs: Path, fetch_attempts: list[list[str]], monkeypatch: pytest.MonkeyPatch
):
    reached: list[str] = []
    real = git_refs._origin_refs_error

    def spy(repo_dir, plan_branch, execution_branch):
        reached.append(execution_branch)
        return real(repo_dir, plan_branch, execution_branch)

    monkeypatch.setattr(git_refs, "_origin_refs_error", spy)
    verdict, _detail = classify_review_diff(str(repo_with_host_fetched_refs), EXEC, base_branch="main")
    assert verdict is DiffVerdict.VERIFIED
    assert reached == [EXEC]
    assert fetch_attempts == []


# why: negative control proving the fetch guard records fetches on the host path
@pytest.mark.usefixtures("unmounted")
def test_review_entry_path_on_host_does_fetch(repo_with_host_fetched_refs: Path, fetch_attempts: list[list[str]]):
    verdict, _detail = classify_review_diff(str(repo_with_host_fetched_refs), EXEC, base_branch="main")
    assert verdict is DiffVerdict.VERIFIED
    assert fetch_attempts
