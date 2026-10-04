"""Hermetic unit tests for sandbox_executor.formatting (prettier convergence utility)."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox_executor.formatting import (
    DEFAULT_MAX_PASSES,
    converge_prettier,
    is_prettier_available,
)


class TestFormattingConvergence(unittest.TestCase):
    """Test suite for prettier convergence utility."""

    def test_is_prettier_available_true(self):
        with patch("shutil.which", return_value="/usr/bin/npx"):
            self.assertTrue(is_prettier_available())

    def test_is_prettier_available_false(self):
        with patch("shutil.which", return_value=None):
            self.assertFalse(is_prettier_available())

    def test_converge_prettier_empty_file_list(self):
        with patch("subprocess.run") as mock_run:
            self.assertTrue(converge_prettier([]))
            mock_run.assert_not_called()

    def test_converge_prettier_nonexistent_files(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch("subprocess.run") as mock_run:
            self.assertTrue(converge_prettier(["nonexistent.md"], repo_dir=tmp_dir))
            mock_run.assert_not_called()

    def test_converge_prettier_clean_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = os.path.join(tmp_dir, "test.md")
            with open(file_path, "w") as f:
                f.write("# Clean Markdown\n\nContent here.\n")

            with (
                patch("sandbox_executor.formatting.is_prettier_available", return_value=True),
                patch("subprocess.run") as mock_run,
            ):
                # Pass 1: write returns 0, check returns 0
                mock_run.side_effect = [
                    MagicMock(returncode=0, stdout="", stderr=""),
                    MagicMock(returncode=0, stdout="", stderr=""),
                ]

                result = converge_prettier(["test.md"], repo_dir=tmp_dir)
                self.assertTrue(result)
                self.assertEqual(mock_run.call_count, 2)
                # First call was --write, second was --check
                self.assertIn("--write", mock_run.call_args_list[0][0][0])
                self.assertIn("--check", mock_run.call_args_list[1][0][0])

    def test_converge_prettier_oscillating_markdown(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "oscillating.md"
            file_path.write_text("# Unformatted Markdown\n")

            with (
                patch("sandbox_executor.formatting.is_prettier_available", return_value=True),
                patch("subprocess.run") as mock_run,
            ):
                # Pass 1: write -> 0, check -> 1 (not yet converged)
                # Pass 2: write -> 0, check -> 0 (converged)
                mock_run.side_effect = [
                    MagicMock(returncode=0, stdout="", stderr=""),
                    MagicMock(returncode=1, stdout="", stderr=""),
                    MagicMock(returncode=0, stdout="", stderr=""),
                    MagicMock(returncode=0, stdout="", stderr=""),
                ]

                result = converge_prettier([file_path], repo_dir=tmp_dir, max_passes=3)
                self.assertTrue(result)
                self.assertEqual(mock_run.call_count, 4)

    def test_converge_prettier_exceeds_max_passes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = os.path.join(tmp_dir, "unstable.md")
            with open(file_path, "w") as f:
                f.write("# Unstable\n")

            with (
                patch("sandbox_executor.formatting.is_prettier_available", return_value=True),
                patch("subprocess.run") as mock_run,
            ):
                # In each pass: write -> 0, check -> 1
                mock_run.side_effect = [
                    MagicMock(returncode=0, stdout="", stderr=""),
                    MagicMock(returncode=1, stdout="", stderr=""),
                ] * DEFAULT_MAX_PASSES

                result = converge_prettier(["unstable.md"], repo_dir=tmp_dir, max_passes=DEFAULT_MAX_PASSES)
                self.assertFalse(result)
                self.assertEqual(mock_run.call_count, 2 * DEFAULT_MAX_PASSES)

    def test_converge_prettier_npx_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = os.path.join(tmp_dir, "test.md")
            with open(file_path, "w") as f:
                f.write("# Hello\n")

            with (
                patch("sandbox_executor.formatting.is_prettier_available", return_value=False),
                patch("subprocess.run") as mock_run,
            ):
                result = converge_prettier(["test.md"], repo_dir=tmp_dir)
                self.assertFalse(result)
                mock_run.assert_not_called()

    def test_converge_prettier_write_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = os.path.join(tmp_dir, "syntax_error.md")
            with open(file_path, "w") as f:
                f.write("# Invalid\n")

            with (
                patch("sandbox_executor.formatting.is_prettier_available", return_value=True),
                patch("subprocess.run") as mock_run,
            ):
                # Write command fails
                mock_run.return_value = MagicMock(returncode=2, stdout="", stderr="SyntaxError")

                result = converge_prettier(["syntax_error.md"], repo_dir=tmp_dir)
                self.assertFalse(result)
                self.assertEqual(mock_run.call_count, 1)

    def test_converge_prettier_timeout_handling(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = os.path.join(tmp_dir, "slow.md")
            with open(file_path, "w") as f:
                f.write("# Slow\n")

            with (
                patch("sandbox_executor.formatting.is_prettier_available", return_value=True),
                patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="npx", timeout=5)),
            ):
                result = converge_prettier(["slow.md"], repo_dir=tmp_dir, timeout_seconds=5)
                self.assertFalse(result)

    def test_converge_prettier_unexpected_exception(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = os.path.join(tmp_dir, "err.md")
            with open(file_path, "w") as f:
                f.write("# Err\n")

            with (
                patch("sandbox_executor.formatting.is_prettier_available", return_value=True),
                patch("subprocess.run", side_effect=OSError("Disk failure")),
            ):
                result = converge_prettier(["err.md"], repo_dir=tmp_dir)
                self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
