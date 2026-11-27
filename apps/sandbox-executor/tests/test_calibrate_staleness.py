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

from tests.calibrate_scenarios import (
    build_clean_merge_repos,
    build_predicate_repo,
    build_sync_repos,
    own_copy,
    own_path_copy,
    run_guarded_template,
)
from tests.repo_templates import shared_template
from tests.test_calibrate_rerun import (
    advance_main,
    commit_calibration,
    ledger_row,
    merge_main,
    pr258_history,
)
from tests.test_calibrate_sync import EXEC, commit_file, git


@pytest.fixture(scope="session")
def predicate_repo_template(tmp_path_factory: pytest.TempPathFactory, hermetic_git_config: str) -> Path:
    root, _ = shared_template(tmp_path_factory, "calibrate-predicate-repo", build_predicate_repo)
    return root


@pytest.fixture(scope="session")
def sync_repos_template(tmp_path_factory: pytest.TempPathFactory, hermetic_git_config: str) -> SimpleNamespace:
    root, built = shared_template(tmp_path_factory, "calibrate-sync-repos", build_sync_repos)
    return SimpleNamespace(root=root, repos=built)


@pytest.fixture
def repo(predicate_repo_template: Path, tmp_path: Path) -> Path:
    return own_path_copy(predicate_repo_template, tmp_path, "predicate")


@pytest.fixture
def repos(sync_repos_template: SimpleNamespace, tmp_path: Path) -> SimpleNamespace:
    return own_copy(sync_repos_template, tmp_path)


@pytest.fixture
def pr258_repos_template(
    sync_repos_template: SimpleNamespace,
    tmp_path_factory: pytest.TempPathFactory,
    gh_write_guard: list[str],
    network_guard_fixture: list[str],
) -> SimpleNamespace:
    def build(root: Path) -> SimpleNamespace:
        own = own_copy(sync_repos_template, root)
        return SimpleNamespace(repos=own, history=pr258_history(own))

    root, built = run_guarded_template(
        tmp_path_factory, "pr258-repos-template", build, (gh_write_guard, network_guard_fixture)
    )
    return SimpleNamespace(root=root, repos=built.repos, history=built.history)


@pytest.fixture
def pr258_repos(pr258_repos_template: SimpleNamespace, tmp_path: Path) -> SimpleNamespace:
    own = own_copy(pr258_repos_template, tmp_path)
    own.history = pr258_repos_template.history
    return own


@pytest.fixture
def clean_merge_repos_template(
    tmp_path_factory: pytest.TempPathFactory,
    gh_write_guard: list[str],
    network_guard_fixture: list[str],
) -> SimpleNamespace:
    root, built = run_guarded_template(
        tmp_path_factory,
        "calibrate-clean-merge-repos",
        build_clean_merge_repos,
        (gh_write_guard, network_guard_fixture),
    )
    return SimpleNamespace(root=root, repos=built.repos, evaluated=built.evaluated)


@pytest.fixture
def clean_merge_repos(clean_merge_repos_template: SimpleNamespace, tmp_path: Path) -> SimpleNamespace:
    own = own_copy(clean_merge_repos_template, tmp_path)
    own.evaluated = clean_merge_repos_template.evaluated
    return own


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


def test_the_pr_own_fingerprint_is_unchanged_across_a_pr258_shaped_history(pr258_repos: SimpleNamespace) -> None:
    history = pr258_repos.history
    other = str(pr258_repos.other)
    assert len(git(pr258_repos.other, "rev-list", "--parents", "-n", "1", history.merge).split()) == 3
    before = approval_fingerprint_of(other, history.evaluated, "main")
    assert before is not None
    assert approval_fingerprint_of(other, history.head, "main") == before
    advance_main(pr258_repos, "main-later.txt")
    assert approval_fingerprint_of(other, history.evaluated, "main") == before
    assert approval_fingerprint_of(other, history.head, "main") == before


def predicate(repos: SimpleNamespace, evaluated: str, target: str | None = "main") -> HeadMoveCheck:
    """The staleness verdict in the second clone, from ``evaluated`` to its checked-out execution head."""
    return head_move_since_calibration(str(repos.other), evaluated, git(repos.other, "rev-parse", "HEAD"), target)


def test_a_clean_merge_of_a_target_that_touched_other_files_is_unmoved(clean_merge_repos: SimpleNamespace) -> None:
    check = predicate(clean_merge_repos, clean_merge_repos.evaluated)
    assert check.moved is False
    assert (
        check.reason == "the pull request's own change is unchanged (1 commit(s) after the evaluated commit discounted)"
    )


def test_a_merge_with_ledger_only_conflicts_is_unmoved_and_never_counts_the_targets_commits(
    pr258_repos: SimpleNamespace,
) -> None:
    history = pr258_repos.history
    in_range = git(pr258_repos.other, "rev-list", "--count", f"{history.evaluated}..{history.head}")
    check = predicate(pr258_repos, history.evaluated)
    assert check.moved is False
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


def test_a_merge_with_no_target_branch_given_is_a_move(clean_merge_repos: SimpleNamespace) -> None:
    check = predicate(clean_merge_repos, clean_merge_repos.evaluated, target=None)
    assert check.moved is True
    assert "could not be compared" in check.reason


def test_report_staleness_reads_a_pr258_shaped_history_as_unmoved(pr258_repos: SimpleNamespace) -> None:
    history = pr258_repos.history
    ctx = FlowContext(target_worktree=str(pr258_repos.host), execution_branch=EXEC, agent="a", model="m")
    ctx.intent_data = {"target_branch": "main"}
    check = report_staleness(ctx)
    assert (check.moved, check.evaluated_sha, check.current_head) == (False, history.evaluated, history.head)
    assert "own change is unchanged" in check.reason
