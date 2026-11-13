"""Stage 5 rebuilds the flow's own stale /calibrated branch and refuses foreign work: bare-repo origins only."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sandbox_executor.cli.commands.flow import _report_flow_summary
from sandbox_executor.flow import StageStatus
from sandbox_executor.flow.calibrate_rebuild import is_calibration_path

from tests.test_calibrate_sync import CALIBRATED, EXEC, git, make_repos, push_ahead, run_stage

REPORT = ("plans/P-2-y_calibration.md", "chore(calibration): add calibration report for P-2-y")


@pytest.fixture
def repos(tmp_path: Path) -> SimpleNamespace:
    return make_repos(tmp_path)


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


def test_own_diverged_calibrated_branch_is_rebuilt_from_the_origin_head(repos: SimpleNamespace) -> None:
    old = calibrated_on_origin(repos, *REPORT)
    head = push_ahead(repos)
    ctx, result = run_stage(repos)
    assert result.status == StageStatus.SUCCESS
    host = repos.host
    git(host, "merge-base", "--is-ancestor", head, CALIBRATED)
    git(repos.origin, "merge-base", "--is-ancestor", head, f"refs/heads/{CALIBRATED}")
    assert git(host, "rev-parse", EXEC) == git(host, "rev-parse", CALIBRATED)
    logs = "\n".join(ctx.logs)
    assert "rebuilt" in logs and old[:12] in logs and head[:12] in logs
    assert result.payload["calibrated_head"] == head


@pytest.mark.parametrize(
    ("path", "subject"),
    [("notes.txt", "add notes"), ("src/x.py", "chore(calibration): add calibration report for P-2-y")],
)
def test_foreign_commit_still_stops_and_changes_nothing(repos: SimpleNamespace, path: str, subject: str) -> None:
    old = calibrated_on_origin(repos, path, subject)
    push_ahead(repos)
    _, result = run_stage(repos)
    assert result.status == StageStatus.FAILED
    assert "does not contain the execution head" in result.error and "git branch -D" in result.error
    assert git(repos.host, "rev-parse", CALIBRATED) == old
    assert origin_sha(repos, CALIBRATED) == old
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


def test_skip_push_rebuilds_locally_and_leaves_origin_untouched(repos: SimpleNamespace) -> None:
    old = calibrated_on_origin(repos, *REPORT)
    head = push_ahead(repos)
    _, result = run_stage(repos, skip_push=True)
    assert result.status == StageStatus.SUCCESS
    git(repos.host, "merge-base", "--is-ancestor", head, CALIBRATED)
    assert origin_sha(repos, CALIBRATED) == old


def test_origin_only_stale_calibrated_branch_is_replaced_with_a_lease(repos: SimpleNamespace) -> None:
    calibrated_on_origin(repos, *REPORT)
    git(repos.host, "branch", "-D", CALIBRATED)
    head = push_ahead(repos)
    _, result = run_stage(repos)
    assert result.status == StageStatus.SUCCESS
    git(repos.origin, "merge-base", "--is-ancestor", head, f"refs/heads/{CALIBRATED}")
    assert git(repos.host, "rev-parse", EXEC) == git(repos.host, "rev-parse", CALIBRATED)


def test_flow_summary_names_the_calibrated_head(repos: SimpleNamespace, capsys: pytest.CaptureFixture[str]) -> None:
    head = push_ahead(repos)
    ctx, _ = run_stage(repos)
    _report_flow_summary(ctx, {}, json_output=False)
    assert f"calibration report covers execution head {head[:12]}" in capsys.readouterr().out


def test_flow_summary_says_a_failed_stage_5_refreshed_nothing(
    repos: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    calibrated_on_origin(repos, "notes.txt", "add notes")
    push_ahead(repos)
    ctx, _ = run_stage(repos)
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
        ("plans/sub/P-2-y_calibration.md", False),
        ("holon-knowledge/ledger/intents.jsonl", False),
        ("src/x.py", False),
    ],
)
def test_calibration_path_allow_list(path: str, allowed: bool) -> None:
    assert is_calibration_path(path) is allowed
