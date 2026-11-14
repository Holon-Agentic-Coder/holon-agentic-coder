"""The swept Stage 4 and Stage 5 git calls pass fully qualified refs (or SHAs) and a ``--`` path separator."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.calibrate_rebuild import holds_only_calibration_commits
from sandbox_executor.flow.calibrate_sync import _divergence_message, publish_calibration
from sandbox_executor.flow.stages.review import run_review_stage

from tests.flow.lookup_patch import patch_lookup_sites

PLAN = "I-1-root--1-child/P-2-ab12cd34-a-m/_"
EXEC = "I-1-root--1-child/P-2-ab12cd34-a-m/E-3-ef56ab78-a-m/_"
CALIBRATED = "I-1-root--1-child/P-2-ab12cd34-a-m/E-3-ef56ab78-a-m/calibrated"
SHA_A = "a" * 40
SHA_B = "b" * 40


def recorder(stdout: str = "", returncode: int = 0) -> tuple[list[list[str]], MagicMock]:
    calls: list[list[str]] = []

    def run(args, **_kwargs):
        calls.append(list(args))
        return MagicMock(returncode=returncode, stdout=stdout, stderr="")

    return calls, MagicMock(side_effect=run)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def test_divergence_count_uses_qualified_refs_and_a_separator() -> None:
    calls, run = recorder(stdout="2\n")
    with patch("sandbox_executor.flow.calibrate_sync.run_git", run):
        message = _divergence_message("/repo", EXEC, SHA_A, SHA_B)
    assert calls == [["rev-list", "--count", f"refs/remotes/origin/{EXEC}..refs/heads/{EXEC}", "--"]]
    assert "holds 2 commit(s)" in message


def test_publish_fast_forwards_from_the_qualified_calibrated_ref() -> None:
    calls, run = recorder()
    ctx = FlowContext(target_worktree="/repo", execution_branch=EXEC, skip_push=True)
    ctx.calibrated_branch = CALIBRATED
    with patch("sandbox_executor.flow.calibrate_sync.run_git", run):
        publish_calibration(ctx)
    # checkout keeps the short name so HEAD stays attached for the later push; only the merge source is qualified.
    assert calls == [["checkout", EXEC], ["merge", "--ff-only", f"refs/heads/{CALIBRATED}"]]


def test_rebuild_inspection_calls_end_with_a_separator() -> None:
    calls, run = recorder(stdout=f"{SHA_B}\n")
    with patch("sandbox_executor.flow.calibrate_rebuild.run_git", run):
        holds_only_calibration_commits("/repo", SHA_A, SHA_B)
    assert calls[0] == ["rev-list", f"{SHA_A}..{SHA_B}", "--"]
    assert ["rev-list", "--parents", "-n", "1", SHA_B, "--"] in calls
    assert ["log", "-1", "--format=%s", SHA_B, "--"] in calls
    assert ["diff-tree", "--no-commit-id", "--name-only", "-r", "--root", SHA_B, "--"] in calls


def _run_review_numstat(tmp_path: Path, verifies: bool) -> tuple[list[list[str]], dict[str, int]]:
    ctx = FlowContext(repo_dir=str(tmp_path), plan_branch=PLAN, execution_branch=EXEC, dry_run=False)
    calls: list[list[str]] = []

    def fake_git(args, **_kwargs):
        calls.append(list(args))
        if args[:2] == ["rev-parse", "--verify"]:
            return MagicMock(returncode=0 if verifies else 1, stdout="")
        if args[:2] == ["diff", "--numstat"]:
            return MagicMock(returncode=0, stdout="7\t2\tsrc/app.py\n")
        return MagicMock(returncode=0, stdout="")

    loop_res = {"consensus": {"approved": True}, "mergeability": {}}
    with (
        patch_lookup_sites(
            "sandbox_executor.flow.github.pr.run_git",
            "sandbox_executor.flow.stage_conflicts.run_git",
            "sandbox_executor.flow.stages.review.run_git",
            side_effect=fake_git,
        ),
        patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=loop_res),
    ):
        res = run_review_stage(ctx)
    return [c for c in calls if c[:1] == ["diff"]], res.payload["review_package"]["diff_summary"]


def test_review_numstat_uses_qualified_refs_and_a_separator(tmp_path: Path) -> None:
    diffs, summary = _run_review_numstat(tmp_path, verifies=True)
    assert diffs == [["diff", "--numstat", f"refs/heads/{PLAN}", f"refs/heads/{EXEC}", "--"]]
    assert summary == {"files_changed": 1, "insertions": 7, "deletions": 2}


def test_review_numstat_is_skipped_for_an_unresolvable_branch(tmp_path: Path) -> None:
    diffs, summary = _run_review_numstat(tmp_path, verifies=False)
    assert diffs == []
    assert summary == {"files_changed": 0, "insertions": 0, "deletions": 0}


def test_divergence_count_on_a_real_nested_branch_ignores_a_colliding_tag_and_path(tmp_path: Path) -> None:
    """A tag of the same name (which git prefers to a branch) and a matching worktree path never skew the count."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    host = tmp_path / "host"
    git(tmp_path, "clone", "-q", str(origin), str(host))
    git(host, "checkout", "-qb", "main")
    git(host, "commit", "-q", "--allow-empty", "-m", "base")
    git(host, "checkout", "-qb", EXEC)
    git(host, "push", "-q", "origin", "main", EXEC)
    git(host, "commit", "-q", "--allow-empty", "-m", "local one")
    git(host, "commit", "-q", "--allow-empty", "-m", "local two")
    git(host, "tag", EXEC, "main")
    collision = host / "origin" / EXEC
    collision.parent.mkdir(parents=True)
    collision.write_text("not a ref\n", encoding="utf-8")
    local_sha = git(host, "rev-parse", f"refs/heads/{EXEC}")
    origin_sha = git(host, "rev-parse", f"refs/remotes/origin/{EXEC}")
    message = _divergence_message(str(host), EXEC, local_sha, origin_sha)
    assert "holds 2 commit(s)" in message
