"""Unit tests for centralised Git repository abstraction (Bean 0124)."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.git import (
    branch_exists,
    get_diff_numstat,
    get_head_commit,
    resolve_ref,
    run_git,
)


def test_run_git_success() -> None:
    """Verify run_git executes successfully and captures output."""
    res = run_git(["rev-parse", "--is-inside-work-tree"], check=True)
    assert res.returncode == 0
    assert res.stdout.strip() == "true"


def test_run_git_failure_raises() -> None:
    """Verify run_git raises RuntimeError with redacted output on failure when check=True."""
    with pytest.raises(RuntimeError) as exc_info:
        run_git(["rev-parse", "--verify", "refs/heads/nonexistent_test_branch_12345"], check=True)
    assert "Git command failed with exit code" in str(exc_info.value)
    assert "nonexistent_test_branch_12345" in str(exc_info.value)


def test_run_git_failure_no_check() -> None:
    """Verify run_git returns CompletedProcess without raising when check=False."""
    res = run_git(["rev-parse", "--verify", "refs/heads/nonexistent_test_branch_12345"], check=False)
    assert res.returncode != 0


def test_run_git_timeout() -> None:
    """Verify run_git raises TimeoutError on subprocess timeout."""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["git", "status"], timeout=0.1)):
        with pytest.raises(TimeoutError) as exc_info:
            run_git(["status"], timeout=1)
        assert "Git command timed out" in str(exc_info.value)


def test_resolve_ref_and_get_head_commit() -> None:
    """Verify resolve_ref resolves valid references and get_head_commit returns 40-char SHA."""
    head_sha = get_head_commit(".")
    assert head_sha is not None
    assert len(head_sha) == 40
    assert all(c in "0123456789abcdefABCDEF" for c in head_sha)

    # Empty and invalid refs
    assert resolve_ref(".", "") is None
    assert resolve_ref(".", "invalid_ref_that_does_not_exist_98765") is None


def test_branch_exists() -> None:
    """Verify branch_exists correctly detects existing and non-existing branches."""
    # Non-existing branch
    assert not branch_exists(".", "branch_never_created_987654321")

    # Local HEAD branch or main
    has_main = branch_exists(".", "main") or branch_exists(".", "HEAD")
    assert has_main is True


def test_get_diff_numstat_parsing() -> None:
    """Verify get_diff_numstat parses textual and binary numstats correctly."""
    mock_stdout = "10\t5\tfile1.py\n-\t-\tbinary.png\n2\t0\tdocs/README.md\n"
    with patch("sandbox_executor.git.run_git") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=mock_stdout)
        numstats = get_diff_numstat(".", "base", "head")

        assert len(numstats) == 3
        assert numstats[0] == (10, 5, "file1.py")
        assert numstats[1] == (-1, -1, "binary.png")
        assert numstats[2] == (2, 0, "docs/README.md")


def test_get_diff_numstat_error() -> None:
    """Verify get_diff_numstat gracefully returns empty list on Git failure."""
    with patch("sandbox_executor.git.run_git") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="fatal: bad revision")
        numstats = get_diff_numstat(".", "bad_base", "bad_head")
        assert numstats == []
