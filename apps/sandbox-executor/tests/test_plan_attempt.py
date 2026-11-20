"""Tests for the failed plan attempt cleanup and failure detail hand-off (real git in a temporary repository)."""

from __future__ import annotations

import pathlib
import subprocess

import pytest
from sandbox_executor.plan_attempt import (
    FAILURE_DETAIL_MAX_CHARS,
    PLAN_FAILURE_DETAIL_REL,
    clear_failure_detail,
    discard_untracked_plan_file,
    read_failure_detail,
    settle_failed_plan_attempt,
    snapshot_plan_attempt,
    untracked_plan_files,
    write_failure_detail,
)

INTENT = "I-1-api-error"
PLAN_REL = f"plans/{INTENT}/P-1-claude-agent-CO5.5.md"


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / ".gitignore").write_text(".holon/\n")
    _git(tmp_path, "add", ".gitignore")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def _write(repo: pathlib.Path, rel: str, text: str = "API Error: 400\n") -> pathlib.Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _untracked_under_plans(repo: pathlib.Path) -> str:
    return _git(repo, "status", "--porcelain", "--untracked-files=all", "--", "plans/")


def test_failure_detail_round_trip_is_clamped_and_git_ignored(repo: pathlib.Path) -> None:
    assert read_failure_detail(str(repo)) is None
    write_failure_detail(str(repo), "x" * (FAILURE_DETAIL_MAX_CHARS + 10))
    assert read_failure_detail(str(repo)) == "x" * FAILURE_DETAIL_MAX_CHARS
    assert _git(repo, "status", "--porcelain", "--untracked-files=all") == ""
    clear_failure_detail(str(repo))
    assert not (repo / PLAN_FAILURE_DETAIL_REL).exists()
    clear_failure_detail(str(repo))


def test_discard_removes_an_untracked_plan_file(repo: pathlib.Path) -> None:
    path = _write(repo, PLAN_REL)
    assert discard_untracked_plan_file(str(repo), PLAN_REL)
    assert not path.exists()
    assert _untracked_under_plans(repo) == ""


def test_discard_leaves_a_tracked_plan_file(repo: pathlib.Path) -> None:
    path = _write(repo, PLAN_REL, "# Plan for I-1\n")
    _git(repo, "add", PLAN_REL)
    _git(repo, "commit", "-q", "-m", "plan")
    path.write_text("modified after commit\n")
    assert not discard_untracked_plan_file(str(repo), PLAN_REL)
    assert path.read_text() == "modified after commit\n"


def test_discard_leaves_a_staged_plan_file(repo: pathlib.Path) -> None:
    path = _write(repo, PLAN_REL)
    _git(repo, "add", PLAN_REL)
    assert not discard_untracked_plan_file(str(repo), PLAN_REL)
    assert path.exists()


def test_discard_outside_a_repository_removes_nothing(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path, PLAN_REL)
    assert not discard_untracked_plan_file(str(tmp_path), PLAN_REL, run=_failing_git)
    assert path.exists()


def _failing_git(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args, 128, stdout="", stderr="fatal: not a git repository")


def test_untracked_plan_files_matches_plan_files_only(repo: pathlib.Path) -> None:
    _write(repo, PLAN_REL)
    _write(repo, f"plans/{INTENT}/P-1-claude-agent-CO5.5_calibration.md")
    _write(repo, f"plans/{INTENT}/notes.md")
    assert untracked_plan_files(str(repo), f"plans/{INTENT}") == {PLAN_REL}


def test_settle_removes_new_plan_files_and_returns_the_detail(repo: pathlib.Path) -> None:
    write_failure_detail(str(repo), "stale detail from an earlier attempt")
    scratch = _write(repo, f"plans/{INTENT}/P-0-human-scratch.md", "# draft\n")
    before = snapshot_plan_attempt(str(repo), f"{INTENT}/_")
    assert read_failure_detail(str(repo)) is None
    assert before == {f"plans/{INTENT}/P-0-human-scratch.md"}

    _write(repo, PLAN_REL)
    write_failure_detail(str(repo), "the planner agent's API call failed: API Error: 400 cache_control.ttl")
    detail = settle_failed_plan_attempt(str(repo), f"{INTENT}/_", before)

    assert detail is not None
    assert "cache_control.ttl" in detail
    assert not (repo / PLAN_REL).exists()
    assert scratch.exists()
    assert read_failure_detail(str(repo)) is None


def test_settle_without_detail_returns_none(repo: pathlib.Path) -> None:
    before = snapshot_plan_attempt(str(repo), INTENT)
    _write(repo, PLAN_REL)
    assert settle_failed_plan_attempt(str(repo), INTENT, before) is None
    assert _untracked_under_plans(repo) == ""


def test_settle_leaves_other_intents_alone(repo: pathlib.Path) -> None:
    other = _write(repo, "plans/I-2-other/P-2-claude-agent-CO5.5.md")
    before = snapshot_plan_attempt(str(repo), INTENT)
    settle_failed_plan_attempt(str(repo), INTENT, before)
    assert other.exists()
