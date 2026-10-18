"""Hermetic unit tests for scripts/pre_commit_check.py (Bean 0110)."""

from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock, patch

_REPO_ROOT = str(pathlib.Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.pre_commit_check import (  # noqa: E402
    get_git_files,
    is_binary_file,
    is_scratch_file,
    is_whitelisted_for_content_scan,
    redact_token,
    run_pre_commit_check,
    scan_file_for_secrets,
)


class TestPreCommitCheck:
    """Test suite for pre-commit credential leakage and scratch file hygiene checks."""

    def test_redact_token(self) -> None:
        """Verify token redaction preserves prefix and obscures sensitive characters."""
        short = "12345"
        assert redact_token(short) == "[REDACTED]"

        google_key = "AIzaSyD-1234567890abcdefghijklmnopqrst"
        redacted = redact_token(google_key)
        assert redacted.startswith("AIzaSy")
        assert "[REDACTED:" in redacted
        assert "1234567890abcdefghijklmnopqrst" not in redacted

    def test_scratch_file_patterns(self) -> None:
        """Verify scratch file detection blocks temporary, log, and index paths."""
        blocked_paths = [
            "tmp/scratch.py",
            "foo/bar/tmp/debug.txt",
            "output.tmp",
            "build/test.tmp",
            "server.log",
            "logs/agent.log",
            ".index/cache.db",
            "holon-knowledge/.index/action_index.db",
        ]
        for p in blocked_paths:
            assert is_scratch_file(p) is True, f"Expected {p} to be blocked as scratch file"

        allowed_paths = [
            "src/module.py",
            "apps/sandbox-executor/src/sandbox_executor/flow.py",
            "docs/safety.md",
            "Makefile",
            "pyproject.toml",
        ]
        for p in allowed_paths:
            assert is_scratch_file(p) is False, f"Expected {p} to be allowed"

    def test_secret_patterns_detection(self, tmp_path: pathlib.Path) -> None:
        """Verify detection of Google AI, GitHub PAT, OpenAI, and Anthropic keys."""
        test_cases = [
            ("google.txt", "AIzaSyD-sampleKey1234567890abcdefghij", "Google AI API Key"),
            ("gh_classic.txt", "ghp_1234567890abcdefghijklmnopqrstuvwx", "GitHub PAT (Classic)"),
            (
                "gh_fine.txt",
                "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ddeeff",
                "GitHub PAT (Fine-Grained)",
            ),
            ("openai.txt", "sk-proj-1234567890abcdefghijklmnopqrstuv", "OpenAI API Key"),
            ("anthropic.txt", "sk-ant-api03-1234567890abcdefghijklmnopqrst", "Anthropic API Key"),
        ]

        for filename, token, expected_rule in test_cases:
            fpath = tmp_path / filename
            fpath.write_text(f"KEY = '{token}'\n", encoding="utf-8")
            findings = scan_file_for_secrets(str(fpath), display_path=filename, enforce_whitelist=False)
            assert len(findings) >= 1, f"Expected finding for {expected_rule} in {filename}"
            rules_found = [f.rule_name for f in findings]
            assert any(expected_rule in r for r in rules_found), f"Rule {expected_rule} not in {rules_found}"
            assert all(token not in f.redacted_token for f in findings)

    def test_clean_file_passes(self, tmp_path: pathlib.Path) -> None:
        """Verify that files without credentials pass scanning with 0 findings."""
        clean_file = tmp_path / "clean.py"
        clean_file.write_text(
            "def add(a: int, b: int) -> int:\n    return a + b\n",
            encoding="utf-8",
        )
        findings = scan_file_for_secrets(str(clean_file), display_path="clean.py", enforce_whitelist=False)
        assert len(findings) == 0

    def test_binary_file_skipped(self, tmp_path: pathlib.Path) -> None:
        """Verify binary files containing null bytes are safely skipped without decoding errors."""
        bin_file = tmp_path / "binary.dat"
        bin_file.write_bytes(b"some\x00data\x00AIzaSyD-sampleKey1234567890abcdefghij")
        assert is_binary_file(str(bin_file)) is True
        findings = scan_file_for_secrets(str(bin_file), enforce_whitelist=False)
        assert len(findings) == 0

    def test_whitelist_skips_content_scan(self) -> None:
        """Verify whitelisted test and script paths skip content regex scanning."""
        assert is_whitelisted_for_content_scan("tests/test_pre_commit_check.py") is True
        assert is_whitelisted_for_content_scan("scripts/pre_commit_check.py") is True
        assert is_whitelisted_for_content_scan(".agents/AGENTS.md") is True
        assert is_whitelisted_for_content_scan("src/sandbox_executor/flow.py") is False

    def test_run_pre_commit_check_staged_clean(self, tmp_path: pathlib.Path) -> None:
        """Verify run_pre_commit_check returns 0 on clean repository."""
        clean_file = tmp_path / "app.py"
        clean_file.write_text("print('hello')\n", encoding="utf-8")

        with patch("scripts.pre_commit_check.get_git_files", return_value=["app.py"]):
            exit_code = run_pre_commit_check(repo_dir=str(tmp_path), staged=True)
            assert exit_code == 0

    def test_run_pre_commit_check_detects_scratch_file(self, tmp_path: pathlib.Path) -> None:
        """Verify run_pre_commit_check returns 1 when scratch files are staged."""
        with patch("scripts.pre_commit_check.get_git_files", return_value=["tmp/scratch.py"]):
            exit_code = run_pre_commit_check(repo_dir=str(tmp_path), staged=True)
            assert exit_code == 1

    def test_run_pre_commit_check_detects_secret(self, tmp_path: pathlib.Path) -> None:
        """Verify run_pre_commit_check returns 1 when secret credentials are staged."""
        secret_file = tmp_path / "secret.env"
        secret_file.write_text("API_KEY=sk-proj-123456789012345678901234567890\n", encoding="utf-8")

        with patch("scripts.pre_commit_check.get_git_files", return_value=["secret.env"]):
            exit_code = run_pre_commit_check(repo_dir=str(tmp_path), staged=True, enforce_whitelist=False)
            assert exit_code == 1

    def test_run_pre_commit_check_all_files_mode(self, tmp_path: pathlib.Path) -> None:
        """Verify run_pre_commit_check correctly queries all tracked files with --all-files."""
        with patch("scripts.pre_commit_check.get_git_files", return_value=[]) as mock_get:
            exit_code = run_pre_commit_check(repo_dir=str(tmp_path), all_files=True)
            assert exit_code == 0
            mock_get.assert_called_once_with(str(tmp_path), mode="all-files")

    def test_get_git_files_subprocesses(self, tmp_path: pathlib.Path) -> None:
        """Verify get_git_files invokes appropriate git commands for staged and all-files."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="file1.py\nfile2.py\n")

            staged_files = get_git_files(str(tmp_path), mode="staged")
            assert staged_files == ["file1.py", "file2.py"]
            assert "--cached" in mock_run.call_args[0][0]

            all_files = get_git_files(str(tmp_path), mode="all-files")
            assert all_files == ["file1.py", "file2.py"]
            assert "ls-files" in mock_run.call_args[0][0]
