"""Tests for empty_diff diagnostics and stderr excerpts."""

import io
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.entrypoint import executor
from sandbox_executor.entrypoint.empty_diff_excerpt import (
    EMPTY_DIFF_EXCERPT_CHARS,
    format_empty_diff_excerpt,
)
from sandbox_executor.ledger import read_merged_rows
from sandbox_executor.turns import TurnResult


def _turn_via_run_cmd(cmd, **kwargs):
    """Route a supervised agent turn through ``executor.run_cmd`` so faking run_cmd also fakes the agent."""
    res = executor.run_cmd(cmd, cwd=kwargs.get("cwd"), check=False)
    stdout = res.stdout if isinstance(getattr(res, "stdout", None), str) else ""
    stderr = res.stderr if isinstance(getattr(res, "stderr", None), str) else ""
    returncode = res.returncode if isinstance(getattr(res, "returncode", None), int) else 0
    return TurnResult(returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture(autouse=True)
def _agent_turns_use_run_cmd():
    with patch("sandbox_executor.entrypoint.executor.run_turn", side_effect=_turn_via_run_cmd) as fake:
        yield fake


@pytest.fixture(autouse=True)
def _workspace_is_mounted():
    """Every test runs the executor as it runs in a role container: in the host's mounted worktree."""
    with patch("sandbox_executor.entrypoint.executor.workspace_mounted", return_value=True):
        yield


def _cuts_branch(args) -> bool:
    """True for the command that cuts the execution branch, the first step after the mount is validated."""
    return list(args[:3]) == ["git", "checkout", "-b"]


def _fake_token(prefix: str, body: str = "9f3Aq7ZxR2tKp8Wm") -> str:
    """Assemble a token-shaped fake credential from a vendor prefix and a declared-fake filler."""
    return prefix + "NOTREAL_not-a-live-key_" + body


class TestExecutorEmptyDiff(unittest.TestCase):
    def _run_main_and_capture_stderr(self, diff_stdout: str, agent_stdout: str = "OK", tip_stdout: str = "tip-sha\n"):
        """Run executor.main() with custom agent stdout and capture stderr."""
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_runner.build_cmd.return_value = ["agy", "--model", "gemini-3.5-flash", "prompt"]

        def side_effect(args, cwd=None, **kwargs):
            if _cuts_branch(args):
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(json.dumps({"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 2.0}) + "\n")
            if args[:3] == ["git", "rev-parse", "HEAD"]:
                return MagicMock(returncode=0 if tip_stdout else 1, stdout=tip_stdout, stderr="")
            if "diff" in args and "--name-only" in args:
                return MagicMock(returncode=0, stdout=diff_stdout, stderr="")
            if "ls-files" in args and "--others" in args:
                return MagicMock(returncode=0, stdout="", stderr="")
            if args == ["agy", "--model", "gemini-3.5-flash", "prompt"]:
                return MagicMock(returncode=0, stdout=agent_stdout, stderr="")
            return MagicMock(returncode=0, stdout="OK", stderr="")

        stderr_io = io.StringIO()
        with (
            patch("sandbox_executor.entrypoint.executor.run_cmd", side_effect=side_effect),
            patch("sandbox_executor.entrypoint.executor.get_runner", return_value=mock_runner),
            patch(
                "sandbox_executor.entrypoint.executor.validate_mounted_workspace",
                new=lambda *_args: tip_stdout.strip(),
            ),
            patch("sys.stderr", stderr_io),
            tempfile.TemporaryDirectory(prefix="sandbox_executor_test_") as tmp_dir,
            patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp_dir}),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]),
        ):
            executor.main()
            rows = read_merged_rows(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl"))
        summaries = [r for r in rows if r.get("entry_type") == "summary"]
        return summaries, stderr_io.getvalue()

    def test_main_empty_diff_stderr_excerpt_non_empty_output(self):
        """When empty_diff occurs and agent output is non-empty, a bounded redacted excerpt is printed to stderr."""
        agent_out = "Starting task\nWaiting for background task...\nTimeout reached without diff."
        summaries, stderr = self._run_main_and_capture_stderr("plans/P-123.md\0", agent_stdout=agent_out)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["status"], "failed")
        self.assertEqual(summaries[0]["failure_reason"], "empty_diff")
        self.assertIn("agent_output_bytes", summaries[0])
        self.assertNotIn("agent_output_text", summaries[0])
        for val in summaries[0].values():
            self.assertNotIn("Timeout reached without diff", str(val))
        self.assertIn("Error: ", stderr)
        self.assertIn("Agent output tail (last ", stderr)
        self.assertIn("Timeout reached without diff.", stderr)

    def test_main_empty_diff_stderr_excerpt_empty_output(self):
        """When empty_diff occurs and agent output is empty, no excerpt header or tail is printed to stderr."""
        summaries, stderr = self._run_main_and_capture_stderr("plans/P-123.md\0", agent_stdout="")
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["status"], "failed")
        self.assertEqual(summaries[0]["failure_reason"], "empty_diff")
        self.assertIn("Error: ", stderr)
        self.assertNotIn("Agent output tail", stderr)

    def test_main_diff_unverified_no_stderr_excerpt(self):
        """When failure is diff_unverified, no excerpt is printed to stderr."""
        summaries, stderr = self._run_main_and_capture_stderr(
            "src/app.py\0", tip_stdout="", agent_stdout="Some agent output\n"
        )
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["status"], "failed")
        self.assertEqual(summaries[0]["failure_reason"], "diff_unverified")
        self.assertIn("Error: ", stderr)
        self.assertNotIn("Agent output tail", stderr)

    def test_main_success_no_stderr_excerpt(self):
        """On successful execution, no error or agent output excerpt is printed to stderr."""
        summaries, stderr = self._run_main_and_capture_stderr(
            "src/app.py\0", agent_stdout="Agent completed task successfully\n"
        )
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["status"], "success")
        self.assertNotIn("failure_reason", summaries[0])
        self.assertNotIn("Error: ", stderr)
        self.assertNotIn("Agent output tail", stderr)
        self.assertIn("agent_output_bytes", summaries[0])

    def test_format_empty_diff_excerpt_empty_and_whitespace(self):
        """Empty and whitespace-only strings return None."""
        self.assertIsNone(format_empty_diff_excerpt("", 0))
        self.assertIsNone(format_empty_diff_excerpt("   \n\t  \n", 12))

    def test_format_empty_diff_excerpt_bounds_to_500_chars_and_redacts(self):
        """Long output returns at most 500 characters, retains the tail, and redacts secrets."""
        self.assertEqual(EMPTY_DIFF_EXCERPT_CHARS, 500)
        token = _fake_token("ghp_")
        head = "HEAD_" * 100
        tail = "TAIL_" * 100 + f" token={token} END"
        full = head + tail
        total_bytes = len(full.encode("utf-8"))

        res = format_empty_diff_excerpt(full, total_bytes)
        self.assertIsNotNone(res)
        header, body = res.split("\n", 1)
        self.assertIn(f"chars of redacted output, {total_bytes} bytes total):", header)
        self.assertLessEqual(len(body), 500)
        self.assertIn("END", body)
        self.assertNotIn("HEAD_", body)
        self.assertNotIn(token, body)
        self.assertNotIn(token, res)

    def test_format_empty_diff_excerpt_redacts_token_straddling_cut(self):
        """A token whose prefix falls before the 500-char cut does not leak its suffix into the excerpt."""
        token = _fake_token("ghp_", "9f3Aq7ZxR2tKp8WmQ4vLs6Bn")
        suffix = token[8:]
        full = "x" * 1000 + " " + token + " " + "y" * (EMPTY_DIFF_EXCERPT_CHARS - len(suffix) - 1)
        res = format_empty_diff_excerpt(full, len(full.encode("utf-8")))
        self.assertIsNotNone(res)
        self.assertNotIn(suffix, res)
        self.assertNotIn(token[-12:], res)

    def test_format_empty_diff_excerpt_redacts_key_value_straddling_cut(self):
        """A `password=value` whose key falls before the 500-char cut does not leak its value into the excerpt."""
        secret = "hunter2-Zq8vLm3RxT7pWk4N"
        full = "x" * 1000 + "\npassword=" + secret + " " + "y" * (EMPTY_DIFF_EXCERPT_CHARS - 10)
        res = format_empty_diff_excerpt(full, len(full.encode("utf-8")))
        self.assertIsNotNone(res)
        self.assertNotIn(secret[-8:], res)
