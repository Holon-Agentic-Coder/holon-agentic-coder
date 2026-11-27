from __future__ import annotations

import contextlib
import io
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor import turns
from sandbox_executor.budgets import DEFAULT_TURN_SECONDS, BudgetBreach, StageTimer, TurnBudgets
from sandbox_executor.cli import _run_docker, run_docker_container
from sandbox_executor.flow import probe_ci_checks
from sandbox_executor.turns import build_turn_env, run_turn, silence_window

from tests import test_turn_supervisor
from tests.test_turn_supervisor import CHILD_PID, MINUTE, Harness, TickStream


class TestTurnExecutionEnvironment(unittest.TestCase):
    def test_build_turn_env_default_injects_docker_cli_hints(self) -> None:
        env = build_turn_env()
        self.assertIn("DOCKER_CLI_HINTS", env)
        self.assertEqual(env["DOCKER_CLI_HINTS"], "false")
        if "PATH" in os.environ:
            self.assertEqual(env["PATH"], os.environ["PATH"])

    def test_build_turn_env_preserves_and_merges_custom_env(self) -> None:
        caller_env = {"MY_CUSTOM_VAR": "custom_value_42", "ANOTHER_KEY": "sample"}
        effective_env = build_turn_env(caller_env)
        self.assertEqual(effective_env["MY_CUSTOM_VAR"], "custom_value_42")
        self.assertEqual(effective_env["ANOTHER_KEY"], "sample")
        self.assertEqual(effective_env["DOCKER_CLI_HINTS"], "false")
        self.assertNotIn("DOCKER_CLI_HINTS", caller_env)

    def test_run_turn_injects_docker_cli_hints_default(self) -> None:
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
            tool_sampler=None,
            child_lister=None,
        )

        self.assertEqual(result.returncode, 0)
        mock_factory.assert_called_once()
        call_kwargs = mock_factory.call_args[1]
        passed_env = call_kwargs.get("env")
        self.assertIsInstance(passed_env, dict)
        self.assertEqual(passed_env.get("DOCKER_CLI_HINTS"), "false")

    def test_run_turn_preserves_and_merges_custom_env(self) -> None:
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
            tool_sampler=None,
            child_lister=None,
        )

        self.assertEqual(result.returncode, 0)
        mock_factory.assert_called_once()
        passed_env = mock_factory.call_args[1].get("env")
        self.assertIsInstance(passed_env, dict)
        self.assertEqual(passed_env.get("MY_VAR"), "value123")
        self.assertEqual(passed_env.get("DOCKER_CLI_HINTS"), "false")

    @patch("subprocess.run")
    def test_cli_docker_helpers_propagate_cli_hints(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        self._assert_run_docker_disables_cli_hints(mock_run)
        mock_run.reset_mock()
        self._assert_run_docker_container_disables_cli_hints(mock_run)

    def _assert_run_docker_disables_cli_hints(self, mock_run: MagicMock) -> None:
        _run_docker("ps")
        self.assertTrue(mock_run.called)
        docker_call_kwargs = mock_run.call_args.kwargs
        self.assertIn("env", docker_call_kwargs)
        self.assertEqual(docker_call_kwargs["env"].get("DOCKER_CLI_HINTS"), "false")

    def _assert_run_docker_container_disables_cli_hints(self, mock_run: MagicMock) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("sandbox_executor.cli.git_flow.sandbox_worktree_mounts", return_value=[]),
            patch("sandbox_executor.launcher.mounts.get_agent_session_mounts", return_value=[]),
        ):
            code = run_docker_container(
                "planner",
                "holon/agent-antigravity",
                ["branch", "agent", "model"],
                agent_id="antigravity",
                target_worktree="/tmp/worktree",
            )
            self.assertEqual(code, 0)
            self.assertTrue(mock_run.called)
            container_cmd = mock_run.call_args[0][0]
            container_env = mock_run.call_args.kwargs.get("env")
            self.assertIn("-e", container_cmd)
            self.assertIn("DOCKER_CLI_HINTS=false", container_cmd)
            self.assertIsInstance(container_env, dict)
            self.assertEqual(container_env.get("DOCKER_CLI_HINTS"), "false")

    def test_non_zero_exit_error_logging_cleanliness(self) -> None:
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


