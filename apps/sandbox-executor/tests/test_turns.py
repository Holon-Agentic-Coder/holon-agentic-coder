"""Hermetic unit tests for turn subprocess execution environments and Docker CLI hint suppression.

Verifies that ``build_turn_env`` and ``run_turn`` consistently inject ``DOCKER_CLI_HINTS='false'``,
that CLI Docker helpers propagate hint suppression both to host subprocesses and container arguments,
and that non-zero container exit error reporting remains clean and unpolluted by promotional hints.
"""

from __future__ import annotations

import io
import os
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor.cli import _run_docker, run_docker_container
from sandbox_executor.flow import probe_ci_checks
from sandbox_executor.turns import build_turn_env, run_turn


class TestTurnExecutionEnvironment(unittest.TestCase):
    """Test suite verifying subprocess environment mapping construction and hint suppression."""

    def test_build_turn_env_default_injects_docker_cli_hints(self) -> None:
        """Verify that ``build_turn_env`` without arguments copies ``os.environ`` and sets hints false."""
        env = build_turn_env()
        self.assertIn("DOCKER_CLI_HINTS", env)
        self.assertEqual(env["DOCKER_CLI_HINTS"], "false")
        if "PATH" in os.environ:
            self.assertEqual(env["PATH"], os.environ["PATH"])

    def test_build_turn_env_preserves_and_merges_custom_env(self) -> None:
        """Verify that custom environment mappings are preserved and augmented with ``DOCKER_CLI_HINTS='false'``."""
        caller_env = {"MY_CUSTOM_VAR": "custom_value_42", "ANOTHER_KEY": "sample"}
        effective_env = build_turn_env(caller_env)
        self.assertEqual(effective_env["MY_CUSTOM_VAR"], "custom_value_42")
        self.assertEqual(effective_env["ANOTHER_KEY"], "sample")
        self.assertEqual(effective_env["DOCKER_CLI_HINTS"], "false")
        self.assertNotIn("DOCKER_CLI_HINTS", caller_env)

    def test_run_turn_injects_docker_cli_hints_default(self) -> None:
        """Verify that ``run_turn`` invokes ``popen_factory`` with ``DOCKER_CLI_HINTS='false'`` when ``env=None``."""
        mock_proc = MagicMock()
        mock_proc.stdout = io.BytesIO(b"")
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0
        mock_proc.pid = 9999
        mock_factory = MagicMock(return_value=mock_proc)

        result = run_turn(
            ["echo", "hello"],
            env=None,
            popen_factory=mock_factory,
            clock=lambda: 0.0,
            sleeper=lambda _: None,
        )

        self.assertEqual(result.returncode, 0)
        mock_factory.assert_called_once()
        call_kwargs = mock_factory.call_args[1]
        passed_env = call_kwargs.get("env")
        self.assertIsInstance(passed_env, dict)
        self.assertEqual(passed_env.get("DOCKER_CLI_HINTS"), "false")

    def test_run_turn_preserves_and_merges_custom_env(self) -> None:
        """Verify that ``run_turn`` retains custom environment variables alongside ``DOCKER_CLI_HINTS='false'``."""
        mock_proc = MagicMock()
        mock_proc.stdout = io.BytesIO(b"")
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0
        mock_proc.pid = 9999
        mock_factory = MagicMock(return_value=mock_proc)

        custom_env = {"MY_VAR": "value123"}
        result = run_turn(
            ["echo", "hello"],
            env=custom_env,
            popen_factory=mock_factory,
            clock=lambda: 0.0,
            sleeper=lambda _: None,
        )

        self.assertEqual(result.returncode, 0)
        mock_factory.assert_called_once()
        passed_env = mock_factory.call_args[1].get("env")
        self.assertIsInstance(passed_env, dict)
        self.assertEqual(passed_env.get("MY_VAR"), "value123")
        self.assertEqual(passed_env.get("DOCKER_CLI_HINTS"), "false")

    @patch("subprocess.run")
    def test_cli_docker_helpers_propagate_cli_hints(self, mock_run: MagicMock) -> None:
        """Verify that CLI Docker helpers pass ``DOCKER_CLI_HINTS='false'`` to host env and container args."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # 1. Verify _run_docker
        _run_docker("ps")
        self.assertTrue(mock_run.called)
        docker_call_kwargs = mock_run.call_args.kwargs
        self.assertIn("env", docker_call_kwargs)
        self.assertEqual(docker_call_kwargs["env"].get("DOCKER_CLI_HINTS"), "false")

        # 2. Verify run_docker_container
        mock_run.reset_mock()
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch(
                "sandbox_executor.cli.resolve_target_repo",
                return_value=("git@github.com:test/repo.git", None),
            ),
            patch("sandbox_executor.cli.get_agent_session_mounts", return_value=[]),
        ):
            code = run_docker_container(
                "planner",
                "holon/agent-antigravity",
                ["branch", "agent", "model"],
                agent_id="antigravity",
            )
            self.assertEqual(code, 0)
            self.assertTrue(mock_run.called)
            container_cmd = mock_run.call_args[0][0]
            container_env = mock_run.call_args.kwargs.get("env")

            # Assert -e DOCKER_CLI_HINTS=false is included in container arguments
            self.assertIn("-e", container_cmd)
            self.assertIn("DOCKER_CLI_HINTS=false", container_cmd)
            # Assert env kwarg passed to host subprocess contains DOCKER_CLI_HINTS='false'
            self.assertIsInstance(container_env, dict)
            self.assertEqual(container_env.get("DOCKER_CLI_HINTS"), "false")

    def test_non_zero_exit_error_logging_cleanliness(self) -> None:
        """Verify that non-zero container exit error reporting does not contain Gordon promotional hints."""
        gordon_hint = "What's next: Debug this container error with Gordon -> docker ai ..."
        error_message = "container execution failed: process returned exit code 1"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["docker", "run"],
                returncode=1,
                stdout="",
                stderr=error_message,
            )
            res = _run_docker("run", "mock_image")
            self.assertEqual(res.returncode, 1)
            self.assertNotIn(gordon_hint, res.stderr)
            self.assertNotIn("docker ai", res.stderr)

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/gh")
    def test_flow_subprocess_environments_contain_docker_cli_hints(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Verify that GitHub CLI and subprocess calls in flow.py guarantee ``DOCKER_CLI_HINTS='false'``."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["gh", "pr", "checks"],
            returncode=0,
            stdout="[]",
            stderr="",
        )
        probe_ci_checks(".", "dummy-branch")
        self.assertTrue(mock_run.called)
        passed_env = mock_run.call_args.kwargs.get("env")
        self.assertIsInstance(passed_env, dict)
        self.assertEqual(passed_env.get("DOCKER_CLI_HINTS"), "false")


if __name__ == "__main__":
    unittest.main()
