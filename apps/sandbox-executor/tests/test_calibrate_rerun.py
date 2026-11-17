"""Stage 5 rerun on a moved head: predicate, bounded rerun, idempotence and the stale signal. Hermetic: real commits
in temp repos, no network, no Docker, no model call."""

from __future__ import annotations

from pathlib import Path

import pytest
from sandbox_executor.flow.calibrate_rerun import (
    HeadMoveCheck,
    head_move_since_calibration,
)

from tests.test_calibrate_sync import commit_file, git

PLAN_ID = "P-2-y"
CAL_SUBJECT = f"chore(calibration): add calibration report for {PLAN_ID}"
CAL_REPORT_REL = f"plans/{PLAN_ID}_calibration.md"


def commit_calibration(repo: Path, rel: str = CAL_REPORT_REL, subject: str = CAL_SUBJECT, text: str = "report\n") -> str:
    """A commit in Stage 5's own shape: a calibration subject touching only calibration paths."""
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    git(repo, "add", rel)
    git(repo, "commit", "-qm", subject)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "predicate"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    commit_file(r, "base.txt")
    return r


def test_equal_heads_are_unmoved(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD")
    check = head_move_since_calibration(str(repo), head, head)
    assert check == HeadMoveCheck(False, head, head, "the head is the evaluated commit")


def test_an_abbreviated_evaluated_sha_still_matches_the_head(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD")
    assert head_move_since_calibration(str(repo), head[:12], head).moved is False


def test_calibration_commits_alone_are_not_a_move(repo: Path) -> None:
    evaluated = git(repo, "rev-parse", "HEAD")
    commit_calibration(repo)
    commit_calibration(repo, rel="holon-knowledge/ledger/executions.jsonl", subject="chore(calibration): record rows")
    head = git(repo, "rev-parse", "HEAD")
    check = head_move_since_calibration(str(repo), evaluated, head)
    assert check.moved is False
    assert "only calibration commits" in check.reason


def test_a_foreign_commit_between_the_calibration_commits_is_a_move(repo: Path) -> None:
    evaluated = git(repo, "rev-parse", "HEAD")
    commit_calibration(repo)
    commit_file(repo, "resolver-fix.txt")
    head = git(repo, "rev-parse", "HEAD")
    check = head_move_since_calibration(str(repo), evaluated, head)
    assert check.moved is True
    assert "non-calibration commit" in check.reason


def test_a_calibration_subject_on_a_foreign_path_is_a_move(repo: Path) -> None:
    evaluated = git(repo, "rev-parse", "HEAD")
    commit_calibration(repo, rel="src/sneaky.py")
    assert head_move_since_calibration(str(repo), evaluated, git(repo, "rev-parse", "HEAD")).moved is True


def test_a_merge_commit_is_always_a_move(repo: Path) -> None:
    evaluated = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-qb", "side")
    commit_calibration(repo)
    git(repo, "checkout", "-q", "main")
    commit_calibration(repo, rel="holon-knowledge/ledger/executions.jsonl", subject="chore(calibration): record rows")
    git(repo, "merge", "-q", "--no-ff", "-m", "chore(calibration): merge side", "side")
    assert head_move_since_calibration(str(repo), evaluated, git(repo, "rev-parse", "HEAD")).moved is True


def test_a_non_ancestor_evaluated_sha_is_a_move(repo: Path) -> None:
    git(repo, "checkout", "-qb", "rewritten")
    evaluated = commit_file(repo, "old-history.txt")
    git(repo, "checkout", "-q", "main")
    head = commit_file(repo, "new-history.txt")
    check = head_move_since_calibration(str(repo), evaluated, head)
    assert check.moved is True
    assert "not an ancestor" in check.reason


def test_an_unknown_sha_is_unmoved_with_the_gap_named(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD")
    check = head_move_since_calibration(str(repo), "", head)
    assert (check.moved, "unknown" in check.reason) == (False, True)
