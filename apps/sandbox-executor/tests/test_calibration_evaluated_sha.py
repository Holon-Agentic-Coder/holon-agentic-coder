"""The Evaluated Commit SHA skips Stage 5's own calibration commits and keeps every other commit (throwaway repos)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from sandbox_executor.calibration import is_calibration_stale
from sandbox_executor.calibration_evaluated_sha import (
    evaluated_code_commit,
    is_calibration_commit,
    is_ledger_only_commit,
)

from tests.calibrate_scenarios import build_evaluated_sha_calibrated_repo, build_evaluated_sha_repo, own_path_copy
from tests.repo_templates import shared_template

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


@pytest.fixture(scope="session")
def evaluated_sha_repo_template(tmp_path_factory: pytest.TempPathFactory, hermetic_git_config: str) -> Path:
    root, _ = shared_template(tmp_path_factory, "calibration-evaluated-sha-repo", build_evaluated_sha_repo)
    return root


@pytest.fixture(scope="session")
def evaluated_sha_calibrated_template(
    tmp_path_factory: pytest.TempPathFactory, hermetic_git_config: str
) -> SimpleNamespace:
    root, built = shared_template(
        tmp_path_factory,
        "calibration-evaluated-sha-calibrated",
        lambda r: build_evaluated_sha_calibrated_repo(r, REPORT, LEDGER),
    )
    return SimpleNamespace(root=root, code=built.code)


@pytest.fixture
def repo(evaluated_sha_repo_template: Path, tmp_path: Path) -> Path:
    return own_path_copy(evaluated_sha_repo_template, tmp_path)


@pytest.fixture
def calibrated_repo(evaluated_sha_calibrated_template: SimpleNamespace, tmp_path: Path) -> tuple[Path, str]:
    return own_path_copy(evaluated_sha_calibrated_template.root, tmp_path), evaluated_sha_calibrated_template.code


def calibrate(repo: Path) -> None:
    """The three commits a Stage 5 run adds: report and ACT-CAL row, reconciliation rows, promoted knowledge."""
    commit(repo, "chore(calibration): add calibration report for P-2-x", REPORT, LEDGER)
    commit(repo, "chore(calibration): record stage 5 reconciliation", LEDGER)
    commit(repo, "chore(calibration): record stage 5 promoted knowledge and wisdom", "holon-knowledge/kb/k.json")


def test_one_calibration_commit_on_top_is_skipped(repo: Path) -> None:
    code = commit(repo, "feat: code", "src/a.py")
    commit(repo, "chore(calibration): add calibration report for P-2-x", REPORT, LEDGER)
    assert evaluated_code_commit("HEAD", str(repo)) == code


def test_a_full_stage5_run_on_top_is_skipped(calibrated_repo: tuple[Path, str]) -> None:
    repo, code = calibrated_repo
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


def test_a_report_is_fresh_on_its_own_calibration_commits(calibrated_repo: tuple[Path, str]) -> None:
    repo, code = calibrated_repo
    report = f"- **Evaluated Commit SHA:** `{code}`\n"
    assert is_calibration_stale(report, target_worktree=str(repo))[0] is False
    commit(repo, "feat: more code", "src/b.py")
    assert is_calibration_stale(report, target_worktree=str(repo))[0] is True


def test_a_commit_that_only_appends_to_a_ledger_is_ledger_only(repo: Path) -> None:
    review_rows = commit(repo, "chore(review): record review rows", LEDGER, "holon-knowledge/ledger/reviews.jsonl")
    assert is_ledger_only_commit(str(repo), review_rows)
    assert is_ledger_only_commit(str(repo), commit(repo, "any subject", LEDGER))  # path-based, no subject required


def test_a_commit_with_a_ledger_file_and_a_code_file_is_not_ledger_only(repo: Path) -> None:
    assert not is_ledger_only_commit(str(repo), commit(repo, "chore(review): rows and a fix", LEDGER, "src/a.py"))


def test_a_merge_commit_is_not_ledger_only(repo: Path) -> None:
    commit(repo, "chore(review): record review rows", LEDGER)
    git(repo, "checkout", "-q", "main")
    commit(repo, "chore: main ledger", "holon-knowledge/ledger/actions.jsonl")
    git(repo, "checkout", "-q", "exec")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge main", "main")
    assert not is_ledger_only_commit(str(repo), "HEAD")


def test_a_commit_under_holon_knowledge_but_outside_the_ledger_is_not_ledger_only(repo: Path) -> None:
    assert not is_ledger_only_commit(str(repo), commit(repo, "chore: knowledge", "holon-knowledge/kb/k.json"))
    assert not is_ledger_only_commit(str(repo), commit(repo, "chore: lookalike", "holon-knowledge/ledger-notes.md"))


def test_an_empty_commit_and_an_unknown_commit_are_not_ledger_only(repo: Path) -> None:
    git(repo, "commit", "-q", "--allow-empty", "-m", "chore(review): nothing")
    assert not is_ledger_only_commit(str(repo), "HEAD")
    assert not is_ledger_only_commit(str(repo), "0" * 40)
