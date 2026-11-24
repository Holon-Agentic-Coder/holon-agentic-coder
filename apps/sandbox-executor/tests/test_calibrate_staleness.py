"""Calibration report staleness predicate: PR-own change moves vs target merges, ledger rows and calibration commits.
Hermetic: real commits in temp repos, no network, no Docker, no model call."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.calibrate_rerun import (
    HeadMoveCheck,
    head_move_since_calibration,
    report_staleness,
)
from sandbox_executor.flow.review.approval_fingerprint import approval_fingerprint_of

from tests.test_calibrate_rerun import (
    advance_main,
    commit_calibration,
    ledger_row,
    merge_main,
    pr258_history,
)
from tests.test_calibrate_sync import EXEC, commit_file, git, make_repos


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "predicate"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    commit_file(r, "base.txt")
    return r


@pytest.fixture
def repos(tmp_path: Path) -> SimpleNamespace:
    return make_repos(tmp_path)


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
    assert check.reason == "only calibration and ledger commits landed after the evaluated commit"


def test_a_foreign_commit_between_the_calibration_commits_is_a_move(repo: Path) -> None:
    evaluated = git(repo, "rev-parse", "HEAD")
    commit_calibration(repo)
    commit_file(repo, "resolver-fix.txt")
    head = git(repo, "rev-parse", "HEAD")
    check = head_move_since_calibration(str(repo), evaluated, head)
    assert check.moved is True
    assert check.reason.startswith(
        "1 commit(s) landed after the evaluated commit"
    )  # the calibration commit is not counted


def test_a_calibration_subject_on_a_foreign_path_is_a_move(repo: Path) -> None:
    evaluated = git(repo, "rev-parse", "HEAD")
    commit_calibration(repo, rel="src/sneaky.py")
    assert head_move_since_calibration(str(repo), evaluated, git(repo, "rev-parse", "HEAD")).moved is True


def test_a_merge_is_a_move_when_the_pr_own_change_cannot_be_shown_unchanged(repo: Path) -> None:
    evaluated = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-qb", "side")
    commit_calibration(repo)
    git(repo, "checkout", "-q", "main")
    commit_calibration(repo, rel="holon-knowledge/ledger/executions.jsonl", subject="chore(calibration): record rows")
    git(repo, "merge", "-q", "--no-ff", "-m", "chore(calibration): merge side", "side")
    check = head_move_since_calibration(str(repo), evaluated, git(repo, "rev-parse", "HEAD"))  # no target ref
    assert check.moved is True
    assert check.reason.startswith("1 commit(s) landed") and "could not be compared" in check.reason


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


def test_the_pr_own_fingerprint_is_unchanged_across_a_pr258_shaped_history(repos: SimpleNamespace) -> None:
    """The hypothesis of the staleness rule: the approval fingerprint of E equals that of the merged head."""
    history = pr258_history(repos)
    other = str(repos.other)
    assert len(git(repos.other, "rev-list", "--parents", "-n", "1", history.merge).split()) == 3  # a real merge
    before = approval_fingerprint_of(other, history.evaluated, "main")
    assert before is not None
    assert approval_fingerprint_of(other, history.head, "main") == before
    advance_main(repos, "main-later.txt")  # the target ref moves past the merged commit
    assert approval_fingerprint_of(other, history.evaluated, "main") == before
    assert approval_fingerprint_of(other, history.head, "main") == before


def predicate(repos: SimpleNamespace, evaluated: str, target: str | None = "main") -> HeadMoveCheck:
    """The staleness verdict in the second clone, from ``evaluated`` to its checked-out execution head."""
    return head_move_since_calibration(str(repos.other), evaluated, git(repos.other, "rev-parse", "HEAD"), target)


def test_a_clean_merge_of_a_target_that_touched_other_files_is_unmoved(repos: SimpleNamespace) -> None:
    evaluated = commit_file(repos.other, "feature.py", "x = 1\n")
    advance_main(repos, "main-only.txt")
    merge_main(repos)
    check = predicate(repos, evaluated)
    assert check.moved is False
    assert (
        check.reason == "the pull request's own change is unchanged (1 commit(s) after the evaluated commit discounted)"
    )


def test_a_merge_with_ledger_only_conflicts_is_unmoved_and_never_counts_the_targets_commits(
    repos: SimpleNamespace,
) -> None:
    history = pr258_history(repos)
    in_range = git(repos.other, "rev-list", "--count", f"{history.evaluated}..{history.head}")
    check = predicate(repos, history.evaluated)
    assert check.moved is False
    # First-parent only: the report commit, the PR's ledger row, the merge, the review row and the knowledge commit.
    assert (in_range, check.reason) == (
        "7",
        "the pull request's own change is unchanged (5 commit(s) after the evaluated commit discounted)",
    )


def test_a_merge_that_brings_a_target_change_to_a_pr_file_is_a_move(repos: SimpleNamespace) -> None:
    lines = ["a", "b", "c", "d", "e", "f", "g", "h"]
    advance_main(repos, "shared.py", "\n".join(lines) + "\n")
    merge_main(repos)  # the execution branch now holds the shared file
    evaluated = commit_file(repos.other, "shared.py", "\n".join(["from the pr", *lines[1:]]) + "\n")
    advance_main(repos, "shared.py", "\n".join([*lines[:-1], "from main"]) + "\n")
    merge_main(repos)  # clean: the two edits do not overlap, but the PR's file is no longer what the report saw
    check = predicate(repos, evaluated)
    assert check.moved is True
    assert check.reason == "1 commit(s) after the evaluated commit changed the pull request's own change"


def test_a_review_ledger_row_commit_after_the_evaluated_commit_is_unmoved(repos: SimpleNamespace) -> None:
    evaluated = commit_file(repos.other, "feature.py")
    ledger_row(repos.other, "E-review")
    ledger_row(repos.other, "E-engine", subject="record the execution")  # path-based: no subject is required
    check = predicate(repos, evaluated, target=None)
    assert (check.moved, check.reason) == (
        False,
        "only calibration and ledger commits landed after the evaluated commit",
    )


def test_a_calibration_commit_after_the_evaluated_commit_is_unmoved(repos: SimpleNamespace) -> None:
    evaluated = commit_file(repos.other, "feature.py")
    commit_calibration(repos.other)
    assert predicate(repos, evaluated).moved is False


def test_a_code_push_is_a_move_and_the_reason_counts_exactly_one_commit(repos: SimpleNamespace) -> None:
    evaluated = commit_file(repos.other, "feature.py")
    commit_calibration(repos.other)
    ledger_row(repos.other, "E-review")
    commit_file(repos.other, "resolver-fix.py")
    commit_calibration(repos.other, rel="holon-knowledge/kb/k.json")
    check = predicate(repos, evaluated)
    assert check.moved is True
    assert check.reason == "1 commit(s) after the evaluated commit changed the pull request's own change"


def test_a_merge_with_no_target_branch_given_is_a_move(repos: SimpleNamespace) -> None:
    evaluated = commit_file(repos.other, "feature.py", "x = 1\n")
    advance_main(repos, "main-only.txt")
    merge_main(repos)
    check = predicate(repos, evaluated, target=None)
    assert check.moved is True
    assert "could not be compared" in check.reason


def test_report_staleness_reads_a_pr258_shaped_history_as_unmoved(repos: SimpleNamespace) -> None:
    history = pr258_history(repos)
    ctx = FlowContext(target_worktree=str(repos.host), execution_branch=EXEC, agent="a", model="m")
    ctx.intent_data = {"target_branch": "main"}
    check = report_staleness(ctx)  # fetches the execution branch and the target into the host worktree
    assert (check.moved, check.evaluated_sha, check.current_head) == (False, history.evaluated, history.head)
    assert "own change is unchanged" in check.reason
