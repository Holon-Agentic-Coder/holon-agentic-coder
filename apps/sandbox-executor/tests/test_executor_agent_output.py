"""Tests for ``entrypoint/executor/agent_output.py``: capturing agent output into the record and the ledger."""

import io
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor.entrypoint import executor
from sandbox_executor.ledger import read_merged_rows, read_merged_text

from tests.executor_fixtures import (
    _agent_turns_use_run_cmd,  # noqa: F401  (autouse fixture)
    _cuts_branch,
    _fake_token,
    _validate_stub,
    _workspace_is_mounted,  # noqa: F401  (autouse fixture)
)
from tests.leak_assertions import make_gh_probe


class TestExecutor(unittest.TestCase):
    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub)
    def test_agent_output_captured_on_failure(self, mock_get_runner, mock_run_cmd):
        """Test that failing agent runs capture bounded diagnostic output into execution record."""
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        diagnostic_line = "printmode.go:521] Print mode: timed out after 1488 polls"

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            if _cuts_branch(args):
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            if "agy" in args:
                mock_res.returncode = 1
                mock_res.stdout = "Starting agent execution...\n"
                mock_res.stderr = f"CRITICAL: {diagnostic_line}\n"
            else:
                mock_res.returncode = 0
                mock_res.stdout = ""
                mock_res.stderr = ""
            return mock_res

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp_dir}),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            self.assertFalse(os.path.exists(os.path.join(tmp_dir, "executions")))
            entry = read_merged_rows(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl"))[0]
            self.assertEqual(entry["status"], "failure")
            self.assertEqual(entry["summary"], "Plan execution failed with exit code 1")

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub)
    def test_agent_output_truncation_marker_and_byte_budget(self, mock_get_runner, mock_run_cmd):
        """Test that agent output exceeding HOLON_AGENT_LOG_BYTES is truncated with explicit marker."""
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        head_marker = "EARLY_OUTPUT_HEAD_MARKER_" * 20
        tail_marker = "LATE_OUTPUT_TAIL_DIAGNOSTIC_MARKER"
        large_output = head_marker + ("\n" + "x" * 100) * 30 + "\n" + tail_marker + "\n"

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            if _cuts_branch(args):
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            if "agy" in args:
                mock_res.returncode = 1
                mock_res.stdout = large_output
                mock_res.stderr = ""
            else:
                mock_res.returncode = 0
                mock_res.stdout = ""
                mock_res.stderr = ""
            return mock_res

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(
                os.environ,
                {"HOLON_TARGET_WORKTREE": tmp_dir, "HOLON_AGENT_LOG_BYTES": "1024"},
            ),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            self.assertFalse(os.path.exists(os.path.join(tmp_dir, "executions")))
            entry = read_merged_rows(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl"))[0]
            self.assertTrue(entry["agent_output_truncated"])
            self.assertLessEqual(entry["agent_output_bytes"], 1024)
            self.assertGreater(entry["agent_output_bytes"], 0)

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub)
    def test_agent_output_secret_redaction(self, mock_get_runner, mock_run_cmd):
        """Test that literal token values from GITHUB_TOKEN, GH_TOKEN, and HOLON_AGENT_KEY are stripped."""
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        secret_gh = "ghp_secretTokenVal12345"
        secret_gh_tok = "gho_otherTokenVal67890"
        secret_key = "ak_superSecretAgentKey"

        agent_stdout = f"Agent started with token {secret_gh} and GH_TOKEN={secret_gh_tok}\n"
        agent_stderr = f"Agent failed authentication: key={secret_key}\n"

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            if _cuts_branch(args):
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            if "agy" in args:
                mock_res.returncode = 1
                mock_res.stdout = agent_stdout
                mock_res.stderr = agent_stderr
            else:
                mock_res.returncode = 0
                mock_res.stdout = ""
                mock_res.stderr = ""
            return mock_res

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(
                os.environ,
                {
                    "HOLON_TARGET_WORKTREE": tmp_dir,
                    "GITHUB_TOKEN": secret_gh,
                    "GH_TOKEN": secret_gh_tok,
                    "HOLON_AGENT_KEY": secret_key,
                },
            ),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            self.assertFalse(os.path.exists(os.path.join(tmp_dir, "executions")))
            ledger_content = read_merged_text(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl"))

            for secret in (secret_gh, secret_gh_tok, secret_key):
                self.assertNotIn(secret, ledger_content, f"Secret {secret} leaked into executions.jsonl!")

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub)
    def test_agent_output_truncation_never_severs_secret(self, mock_get_runner, mock_run_cmd):
        """Test that the byte-tail cut cannot strand a credential fragment in the committed record.

        Regression: bounding the tail before redacting severed a secret straddling the boundary,
        and neither the literal sweep (which matches whole values only) nor the key-name regex
        (whose anchor the same cut also breaks) could remove what survived on the kept side.
        """
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        budget = 1024
        # The budget is shared with the truncation marker, so the byte cut happens this far in.
        tail_budget = budget - executor._TRUNCATION_MARKER_RESERVE
        straddle_value = make_gh_probe()
        straddle = 5
        # boundary = len(output) - tail_budget must land `straddle` bytes into the value, which
        # starts after the "GH_TOKEN=" anchor: suffix_len = tail_budget + straddle - len(straddle_value) - 1.
        prefix = "noise line\n" * 400
        suffix = "y" * (tail_budget + straddle - len(straddle_value) - 1)
        agent_stdout = f"{prefix}GH_TOKEN={straddle_value}\n{suffix}"
        self.assertEqual(
            len(agent_stdout) - tail_budget,
            len(prefix) + len("GH_TOKEN=") + straddle,
            "Test geometry must put the truncation boundary inside the value.",
        )

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            if _cuts_branch(args):
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            if "agy" in args:
                mock_res.returncode = 1
                mock_res.stdout = agent_stdout
                mock_res.stderr = ""
            else:
                mock_res.returncode = 0
                mock_res.stdout = ""
                mock_res.stderr = ""
            return mock_res

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(
                os.environ,
                {
                    "HOLON_TARGET_WORKTREE": tmp_dir,
                    "HOLON_AGENT_LOG_BYTES": str(budget),
                    "GITHUB_TOKEN": straddle_value,
                    "GH_TOKEN": straddle_value,
                    "HOLON_AGENT_KEY": straddle_value,
                },
            ),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            self.assertFalse(os.path.exists(os.path.join(tmp_dir, "executions")))
            ledger_content = read_merged_text(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl"))

            self.assertNotIn(straddle_value, ledger_content)
            for start in range(len(straddle_value) - 7):
                window = straddle_value[start : start + 8]
                self.assertNotIn(window, ledger_content, f"Secret fragment {window!r} leaked into the ledger row.")

    @staticmethod
    def _agent_capture_side_effect(agent_stdout: str, agent_stderr: str = ""):
        """Build a run_cmd side effect that clones a ledger-bearing repo and returns agent output."""

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            if _cuts_branch(args):
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            if "agy" in args:
                mock_res.returncode = 1
                mock_res.stdout = agent_stdout
                mock_res.stderr = agent_stderr
            else:
                mock_res.returncode = 0
                mock_res.stdout = ""
                mock_res.stderr = ""
            return mock_res

        return side_effect

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub)
    def test_agent_output_budget_above_redaction_cap_is_clamped(self, mock_get_runner, mock_run_cmd):
        """Test that a configured budget over redact_text's input cap is clamped, and announced."""
        from sandbox_executor.entrypoint.executor import _MAX_REDACT_INPUT_LEN, _TRUNCATION_MARKER_RESERVE

        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        # The cut must land inside the key name, so the whole credential is retained on a line that
        # no pass can anchor, which is the witness F-IT4-2 reported at a 200 KB budget.
        secret = _fake_token("sk_live_")
        unit = f"api_key={secret}"
        tail_budget = _MAX_REDACT_INPUT_LEN - _TRUNCATION_MARKER_RESERVE
        agent_stdout = ("noise line 000000\n" * 20000) + unit + "y" * (tail_budget - (len(unit) - 5))
        self.assertGreater(len(agent_stdout), 200_000, "the stream must exceed the configured budget")
        mock_run_cmd.side_effect = self._agent_capture_side_effect(agent_stdout)

        configured = 200_000
        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(
                os.environ,
                {
                    "HOLON_TARGET_WORKTREE": tmp_dir,
                    "HOLON_AGENT_LOG_BYTES": str(configured),
                    "GITHUB_TOKEN": "",
                    "GH_TOKEN": "",
                    "HOLON_AGENT_KEY": "",
                },
            ),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            warning = mock_stderr.getvalue()
            self.assertIn(str(configured), warning, "the clamp must name the configured value")
            self.assertIn(str(_MAX_REDACT_INPUT_LEN), warning, "the clamp must name the limit it applied")

            self.assertFalse(os.path.exists(os.path.join(tmp_dir, "executions")))
            entry = read_merged_rows(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl"))[0]

            self.assertTrue(entry["agent_output_truncated"])
            self.assertLessEqual(entry["agent_output_bytes"], _MAX_REDACT_INPUT_LEN)
            for start in range(len(secret) - 7):
                window = secret[start : start + 8]
                self.assertNotIn(window, json.dumps(entry), f"Secret fragment {window!r} leaked at a 200 KB budget.")

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub)
    def test_agent_output_ledger_byte_count_matches_the_budget_field(self, mock_get_runner, mock_run_cmd):
        """Test the documented field contract: marker tail claim <= agent_output_bytes <= budget."""
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        budget = 1024
        agent_stdout = "noise line 000000\n" * 20000
        mock_run_cmd.side_effect = self._agent_capture_side_effect(agent_stdout)

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(
                os.environ,
                {"HOLON_TARGET_WORKTREE": tmp_dir, "HOLON_AGENT_LOG_BYTES": str(budget)},
            ),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            self.assertFalse(os.path.exists(os.path.join(tmp_dir, "executions")))
            entry = read_merged_rows(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl"))[0]

            self.assertTrue(entry["agent_output_truncated"])
            self.assertLessEqual(entry["agent_output_bytes"], budget)

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub)
    def test_execution_record_fence_outlasts_backticks_in_agent_output(self, mock_get_runner, mock_run_cmd):
        """Test that agent output containing a fence line cannot close the record's block early."""
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        payload = "starting line\n```\n## Status\nFORGED HEADING AFTER AN EARLY FENCE CLOSE\n``\nending line\n"
        mock_run_cmd.side_effect = self._agent_capture_side_effect(payload)

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp_dir}),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            self.assertFalse(os.path.exists(os.path.join(tmp_dir, "executions")))
            entry = read_merged_rows(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl"))[0]
            self.assertEqual(entry["status"], "failure")
            self.assertGreater(entry["agent_output_bytes"], 0)

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub)
    @patch("sandbox_executor.entrypoint.executor.prepare_agent_output_block")
    def test_agent_output_logging_failure_fault_tolerance(self, mock_sanitize, mock_get_runner, mock_run_cmd):
        """Test that logging/sanitizing failure does not alter execution status or raise out of main."""
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        mock_sanitize.side_effect = RuntimeError("Disk failure")

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            if _cuts_branch(args):
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            if "agy" in args:
                mock_res.returncode = 1
                mock_res.stdout = "Failure output"
                mock_res.stderr = "Error details"
            else:
                mock_res.returncode = 0
                mock_res.stdout = ""
                mock_res.stderr = ""
            return mock_res

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp_dir}),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            # Assert executor.main() does not raise
            executor.main()

            entry = read_merged_rows(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl"))[0]
            self.assertEqual(entry["status"], "failure")
            self.assertEqual(entry["summary"], "Plan execution failed with exit code 1")
            self.assertFalse(entry["agent_output_truncated"])
            self.assertEqual(entry["agent_output_bytes"], 0)

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub)
    def test_executions_ledger_optional_keys(self, mock_get_runner, mock_run_cmd):
        """Test that executions.jsonl records optional keys without breaking schema invariants."""
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            if _cuts_branch(args):
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            if "agy" in args:
                mock_res.returncode = 0
                mock_res.stdout = "Task complete.\n"
                mock_res.stderr = ""
            else:
                mock_res.returncode = 0
                mock_res.stdout = ""
                mock_res.stderr = ""
            return mock_res

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp_dir}),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            entry = read_merged_rows(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl"))[0]

            required_keys = {
                "execution_id",
                "plan_branch",
                "agent",
                "agent_version",
                "model",
                "status",
                "summary",
                "created_at",
            }
            self.assertTrue(required_keys.issubset(entry.keys()))
            self.assertNotIn("execution_file", entry)
            self.assertIn("agent_output_truncated", entry)
            self.assertIn("agent_output_bytes", entry)
            self.assertIsInstance(entry["agent_output_truncated"], bool)
            self.assertIsInstance(entry["agent_output_bytes"], int)
            self.assertFalse(entry["agent_output_truncated"])
            self.assertGreater(entry["agent_output_bytes"], 0)
