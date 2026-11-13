"""Stage 5 syncs with origin before calibrating: bare-repo origins only, no network, no Docker, no model call."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sandbox_executor.flow import FlowContext, StageStatus
from sandbox_executor.flow.stages.calibrate import run_calibrate_stage
from sandbox_executor.ref_names import calibration_branch

EXEC = "I-1-x/P-2-y/E-3-z/_"
CALIBRATED = calibration_branch(EXEC)
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def git(cwd: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env={**os.environ, **GIT_ENV}
    )
    return res.stdout.strip()


def commit_file(repo: Path, name: str, text: str = "x") -> str:
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-qm", f"add {name}")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path: Path) -> SimpleNamespace:
    """A bare origin, the host worktree (on its own branch) and a second clone that pushes ahead of it."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    host = tmp_path / "host"
    git(tmp_path, "clone", "-q", str(origin), str(host))
    git(host, "checkout", "-qb", "main")
    commit_file(host, "base.txt")
    git(host, "push", "-q", "origin", "main")
    git(host, "checkout", "-qb", EXEC)
    commit_file(host, "exec.txt")
    git(host, "push", "-q", "origin", EXEC)
    git(host, "checkout", "-qb", "operator-work", "main")
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(origin), str(other))
    git(other, "checkout", "-q", EXEC)
    return SimpleNamespace(origin=origin, host=host, other=other)


def push_ahead(repos: SimpleNamespace) -> str:
    """Commit a merge-from-main style file and a ledger-interleave file on origin only."""
    commit_file(repos.other, "from-main.txt")
    sha = commit_file(repos.other, "ledger-interleave.txt")
    git(repos.other, "push", "-q", "origin", EXEC)
    return sha


def fake_calibrate(**kwargs: object) -> SimpleNamespace:
    """Stand in for run_calibrate: check out the calibrated branch, commit a report, as the real one does."""
    if kwargs["skip_commit"]:
        return SimpleNamespace(calibrated_branch=None, to_dict=dict)
    cwd = Path(str(kwargs["target_worktree"]))
    local = subprocess.run(["git", "rev-parse", "--verify", "-q", f"refs/heads/{CALIBRATED}"], cwd=cwd)
    git(cwd, "checkout", "-q", CALIBRATED) if local.returncode == 0 else git(cwd, "checkout", "-qb", CALIBRATED)
    (cwd / "report.md").write_text("report\n")
    tree = sorted(p.name for p in cwd.iterdir() if p.is_file())
    git(cwd, "add", "report.md")
    git(cwd, "commit", "-qm", "report")
    return SimpleNamespace(calibrated_branch=CALIBRATED, to_dict=lambda: {"tree": tree})


