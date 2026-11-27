"""Stage 5 syncs with origin before calibrating: bare-repo origins only, no network, no Docker, no model call."""

from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sandbox_executor.flow import FlowContext, StageStatus
from sandbox_executor.flow.stages.calibrate import run_calibrate_stage

from tests.calibrate_scenarios import (
    SYNC_CALIBRATED,
    SYNC_EXEC,
    SYNC_GIT_ENV,
    build_sync_repos,
    build_sync_repos_ahead,
    clone_flow_pair,
    commit_sync_file,
    own_copy,
    push_sync_ahead,
    run_guarded_template,
    run_sync_git,
)
from tests.repo_templates import shared_template

EXEC = SYNC_EXEC
CALIBRATED = SYNC_CALIBRATED
GIT_ENV = SYNC_GIT_ENV
git = run_sync_git
commit_file = commit_sync_file
make_repos = build_sync_repos
push_ahead = push_sync_ahead


@pytest.fixture(scope="session")
def sync_repos_template(tmp_path_factory: pytest.TempPathFactory, hermetic_git_config: str) -> SimpleNamespace:
    root, built = shared_template(tmp_path_factory, "calibrate-sync-repos", build_sync_repos)
    return SimpleNamespace(root=root, repos=built)


@pytest.fixture(scope="session")
def sync_repos_ahead_template(tmp_path_factory: pytest.TempPathFactory, hermetic_git_config: str) -> SimpleNamespace:
    root, built = shared_template(tmp_path_factory, "calibrate-sync-repos-ahead", build_sync_repos_ahead)
    return SimpleNamespace(root=root, repos=built.repos, origin_head=built.origin_head)


@pytest.fixture
def repos(sync_repos_template: SimpleNamespace, tmp_path: Path) -> SimpleNamespace:
    return own_copy(sync_repos_template, tmp_path)


@pytest.fixture
def repos_ahead(sync_repos_ahead_template: SimpleNamespace, tmp_path: Path) -> SimpleNamespace:
    own = own_copy(sync_repos_ahead_template, tmp_path)
    own.origin_head = sync_repos_ahead_template.origin_head
    return own


@pytest.fixture
def sync_stage5_ran_template(
    sync_repos_ahead_template: SimpleNamespace,
    tmp_path_factory: pytest.TempPathFactory,
    gh_write_guard: list[str],
    network_guard_fixture: list[str],
) -> SimpleNamespace:
    def build(root: Path) -> SimpleNamespace:
        own = own_copy(sync_repos_ahead_template, root)
        ctx, result = run_stage(own)
        return SimpleNamespace(
            repos=own,
            origin_head=sync_repos_ahead_template.origin_head,
            stage_run=(ctx, result),
        )

    root, ran = run_guarded_template(
        tmp_path_factory, "calibrate-sync-stage5-ran", build, (gh_write_guard, network_guard_fixture)
    )
    return SimpleNamespace(root=root, repos=ran.repos, origin_head=ran.origin_head, stage_run=ran.stage_run)


@pytest.fixture
def sync_stage5_ran(sync_stage5_ran_template: SimpleNamespace, tmp_path: Path) -> SimpleNamespace:
    own = own_copy(sync_stage5_ran_template, tmp_path)
    own.origin_head = sync_stage5_ran_template.origin_head
    own.ctx, own.result = clone_flow_pair(sync_stage5_ran_template.root, sync_stage5_ran_template.stage_run, own.host)
    return own


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
        patch("sandbox_executor.flow.stages.calibrate.sync_before_calibration", return_value=None),
        patch("sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=fake_calibrate),
        patch("sandbox_executor.flow.stages.calibrate.check_final_head_ci", return_value={"state": "success"}),
    ):
        return ctx, run_calibrate_stage(ctx)


def test_calibrated_branch_descends_from_origin_head_and_fast_forwards(sync_stage5_ran: SimpleNamespace) -> None:
    origin_head, result = sync_stage5_ran.origin_head, sync_stage5_ran.result
    assert result.status == StageStatus.SUCCESS
    host = sync_stage5_ran.host
    git(host, "merge-base", "--is-ancestor", origin_head, CALIBRATED)
    assert git(host, "rev-parse", EXEC) == git(host, "rev-parse", CALIBRATED)
    assert git(host, "rev-list", "--merges", "--count", f"{origin_head}..{EXEC}") == "0"
    assert git(host, "rev-parse", f"origin/{EXEC}") == git(host, "rev-parse", EXEC)


def test_calibrated_report_tree_contains_origin_only_files(sync_stage5_ran: SimpleNamespace) -> None:
    result = sync_stage5_ran.result
    tree = result.payload["calibration_report"]["tree"]
    assert {"from-main.txt", "ledger-interleave.txt", "exec.txt"} <= set(tree)
    assert result.payload["stage5_reconciliation"] == {
        "status": "skipped_no_ledger",
        "verdict": "failure",
        "rows_appended": 0,
    }


