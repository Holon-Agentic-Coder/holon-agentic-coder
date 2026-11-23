"""The Evaluated Commit SHA skips Stage 5's own calibration commits and keeps every other commit (throwaway repos)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sandbox_executor.calibration import is_calibration_stale
from sandbox_executor.calibration_evaluated_sha import evaluated_code_commit, is_calibration_commit

REPORT = "plans/I-1-x/P-2-x_calibration.md"
LEDGER = "holon-knowledge/ledger/executions.jsonl"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def commit(repo: Path, message: str, *paths: str) -> str:
    for rel in paths:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{message}\n")
    git(repo, "add", "--", *paths)
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "base", "base.txt")
    git(tmp_path, "checkout", "-qb", "exec")
    return tmp_path


def calibrate(repo: Path) -> None:
    """The three commits a Stage 5 run adds: report and ACT-CAL row, reconciliation rows, promoted knowledge."""
    commit(repo, "chore(calibration): add calibration report for P-2-x", REPORT, LEDGER)
    commit(repo, "chore(calibration): record stage 5 reconciliation", LEDGER)
    commit(repo, "chore(calibration): record stage 5 promoted knowledge and wisdom", "holon-knowledge/kb/k.json")


def test_one_calibration_commit_on_top_is_skipped(repo: Path) -> None:
    code = commit(repo, "feat: code", "src/a.py")
    commit(repo, "chore(calibration): add calibration report for P-2-x", REPORT, LEDGER)
    assert evaluated_code_commit("HEAD", str(repo)) == code


def test_a_full_stage5_run_on_top_is_skipped(repo: Path) -> None:
    code = commit(repo, "feat: code", "src/a.py")
    calibrate(repo)
    assert evaluated_code_commit("HEAD", str(repo)) == code


def test_review_ledger_rows_after_the_code_are_the_approved_head(repo: Path) -> None:
    """The conflict gate's ``chore(review)`` ledger commit before Stage 5 is part of the approved head (PR #249)."""
    commit(repo, "feat: code", "src/a.py")
    review_rows = commit(repo, "chore(review): record review ledger rows (before Stage 5)", LEDGER)
    calibrate(repo)
    assert evaluated_code_commit("HEAD", str(repo)) == review_rows


def test_a_merge_of_main_is_returned(repo: Path) -> None:
    commit(repo, "feat: code", "src/a.py")
    git(repo, "checkout", "-q", "main")
    commit(repo, "main moves", "main.txt")
    git(repo, "checkout", "-q", "exec")
    git(repo, "merge", "-q", "--no-edit", "main")
    merge = git(repo, "rev-parse", "HEAD")
    assert evaluated_code_commit("HEAD", str(repo)) == merge
    calibrate(repo)
    assert evaluated_code_commit("HEAD", str(repo)) == merge


def test_no_calibration_commit_on_top_returns_the_head(repo: Path) -> None:
    head = commit(repo, "feat: code", "src/a.py")
    assert evaluated_code_commit("HEAD", str(repo)) == head


def test_a_calibration_subject_touching_code_counts_as_code(repo: Path) -> None:
    mixed = commit(repo, "chore(calibration): sneaks in code", REPORT, "src/a.py")
    assert not is_calibration_commit(str(repo), mixed)
    assert evaluated_code_commit("HEAD", str(repo)) == mixed


def test_only_calibration_commits_return_the_head(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    head = commit(tmp_path, "chore(calibration): add calibration report for P-2-x", REPORT)
    assert evaluated_code_commit("HEAD", str(tmp_path)) == head


def test_unknown_head_returns_none(repo: Path) -> None:
    assert evaluated_code_commit("refs/heads/missing", str(repo)) is None


def test_a_report_is_fresh_on_its_own_calibration_commits(repo: Path) -> None:
    code = commit(repo, "feat: code", "src/a.py")
    calibrate(repo)
    report = f"- **Evaluated Commit SHA:** `{code}`\n"
    assert is_calibration_stale(report, target_worktree=str(repo))[0] is False
    commit(repo, "feat: more code", "src/b.py")
    assert is_calibration_stale(report, target_worktree=str(repo))[0] is True
