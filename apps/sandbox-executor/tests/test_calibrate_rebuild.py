"""Stage 5 rebuilds the flow's own stale /calibrated branch and refuses foreign work: bare-repo origins only."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sandbox_executor.cli.commands.flow import _report_flow_summary
from sandbox_executor.flow import StageStatus
from sandbox_executor.flow.calibrate_rebuild import is_calibration_path

from tests.calibrate_scenarios import (
    build_rebuild_stale_repos,
    build_sync_repos,
    build_sync_repos_ahead,
    clone_flow_pair,
    own_copy,
    run_guarded_template,
)
from tests.repo_templates import shared_template
from tests.test_calibrate_sync import CALIBRATED, EXEC, git, push_ahead, run_stage

REPORT = ("plans/P-2-y_calibration.md", "chore(calibration): add calibration report for P-2-y")


@pytest.fixture(scope="session")
def sync_repos_template(tmp_path_factory: pytest.TempPathFactory, hermetic_git_config: str) -> SimpleNamespace:
    root, built = shared_template(tmp_path_factory, "calibrate-sync-repos", build_sync_repos)
    return SimpleNamespace(root=root, repos=built)


@pytest.fixture(scope="session")
def sync_repos_ahead_template(tmp_path_factory: pytest.TempPathFactory, hermetic_git_config: str) -> SimpleNamespace:
    root, built = shared_template(tmp_path_factory, "calibrate-sync-repos-ahead", build_sync_repos_ahead)
    return SimpleNamespace(root=root, repos=built.repos, origin_head=built.origin_head)


@pytest.fixture(scope="session")
def rebuild_stale_template(tmp_path_factory: pytest.TempPathFactory, hermetic_git_config: str) -> SimpleNamespace:
    root, built = shared_template(
        tmp_path_factory, "calibrate-rebuild-stale", lambda r: build_rebuild_stale_repos(r, *REPORT)
    )
    return SimpleNamespace(root=root, repos=built.repos, old=built.old, head=built.head)


@pytest.fixture
def repos(sync_repos_template: SimpleNamespace, tmp_path: Path) -> SimpleNamespace:
    return own_copy(sync_repos_template, tmp_path)


@pytest.fixture
def repos_ahead(sync_repos_ahead_template: SimpleNamespace, tmp_path: Path) -> SimpleNamespace:
    own = own_copy(sync_repos_ahead_template, tmp_path)
    own.origin_head = sync_repos_ahead_template.origin_head
    return own


@pytest.fixture
def rebuild_stale(rebuild_stale_template: SimpleNamespace, tmp_path: Path) -> SimpleNamespace:
    own = own_copy(rebuild_stale_template, tmp_path)
    own.old, own.head = rebuild_stale_template.old, rebuild_stale_template.head
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
def foreign_notes_failed_template(
    sync_repos_template: SimpleNamespace,
    tmp_path_factory: pytest.TempPathFactory,
    gh_write_guard: list[str],
    network_guard_fixture: list[str],
) -> SimpleNamespace:
    def build(root: Path) -> SimpleNamespace:
        own = own_copy(sync_repos_template, root)
        old = calibrated_on_origin(own, "notes.txt", "add notes")
        push_ahead(own)
        ctx, result = run_stage(own)
        return SimpleNamespace(repos=own, old=old, stage_run=(ctx, result))

    root, ran = run_guarded_template(
        tmp_path_factory, "calibrate-rebuild-foreign-notes", build, (gh_write_guard, network_guard_fixture)
    )
    return SimpleNamespace(root=root, repos=ran.repos, old=ran.old, stage_run=ran.stage_run)


def commit_as(repo: Path, path: str, subject: str) -> str:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(f"{subject}\n")
    git(repo, "add", path)
    git(repo, "commit", "-qm", subject)
    return git(repo, "rev-parse", "HEAD")


def calibrated_on_origin(repos: SimpleNamespace, path: str, subject: str) -> str:
    """Leave a /calibrated branch at the current execution head plus one commit, locally and on origin."""
    host = repos.host
    git(host, "checkout", "-qb", CALIBRATED, EXEC)
    sha = commit_as(host, path, subject)
    git(host, "push", "-q", "origin", CALIBRATED)
    git(host, "checkout", "-q", "operator-work")
    return sha


def origin_sha(repos: SimpleNamespace, ref: str) -> str:
    return git(repos.origin, "rev-parse", f"refs/heads/{ref}")


def test_own_diverged_calibrated_branch_is_rebuilt_from_the_origin_head(rebuild_stale: SimpleNamespace) -> None:
    old, head = rebuild_stale.old, rebuild_stale.head
    ctx, result = run_stage(rebuild_stale)
    assert result.status == StageStatus.SUCCESS
    host = rebuild_stale.host
    git(host, "merge-base", "--is-ancestor", head, CALIBRATED)
    git(rebuild_stale.origin, "merge-base", "--is-ancestor", head, f"refs/heads/{CALIBRATED}")
    assert git(host, "rev-parse", EXEC) == git(host, "rev-parse", CALIBRATED)
    logs = "\n".join(ctx.logs)
    assert "re-pointed" in logs and old[:12] in logs and head[:12] in logs
    assert result.payload["calibrated_head"] == head


@pytest.mark.parametrize(
    ("path", "subject"),
    [("notes.txt", "add notes"), ("src/x.py", "chore(calibration): add calibration report for P-2-y")],
)
def test_foreign_commit_still_stops_and_changes_nothing(
    repos: SimpleNamespace,
    foreign_notes_failed_template: SimpleNamespace,
    tmp_path: Path,
    path: str,
    subject: str,
) -> None:
    if (path, subject) == ("notes.txt", "add notes"):
        own = own_copy(foreign_notes_failed_template, tmp_path / "foreign-notes")
        old = foreign_notes_failed_template.old
        _, result = clone_flow_pair(
            foreign_notes_failed_template.root, foreign_notes_failed_template.stage_run, own.host
        )
    else:
        own = repos
        old = calibrated_on_origin(own, path, subject)
        push_ahead(own)
        _, result = run_stage(own)
    assert result.status == StageStatus.FAILED
    assert "does not contain the execution head" in result.error and "git branch -D" in result.error
    assert git(own.host, "rev-parse", CALIBRATED) == old
    assert origin_sha(own, CALIBRATED) == old
    assert result.payload["calibrated_head"] is None


def test_merge_commit_on_calibrated_counts_as_foreign(repos: SimpleNamespace) -> None:
    host = repos.host
    git(host, "checkout", "-qb", "side", EXEC)
    commit_as(host, "plans/P-2-y_calibration.md", "chore(calibration): add calibration report for P-2-y")
    git(host, "checkout", "-qb", CALIBRATED, EXEC)
    commit_as(host, "holon-knowledge/kb/k.json", "chore(calibration): record stage 5 promoted knowledge and wisdom")
    git(host, "merge", "-q", "--no-ff", "-m", "chore(calibration): merge side", "side")
    merged = git(host, "rev-parse", "HEAD")
    git(host, "checkout", "-q", "operator-work")
    push_ahead(repos)
    _, result = run_stage(repos)
    assert result.status == StageStatus.FAILED
    assert git(host, "rev-parse", CALIBRATED) == merged


def test_skip_push_rebuilds_locally_and_leaves_origin_untouched(rebuild_stale: SimpleNamespace) -> None:
    old, head = rebuild_stale.old, rebuild_stale.head
    _, result = run_stage(rebuild_stale, skip_push=True)
    assert result.status == StageStatus.SUCCESS
    git(rebuild_stale.host, "merge-base", "--is-ancestor", head, CALIBRATED)
    assert origin_sha(rebuild_stale, CALIBRATED) == old


def test_origin_only_stale_calibrated_branch_is_replaced_with_a_lease(rebuild_stale: SimpleNamespace) -> None:
    git(rebuild_stale.host, "branch", "-D", CALIBRATED)
    head = rebuild_stale.head
    _, result = run_stage(rebuild_stale)
    assert result.status == StageStatus.SUCCESS
    git(rebuild_stale.origin, "merge-base", "--is-ancestor", head, f"refs/heads/{CALIBRATED}")
    assert git(rebuild_stale.host, "rev-parse", EXEC) == git(rebuild_stale.host, "rev-parse", CALIBRATED)


def test_stale_local_with_current_origin_calibrated_follows_origin(rebuild_stale: SimpleNamespace) -> None:
    head = rebuild_stale.head
    other = rebuild_stale.other
    git(other, "checkout", "-qb", CALIBRATED, head)
    ahead = commit_as(other, "holon-knowledge/kb/k.json", "chore(calibration): record stage 5 promoted knowledge")
    git(other, "push", "-qf", "origin", CALIBRATED)
    git(rebuild_stale.host, "fetch", "-q", "origin")
    _, result = run_stage(rebuild_stale)
    assert result.status == StageStatus.SUCCESS
    git(rebuild_stale.host, "merge-base", "--is-ancestor", ahead, CALIBRATED)
    assert origin_sha(rebuild_stale, CALIBRATED) == git(rebuild_stale.host, "rev-parse", CALIBRATED)
    assert origin_sha(rebuild_stale, EXEC) == origin_sha(rebuild_stale, CALIBRATED)


def test_flow_summary_names_the_calibrated_head(
    sync_stage5_ran_template: SimpleNamespace, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = own_copy(sync_stage5_ran_template, tmp_path)
    ctx, _ = clone_flow_pair(sync_stage5_ran_template.root, sync_stage5_ran_template.stage_run, own.host)
    _report_flow_summary(ctx, {}, json_output=False)
    assert f"calibration report covers execution head {sync_stage5_ran_template.origin_head[:12]}" in (
        capsys.readouterr().out
    )


def test_failed_publish_does_not_report_a_calibrated_head(repos_ahead: SimpleNamespace) -> None:
    with patch("sandbox_executor.flow.stages.calibrate.publish_calibration", return_value=False):
        _, result = run_stage(repos_ahead)
    assert result.payload["calibrated_head"] is None


def test_flow_summary_says_a_failed_stage_5_refreshed_nothing(
    foreign_notes_failed_template: SimpleNamespace, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = own_copy(foreign_notes_failed_template, tmp_path)
    ctx, _ = clone_flow_pair(foreign_notes_failed_template.root, foreign_notes_failed_template.stage_run, own.host)
    _report_flow_summary(ctx, {}, json_output=False)
    assert "calibration report not refreshed for the current head" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("plans/P-2-y_calibration.md", True),
        ("holon-knowledge/ledger/executions.jsonl", True),
        ("holon-knowledge/kb/k.json", True),
        ("holon-knowledge/wisdom/w.json", True),
        ("plans/P-2-y.md", False),
        ("plans/I-1-x/P-2-y_calibration.md", True),
        ("plans/I-1-x--1-child/P-2-y_calibration.md", True),
        ("plans/I-1-x/P-2-y/P-3-z_calibration.md", True),
        ("plans/I-1-x/P-2-y.md", False),
        ("plans/sub/P-2-y_calibration.md", False),
        ("plans/P-2-y/P-3-z_calibration.md", False),
        ("plans/legacy/P-2-y_calibration.md", False),
        ("holon-knowledge/ledger/intents.jsonl", False),
        ("src/x.py", False),
    ],
)
def test_calibration_path_allow_list(path: str, allowed: bool) -> None:
    assert is_calibration_path(path) is allowed