def run_stage(repos: SimpleNamespace, **kwargs: bool):
    ctx = FlowContext(target_worktree=str(repos.host), execution_branch=EXEC, agent="a", model="m", **kwargs)
    with (
        patch("sandbox_executor.flow.stages.calibrate.check_and_resolve_stage_conflicts"),
        patch("sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=fake_calibrate),
        patch("sandbox_executor.flow.stages.calibrate.check_final_head_ci", return_value={"state": "success"}),
    ):
        return ctx, run_calibrate_stage(ctx)


def test_calibrated_branch_descends_from_origin_head_and_fast_forwards(repos: SimpleNamespace) -> None:
    origin_head = push_ahead(repos)
    _, result = run_stage(repos)
    assert result.status == StageStatus.SUCCESS
    host = repos.host
    git(host, "merge-base", "--is-ancestor", origin_head, CALIBRATED)
    assert git(host, "rev-parse", EXEC) == git(host, "rev-parse", CALIBRATED)
    assert git(host, "rev-list", "--merges", "--count", f"{origin_head}..{EXEC}") == "0"
    assert git(host, "rev-parse", f"origin/{EXEC}") == git(host, "rev-parse", EXEC)


def test_calibrated_report_tree_contains_origin_only_files(repos: SimpleNamespace) -> None:
    push_ahead(repos)
    _, result = run_stage(repos)
    tree = result.payload["calibration_report"]["tree"]
    assert {"from-main.txt", "ledger-interleave.txt", "exec.txt"} <= set(tree)


def test_divergent_local_ref_fails_loudly_and_changes_nothing(repos: SimpleNamespace) -> None:
    origin_head = push_ahead(repos)
    git(repos.host, "checkout", "-q", EXEC)
    local_sha = commit_file(repos.host, "local-only.txt")
    git(repos.host, "checkout", "-q", "operator-work")
    _, result = run_stage(repos)
    assert result.status == StageStatus.FAILED
    assert EXEC in result.error
    assert local_sha[:12] in result.error and origin_head[:12] in result.error
    assert "git push origin" in result.error
    assert git(repos.host, "rev-parse", EXEC) == local_sha
    assert git(repos.host, "branch", "--list", CALIBRATED) == ""
    assert git(repos.host, "symbolic-ref", "--short", "HEAD") == "operator-work"


def test_starting_branch_is_restored_with_a_clean_worktree(repos: SimpleNamespace) -> None:
    push_ahead(repos)
    run_stage(repos)
    assert git(repos.host, "symbolic-ref", "--short", "HEAD") == "operator-work"
    assert git(repos.host, "status", "--porcelain") == ""


def test_detached_head_is_restored(repos: SimpleNamespace) -> None:
    sha = git(repos.host, "rev-parse", "main")
    git(repos.host, "checkout", "-q", "--detach", sha)
    run_stage(repos)
    assert subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=repos.host).returncode != 0
    assert git(repos.host, "rev-parse", "HEAD") == sha


def test_missing_origin_branch_proceeds_from_local_ref(repos: SimpleNamespace) -> None:
    git(repos.host, "push", "-q", "origin", "--delete", EXEC)
    git(repos.host, "update-ref", "-d", f"refs/remotes/origin/{EXEC}")
    _, result = run_stage(repos, skip_push=True)
    assert result.status == StageStatus.SUCCESS
    assert git(repos.host, "rev-parse", EXEC) == git(repos.host, "rev-parse", CALIBRATED)


def test_dry_run_touches_no_ref(repos: SimpleNamespace) -> None:
    push_ahead(repos)
    before = git(repos.host, "for-each-ref")
    with patch("sandbox_executor.flow.stages.calibrate._checkout_execution_branch", return_value=None):
        run_stage(repos, dry_run=True)
    assert git(repos.host, "for-each-ref") == before
    assert git(repos.host, "symbolic-ref", "--short", "HEAD") == "operator-work"


def test_recording_the_starting_branch_uses_the_callers_git_runner(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from sandbox_executor.flow.calibrate_sync import record_starting_branch

    fake = MagicMock(return_value=SimpleNamespace(returncode=0, stdout="operator-work\n"))
    ctx = FlowContext(target_worktree=str(tmp_path), execution_branch=EXEC, agent="a", model="m")
    with patch("sandbox_executor.flow.calibrate_sync.run_git", side_effect=AssertionError("spawned a real git")):
        record_starting_branch(ctx, fake)
    assert ctx.starting_branch == "operator-work"
    fake.assert_called_once()


def test_a_detached_head_is_resolved_through_the_callers_git_runner_too(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from sandbox_executor.flow.calibrate_sync import record_starting_branch

    answers = [SimpleNamespace(returncode=1, stdout=""), SimpleNamespace(returncode=0, stdout="abc123\n")]
    fake = MagicMock(side_effect=answers)
    ctx = FlowContext(target_worktree=str(tmp_path), execution_branch=EXEC, agent="a", model="m")
    with patch("sandbox_executor.flow.calibrate_sync.run_git", side_effect=AssertionError("spawned a real git")):
        record_starting_branch(ctx, fake)
    assert (ctx.starting_branch, ctx.starting_detached) == ("abc123", True)
