"""The suite-wide guard refuses unmocked ``gh`` writes and lets reads through."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.gh_write_guard import GhWriteBlockedError, gh_write_command


@pytest.fixture
def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put a ``gh`` first on PATH that records each invocation, so no real gh ever runs."""
    if sys.platform == "win32":
        pytest.skip("fake gh is a POSIX shell script")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh.log"
    script = bin_dir / "gh"
    script.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\necho read-ok\n', encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return log


@pytest.mark.parametrize(
    "argv",
    [
        ["gh", "pr", "comment", "95", "--body", "x"],
        ["gh", "pr", "review", "95", "--approve"],
        ["gh", "pr", "create", "--fill"],
        ["gh", "pr", "edit", "95", "--body-file", "b.md"],
        ["gh", "pr", "merge", "95", "--squash"],
        ["gh", "pr", "close", "95"],
        ["gh", "pr", "ready", "95"],
        ["gh", "-R", "o/r", "pr", "comment", "1"],
        ["/usr/local/bin/gh", "issue", "create", "--title", "t"],
        ["gh", "issue", "comment", "1", "--body", "x"],
        ["gh", "issue", "edit", "1"],
        ["gh", "repo", "edit", "--enable-auto-merge"],
        ["gh", "api", "-X", "POST", "repos/o/r/pulls/1/reviews"],
        ["gh", "api", "--method", "patch", "repos/o/r"],
        ["gh", "api", "--method=DELETE", "repos/o/r/git/refs/heads/x"],
        ["gh", "api", "-XPUT", "repos/o/r/pulls/1/merge"],
        ["gh", "api", "repos/o/r/issues/1/comments", "-f", "body=x"],
        ["gh", "api", "repos/o/r/issues/1/comments", "-F", "body=@b.md"],
        ["gh", "api", "graphql", "--raw-field", "query=mutation{}"],
        "gh pr comment 1 --body x",
    ],
)
def test_write_commands_are_classified(argv):
    assert gh_write_command(argv, shell=isinstance(argv, str)) is not None


@pytest.mark.parametrize(
    "argv",
    [
        ["gh", "pr", "view", "95", "--json", "headRefOid"],
        ["gh", "pr", "checks", "95", "--json", "bucket"],
        ["gh", "pr", "diff", "95"],
        ["gh", "auth", "token"],
        ["gh", "auth", "status"],
        ["gh", "--version"],
        ["gh", "-R", "o/r", "pr", "view", "1"],
        ["gh", "api", "repos/o/r/pulls/1"],
        ["gh", "api", "-X", "GET", "search/issues", "-f", "q=x"],
        ["git", "commit", "-m", "gh pr comment"],
        "gh pr view 1",
    ],
)
def test_read_and_non_gh_commands_are_not_classified(argv):
    assert gh_write_command(argv, shell=isinstance(argv, str)) is None


def test_guard_blocks_unmocked_write_and_fails_the_test(fake_gh: Path, gh_write_guard: list[str]):
    with pytest.raises(GhWriteBlockedError, match="gh pr comment"):
        subprocess.run(["gh", "pr", "comment", "95", "--body", "x"], capture_output=True, check=False)
    with pytest.raises(GhWriteBlockedError, match="gh api POST"):
        subprocess.check_output(["gh", "api", "repos/o/r/issues/1/comments", "-f", "body=x"])

    # The write never reached gh, and the guard recorded both so the teardown would fail this test.
    assert not fake_gh.exists()
    assert len(gh_write_guard) == 2
    gh_write_guard.clear()


def test_guard_lets_read_commands_through(fake_gh: Path, gh_write_guard: list[str]):
    proc = subprocess.run(["gh", "pr", "view", "95", "--json", "state"], capture_output=True, text=True, check=False)

    assert proc.returncode == 0
    assert proc.stdout.strip() == "read-ok"
    assert fake_gh.read_text(encoding="utf-8").strip() == "pr view 95 --json state"
    assert gh_write_guard == []


def test_mocked_subprocess_run_bypasses_the_guard(gh_write_guard: list[str]):
    from unittest.mock import patch

    with patch("subprocess.run") as mock_run:
        subprocess.run(["gh", "pr", "comment", "95", "--body", "x"])

    mock_run.assert_called_once()
    assert gh_write_guard == []