class TestUnlimitedTurnSupervision(unittest.TestCase):
    def _progressing(self, harness, every=MINUTE):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "notes.md")

        def grow(now):
            if int(now) % every == 0:
                with open(path, "a") as f:
                    f.write(f"section at {now}\n")

        harness.on_tick = grow
        return path

    def test_progressing_runner_past_an_hour_is_never_stopped_when_unlimited(self):
        h = Harness(exit_at=3 * 3600)
        path = self._progressing(h)
        timer = StageTimer("execute", None, clock=h.clock)
        result = h.run(turn=None, stall=90, watch_path=path, stage_timer=timer)
        self.assertIsNone(result.breach)
        self.assertEqual(h.signals, [])
        self.assertGreaterEqual(h.clock.now, 3 * 3600)

    def test_streaming_runner_that_goes_silent_is_still_stalled(self):
        stream = TickStream()
        h = Harness(exit_at=None, stdout=stream)
        h.on_tick = lambda now: stream.feed(b"tok ") if now <= 60 else None
        timer = StageTimer("execute", None, clock=h.clock)
        result = h.run(turn=None, stall=90, stage_timer=timer)
        self.assertEqual(result.breach.budget_name, "stall")
        self.assertEqual(result.breach.budget_seconds, 90)
        self.assertLess(h.clock.now, 60 + 120)
        self.assertEqual(h.signals, [(CHILD_PID, signal.SIGTERM)])

    def test_finite_turn_limit_still_fires_under_an_unlimited_stage(self):
        stream = TickStream()
        h = Harness(exit_at=None, stdout=stream)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        h.on_tick = lambda now: stream.feed(b"tok ")
        timer = StageTimer("execute", None, clock=h.clock)
        result = h.run(turn=300, stall=90, watch_path=os.path.join(tmp.name, "plan.md"), stage_timer=timer)
        self.assertEqual(result.breach.budget_name, "turn")
        self.assertLess(h.clock.now, 400)

    def test_non_streaming_silence_window_falls_back_to_the_default_turn(self):
        self.assertEqual(silence_window(TurnBudgets(turn_seconds=None, stall_seconds=90), streaming=False), 3600)
        self.assertEqual(silence_window(TurnBudgets(turn_seconds=None, stall_seconds=90), streaming=True), 90)
        self.assertEqual(silence_window(TurnBudgets(turn_seconds=300, stall_seconds=90), streaming=False), 300)
        h = Harness(exit_at=None)
        result = h.run(turn=None, stall=90)
        self.assertEqual(result.breach.budget_name, "stall")
        self.assertEqual(result.breach.budget_seconds, DEFAULT_TURN_SECONDS)
        self.assertLess(h.clock.now, DEFAULT_TURN_SECONDS + 30)


class TestUnlimitedAuthoringTurn(unittest.TestCase):
    def test_resume_runs_under_an_unlimited_stage_however_long_it_ran(self):
        # why: reached as a module attribute so this file does not also collect the supervisor suite's harness class
        harness = test_turn_supervisor.TestSuperviseAuthoringTurn("test_no_resume_when_stage_has_no_time_left")
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        harness.timer = StageTimer("plan", None, clock=harness.clock)
        harness.clock.now = 30 * 24 * 3600
        stall = BudgetBreach("plan", "stall", 90, 95)
        outcome = harness.supervise([harness.write("# Plan for I-1\n", breach=stall), harness.write(None)])
        self.assertEqual(outcome.follow_up, "resume")
        self.assertEqual(len(outcome.turns), 2)


class TestBackgroundCommandOnThisHost(unittest.TestCase):
    def test_backgrounded_sleep_is_named_after_the_runner_exits(self) -> None:
        if turns.process_table() is None:
            self.skipTest("no /proc and no ps on this host")
        spawn = (
            "import subprocess\n"
            "d = subprocess.DEVNULL\n"
            "subprocess.Popen(['sleep', '30'], stdin=d, stdout=d, stderr=d)\n"
        )
        started: list[subprocess.Popen[bytes]] = []

        def popen(cmd: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
            proc = subprocess.Popen(cmd, **kwargs)  # type: ignore[call-overload]
            started.append(proc)
            return proc

        try:
            with self.assertLogs("sandbox_executor.turns", "WARNING") as logs:
                result = run_turn([sys.executable, "-c", spawn], popen_factory=popen, poll_interval=0.05)
        finally:
            for proc in started:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
        self.assertIsNone(result.breach)
        self.assertEqual(result.returncode, 0)
        self.assertIn("sleep 30", result.background_commands)
        self.assertTrue(any("background" in line and "sleep 30" in line for line in logs.output), logs.output)


if __name__ == "__main__":
    unittest.main()