def test_divergent_local_ref_fails_loudly_and_changes_nothing(repos_ahead: SimpleNamespace) -> None:
    origin_head = repos_ahead.origin_head
    git(repos_ahead.host, "checkout", "-q", EXEC)
    local_sha = commit_file(repos_ahead.host, "local-only.txt")
    git(repos_ahead.host, "checkout", "-q", "operator-work")
    _, result = run_stage(repos_ahead)
    assert result.status == StageStatus.FAILED
    assert EXEC in result.error
    assert local_sha[:12] in result.error and origin_head[:12] in result.error
    assert "git push origin" in result.error
    assert git(repos_ahead.host, "rev-parse", EXEC) == local_sha
    assert git(repos_ahead.host, "branch", "--list", CALIBRATED) == ""
    assert git(repos_ahead.host, "symbolic-ref", "--short", "HEAD") == "operator-work"


def test_strict_ancestor_calibrated_branch_is_rebuilt_from_the_origin_head(repos_ahead: SimpleNamespace) -> None:
    git(repos_ahead.host, "branch", CALIBRATED, "main")
    origin_head = repos_ahead.origin_head
    _, result = run_stage(repos_ahead)
    assert result.status == StageStatus.SUCCESS
    git(repos_ahead.host, "merge-base", "--is-ancestor", origin_head, CALIBRATED)
    assert git(repos_ahead.host, "rev-parse", EXEC) == git(repos_ahead.host, "rev-parse", CALIBRATED)
    assert git(repos_ahead.host, "symbolic-ref", "--short", "HEAD") == "operator-work"


def test_flow_ending_at_a_failed_review_restores_the_starting_branch(repos: SimpleNamespace) -> None:
    from sandbox_executor.flow.pipeline import PipelineEngine
    from sandbox_executor.flow.stages.review import run_review_stage

    ctx = FlowContext(target_worktree=str(repos.host), execution_branch=EXEC, agent="a", model="m")

    def review_then_fail(context: FlowContext):
        loop = patch("sandbox_executor.flow.stages.review._run_engine_review_loop", side_effect=RuntimeError("boom"))
        with loop, contextlib.suppress(RuntimeError):
            run_review_stage(context)
        assert git(repos.host, "symbolic-ref", "--short", "HEAD") == EXEC
        raise RuntimeError("review failed")

    engine = PipelineEngine(ctx, from_stage="review")
    with (
        patch("sandbox_executor.flow.stages.review.at_stage_boundary", return_value=None),
        patch.object(engine, "execute_stage", side_effect=lambda stage: review_then_fail(ctx)),
        patch("sandbox_executor.flow.pipeline.pre_run_guard_error", return_value=None),
        patch.object(engine, "save_checkpoint"),
        patch("sandbox_executor.flow.pipeline.validate_stage_ledger"),
    ):
        engine.run()
    assert git(repos.host, "symbolic-ref", "--short", "HEAD") == "operator-work"


def test_starting_branch_is_restored_with_a_clean_worktree(sync_stage5_ran: SimpleNamespace) -> None:
    assert git(sync_stage5_ran.host, "symbolic-ref", "--short", "HEAD") == "operator-work"
    assert git(sync_stage5_ran.host, "status", "--porcelain") == ""


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


def test_dry_run_touches_no_ref(repos_ahead: SimpleNamespace) -> None:
    before = git(repos_ahead.host, "for-each-ref")
    with patch("sandbox_executor.flow.stages.calibrate._checkout_execution_branch", return_value=None):
        run_stage(repos_ahead, dry_run=True)
    assert git(repos_ahead.host, "for-each-ref") == before
    assert git(repos_ahead.host, "symbolic-ref", "--short", "HEAD") == "operator-work"


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

    answers = [
        SimpleNamespace(returncode=1, stdout=""),
        SimpleNamespace(returncode=0, stdout="abc123\n"),
        SimpleNamespace(returncode=0, stdout="def456\n"),  # the execution branch tip is elsewhere
    ]
    fake = MagicMock(side_effect=answers)
    ctx = FlowContext(target_worktree=str(tmp_path), execution_branch=EXEC, agent="a", model="m")
    with patch("sandbox_executor.flow.calibrate_sync.run_git", side_effect=AssertionError("spawned a real git")):
        record_starting_branch(ctx, fake)
    assert (ctx.starting_branch, ctx.starting_detached) == ("abc123", True)


def test_a_start_detached_at_the_execution_tip_is_recorded_as_the_branch_and_never_re_detached(
    repos: SimpleNamespace,
) -> None:
    from sandbox_executor.flow.calibrate_sync import record_starting_branch, restore_starting_branch

    git(repos.host, "checkout", "-q", "--detach", EXEC)
    ctx = FlowContext(target_worktree=str(repos.host), execution_branch=EXEC, agent="a", model="m")

    record_starting_branch(ctx)
    assert (ctx.starting_branch, ctx.starting_detached) == (EXEC, False)
    git(repos.host, "symbolic-ref", "HEAD", f"refs/heads/{EXEC}")  # the stage re-attached the branch
    restore_starting_branch(ctx)

    assert git(repos.host, "symbolic-ref", "--short", "HEAD") == EXEC
    assert any("nothing to restore" in line for line in ctx.logs)
