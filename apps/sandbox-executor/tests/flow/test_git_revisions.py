"""Branch-name-to-revision qualification on real git repositories: local bare origin only, no network."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sandbox_executor.git_revisions import qualify_branch_revision, revision_args

LOCAL = "I-1-root--1-child/P-2-ab12cd34-a-m/E-3-ef56ab78-a-m/_"
REMOTE_ONLY = "I-1-root--1-child/P-2-ab12cd34-a-m/_"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> SimpleNamespace:
    """A host clone with a local-only nested branch and a nested branch that exists only as a remote-tracking ref."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    host = tmp_path / "host"
    git(tmp_path, "clone", "-q", str(origin), str(host))
    git(host, "checkout", "-qb", "main")
    git(host, "commit", "-q", "--allow-empty", "-m", "base")
    git(host, "push", "-q", "origin", "main", f"main:refs/heads/{REMOTE_ONLY}")
    git(host, "fetch", "-q", "origin")
    git(host, "branch", LOCAL)
    return SimpleNamespace(host=host, sha=git(host, "rev-parse", "HEAD"))


def test_local_nested_branch_qualifies_to_refs_heads(repo: SimpleNamespace) -> None:
    assert qualify_branch_revision(str(repo.host), LOCAL) == f"refs/heads/{LOCAL}"


def test_remote_only_branch_qualifies_to_the_tracking_ref(repo: SimpleNamespace) -> None:
    assert qualify_branch_revision(str(repo.host), REMOTE_ONLY) == f"refs/remotes/origin/{REMOTE_ONLY}"
    assert qualify_branch_revision(str(repo.host), f"origin/{REMOTE_ONLY}") == f"refs/remotes/origin/{REMOTE_ONLY}"


@pytest.mark.parametrize("prefix", ["refs/heads/", "  "])
def test_qualified_or_padded_input_is_idempotent(repo: SimpleNamespace, prefix: str) -> None:
    assert qualify_branch_revision(str(repo.host), f"{prefix}{LOCAL}") == f"refs/heads/{LOCAL}"


def test_commit_sha_is_returned_unchanged(repo: SimpleNamespace) -> None:
    assert qualify_branch_revision(str(repo.host), repo.sha) == repo.sha
    assert qualify_branch_revision(str(repo.host), repo.sha[:12]) == repo.sha[:12]


@pytest.mark.parametrize("name", ["I-9-gone/P-9-gone/_", "", "   "])
def test_missing_branch_returns_none(repo: SimpleNamespace, name: str) -> None:
    assert qualify_branch_revision(str(repo.host), name) is None


def test_branch_name_that_is_also_a_worktree_path_still_resolves_as_a_ref(repo: SimpleNamespace) -> None:
    path = repo.host / LOCAL
    path.parent.mkdir(parents=True)
    path.write_text("not a ref\n", encoding="utf-8")
    assert qualify_branch_revision(str(repo.host), LOCAL) == f"refs/heads/{LOCAL}"


def test_the_callers_git_runner_is_used() -> None:
    run = MagicMock(return_value=MagicMock(returncode=0))
    assert qualify_branch_revision("/nowhere", LOCAL, run=run) == f"refs/heads/{LOCAL}"
    run.assert_called_once()
    assert run.call_args.args[0] == ["rev-parse", "--verify", "--quiet", f"refs/heads/{LOCAL}^{{commit}}"]


def test_revision_args_end_with_the_path_separator() -> None:
    assert revision_args("refs/heads/a", "refs/heads/b") == ["refs/heads/a", "refs/heads/b", "--"]
    assert revision_args() == ["--"]
