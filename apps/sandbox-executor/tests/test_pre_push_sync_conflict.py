"""Hermetic tests of the pre-push sync outcomes: non-ledger conflict, ledger-only conflict and clean sync."""

import json
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.entrypoint.executor import (
    _get_repo,
    sync_and_reconcile_pre_push,
)
from sandbox_executor.entrypoint.sync_conflict import (
    SYNC_CONFLICT_STATUS,
    record_sync_failure,
    superseding_failure_row,
    sync_conflict_paths,
)
from sandbox_executor.flow import FlowContext, FlowStage, StageStatus
from sandbox_executor.flow.stages.review import run_review_stage
from sandbox_executor.flow.stages.sync_conflict import sync_conflict_result

LEDGER = "holon-knowledge/ledger/executions.jsonl"
EXEC_BRANCH = "I-1-x/P-2-a-b/E-3-a-b/_"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def commit_file(repo: Path, name: str, text: str, message: str) -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path]:
    """A work clone on a feature branch and a peer clone, both of one bare remote whose main has a file and a ledger."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    clones = []
    for name in ("work", "peer"):
        clone = tmp_path / name
        subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True, capture_output=True)
        for key, value in (("user.email", "t@holon.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
            git(clone, "config", key, value)
        clones.append(clone)
    work, peer = clones
    git(work, "checkout", "-q", "-b", "main")
    commit_file(work, "code.py", "x = 0\n", "base")
    commit_file(work, LEDGER, json.dumps({"execution_id": "E-base"}) + "\n", "ledger")
    git(work, "push", "-q", "origin", "main")
    git(peer, "pull", "-q", "origin", "main")
    git(work, "checkout", "-q", "-b", "feature")
    return work, peer


def advance_main(work: Path, peer: Path, name: str, text: str | None) -> None:
    """Advance origin/main from the peer (text None deletes the file) and fetch it, as the host does."""
    if text is None:
        git(peer, "rm", "-q", name)
        git(peer, "commit", "-q", "-m", f"delete {name}")
    else:
        commit_file(peer, name, text, f"change {name}")
    git(peer, "push", "-q", "origin", "main")
    git(work, "fetch", "-q", "origin")


def test_non_ledger_conflict_keeps_the_branch_and_records_a_sync_conflict_row(repos: tuple[Path, Path]) -> None:
    work, peer = repos
    commit_file(work, "code.py", "x = 1\n", "executor edit")
    plan_tip = git(work, "rev-parse", "HEAD~1")
    advance_main(work, peer, "code.py", None)  # a merged pull request deleted the file the branch modified

    ok, message = sync_and_reconcile_pre_push(str(work))

    assert not ok
    assert "Non-ledger merge conflicts detected" in message
    assert sync_conflict_paths(message) == ("code.py",)
    assert git(work, "status", "--porcelain") == ""
    subprocess.run(["git", "merge-base", "--is-ancestor", plan_tip, "HEAD"], cwd=work, check=True)
    assert git(work, "rev-list", "--merges", "HEAD") == ""

    ledger_dir = work / "holon-knowledge" / "ledger"
    exec_entry = {"execution_id": "E-3", "status": "success", "ledger_revision": 1}
    assert record_sync_failure(_get_repo(str(work)), exec_entry, "E-3", str(ledger_dir), message) is True
    row = json.loads((work / LEDGER).read_text(encoding="utf-8").splitlines()[-1])
    assert row["status"] == SYNC_CONFLICT_STATUS
    assert row["conflicted_paths"] == ["code.py"]
    assert row["ledger_revision"] == 2
    assert git(work, "status", "--porcelain") == ""
    assert git(work, "show", "HEAD:code.py") == "x = 1"


def test_other_sync_failures_still_record_a_failure_and_do_not_publish(repos: tuple[Path, Path]) -> None:
    work, _ = repos
    exec_entry = {"execution_id": "E-3", "status": "success", "ledger_revision": 1}
    published = record_sync_failure(
        _get_repo(str(work)), exec_entry, "E-3", str(work / "holon-knowledge" / "ledger"), "Failed to reconcile ledger"
    )
    assert published is False
    row = json.loads((work / LEDGER).read_text(encoding="utf-8").splitlines()[-1])
    assert row["status"] == "failure"
    assert "conflicted_paths" not in row
    assert row == {**row, **superseding_failure_row(exec_entry, row["summary"]), "created_at": row["created_at"]}


@pytest.mark.parametrize("status", ["failure", "over_budget", "decomposed"])
def test_conflict_after_a_non_success_execution_keeps_a_failure_and_does_not_publish(
    repos: tuple[Path, Path], status: str
) -> None:
    work, peer = repos
    commit_file(work, "code.py", "x = 1\n", "executor edit")
    advance_main(work, peer, "code.py", None)
    _, message = sync_and_reconcile_pre_push(str(work))
    exec_entry = {"execution_id": "E-3", "status": status, "ledger_revision": 1}

    ledger_dir = str(work / "holon-knowledge" / "ledger")
    published = record_sync_failure(_get_repo(str(work)), exec_entry, "E-3", ledger_dir, message)

    assert published is False
    row = json.loads((work / LEDGER).read_text(encoding="utf-8").splitlines()[-1])
    assert row["status"] == "failure"
    assert "conflicted_paths" not in row


def test_ledger_only_conflict_is_reconciled_without_a_sync_conflict_row(repos: tuple[Path, Path]) -> None:
    work, peer = repos
    commit_file(work, LEDGER, (work / LEDGER).read_text() + json.dumps({"execution_id": "E-feature"}) + "\n", "row")
    advance_main(work, peer, LEDGER, (peer / LEDGER).read_text() + json.dumps({"execution_id": "E-remote"}) + "\n")

    ok, message = sync_and_reconcile_pre_push(str(work))

    assert ok, message
    assert "Successfully reconciled" in message
    assert sync_conflict_paths(message) is None
    assert SYNC_CONFLICT_STATUS not in (work / LEDGER).read_text(encoding="utf-8")


def test_clean_sync_merges_without_a_sync_conflict_row(repos: tuple[Path, Path]) -> None:
    work, peer = repos
    commit_file(work, "mine.py", "y = 1\n", "executor edit")
    advance_main(work, peer, "other.py", "z = 1\n")

    ok, message = sync_and_reconcile_pre_push(str(work))

    assert ok
    assert message.startswith("Cleanly merged")
    assert SYNC_CONFLICT_STATUS not in (work / LEDGER).read_text(encoding="utf-8")


def stage_context(tmp_path: Path) -> FlowContext:
    return FlowContext(
        repo_dir=str(tmp_path),
        plan_branch="I-1-x/P-2-a-b/_",
        intent_data={"slug": "x"},
        agent="a",
        model="b",
        dry_run=True,
    )


def test_stage_reports_a_sync_conflict_as_resumable_and_review_accepts_the_branch(tmp_path: Path) -> None:
    context = stage_context(tmp_path)
    context.execution_branch = EXEC_BRANCH
    row = {"status": SYNC_CONFLICT_STATUS, "conflicted_paths": ["code.py"]}
    payload = {"execution_id": "E-3", "execution_branch": EXEC_BRANCH, "status": SYNC_CONFLICT_STATUS}

    result = sync_conflict_result(context, "2026-01-01T00:00:00Z", row, payload)

    assert result is not None
    assert result.status == StageStatus.SUCCESS
    assert result.payload["execution_branch"] == EXEC_BRANCH
    assert result.payload["conflicted_paths"] == ["code.py"]
    assert context.stage_results["execute"] is result
    review = run_review_stage(context)
    assert review.stage == FlowStage.REVIEW
    assert "Cannot run Review stage" not in (review.error or "")


def test_stage_ignores_other_statuses(tmp_path: Path) -> None:
    context = stage_context(tmp_path)
    for row in (None, {"status": "success"}, {"status": "failure"}):
        assert sync_conflict_result(context, "t", row, {"execution_branch": EXEC_BRANCH}) is None
