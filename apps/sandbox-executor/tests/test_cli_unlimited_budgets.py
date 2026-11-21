"""CLI tests for ``unlimited`` budgets: the ``holon doctor`` report and the docker stage-budget hand-down."""

import io
import os
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor import cli, local_llm
from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.budgets import StageTimer, active_stage_timer
from sandbox_executor.cli import run_docker_container

from tests.test_local_llm import HEALTH_WARM, _fetcher

# What git_flow.sandbox_worktree_mounts returns for a valid worktree, reduced to its bind mount.
_WORKTREE_MOUNTS = ["-v", f"/work/flow-x:{SANDBOX_WORKSPACE_MOUNT}"]


class TestUnlimitedDockerHandDown(unittest.TestCase):
    @patch("sandbox_executor.cli.git_flow.sandbox_worktree_mounts", return_value=list(_WORKTREE_MOUNTS))
    @patch("sandbox_executor.cli.get_agent_session_mounts", return_value=["-v", "/mock:/mock"])
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_run_docker_container_hands_down_an_unlimited_stage(
        self, mock_which, mock_run, mock_mounts, mock_worktree_mounts
    ):
        # An unlimited host stage replaces any forwarded host value with the sentinel the container honours.
        mock_run.return_value = MagicMock(returncode=0)
        timer = StageTimer("execute", None, clock=lambda: 1000.0)
        with patch.dict(os.environ, {"HOLON_STAGE_BUDGET_SECONDS": "9999"}, clear=True):
            with active_stage_timer(timer):
                run_docker_container("planner", "holon/agent-pi", ["branch"], agent_id="pi")
            budget_args = [a for a in mock_run.call_args[0][0] if a.startswith("HOLON_STAGE_BUDGET_SECONDS=")]
            self.assertEqual(budget_args, ["HOLON_STAGE_BUDGET_SECONDS=unlimited"])


class TestUnlimitedDoctor(unittest.TestCase):
    def run_doctor(self, env):
        out = io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch.object(local_llm, "host_models_json", return_value=None):
            cli.report_budgets_and_envelope(fetch=_fetcher(HEALTH_WARM), out=out)
        return out.getvalue()

    def test_doctor_shows_unlimited_budgets(self):
        env = {"HOLON_STAGE_BUDGET_EXECUTE_MINUTES": "unlimited", "HOLON_TURN_TIMEOUT_SECONDS": " Unlimited "}
        output = self.run_doctor(env)
        self.assertIn("budgets turn=unlimited stall=90s", output)
        self.assertIn("execute=unlimited", output)
        self.assertIn("plan=35min", output)
        self.assertNotIn("stage_override", output)
        self.assertNotIn("hung agent", output)

    def test_doctor_shows_an_unlimited_stage_override(self):
        output = self.run_doctor({"HOLON_STAGE_BUDGET_SECONDS": "UNLIMITED"})
        self.assertIn("intent=unlimited plan=unlimited execute=unlimited", output)
        self.assertIn("stage_override=unlimited", output)

    def test_doctor_warns_when_every_limit_is_disabled(self):
        env = {
            "HOLON_STAGE_BUDGET_EXECUTE_MINUTES": "unlimited",
            "HOLON_TURN_TIMEOUT_SECONDS": "unlimited",
            "HOLON_TURN_STALL_SECONDS": "0",
        }
        output = self.run_doctor(env)
        warnings = [line for line in output.splitlines() if line.startswith("WARNING: Unbounded agent supervision")]
        self.assertEqual(len(warnings), 1)
        self.assertIn("execute", warnings[0])
        self.assertIn("nothing will stop a hung agent", warnings[0])
