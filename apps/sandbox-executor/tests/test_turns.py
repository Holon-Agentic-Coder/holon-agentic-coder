from __future__ import annotations

import contextlib
import io
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from unittest import mock
from unittest.mock import MagicMock, patch

from sandbox_executor import turns
from sandbox_executor.budgets import DEFAULT_TURN_SECONDS, BudgetBreach, StageTimer, TurnBudgets
from sandbox_executor.cli import _run_docker, run_docker_container
from sandbox_executor.flow import probe_ci_checks
from sandbox_executor.turns import TurnConfig, TurnRequest, build_turn_env, run_turn, silence_window

from tests import test_turn_supervisor
from tests.test_turn_supervisor import CHILD_PID, MINUTE, FakeClock, Harness, TickStream


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

        _run_docker("ps")
        self.assertTrue(mock_run.called)
        docker_call_kwargs = mock_run.call_args.kwargs
        self.assertIn("env", docker_call_kwargs)
        self.assertEqual(docker_call_kwargs["env"].get("DOCKER_CLI_HINTS"), "false")

        mock_run.reset_mock()
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
        # why: accessing TestSuperviseAuthoringTurn via module attribute keeps pytest from re-collecting it
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


class TestTurnConfigAndRequest(unittest.TestCase):
    def _mock_factory(self) -> MagicMock:
        mock_proc = MagicMock()
        mock_proc.stdout = io.BytesIO(b"ok\n")
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0
        mock_proc.pid = 9999
        return MagicMock(return_value=mock_proc)

    def test_turn_config_defaults_and_immutability(self) -> None:
        cfg = TurnConfig()
        self.assertIsNone(cfg.stdin_file)
        self.assertIsNone(cfg.turn_budgets)
        self.assertIsNone(cfg.stage_timer)
        self.assertEqual(cfg.stage, "turn")
        self.assertIsNone(cfg.watch_path)
        self.assertIsNone(cfg.watch_tree)
        self.assertIsNone(cfg.cwd)
        self.assertIsNone(cfg.env)
        self.assertEqual(cfg.poll_interval, turns.POLL_INTERVAL_SECONDS)
        self.assertEqual(cfg.grace_seconds, turns.TERMINATION_GRACE_SECONDS)
        self.assertIs(cfg.tool_sampler, turns.descendant_cpu_seconds)
        self.assertIs(cfg.child_lister, turns.descendant_commands)
        with self.assertRaises(FrozenInstanceError):
            cfg.stage = "plan"  # type: ignore[misc]

    def test_run_turn_with_turn_config_passes_env_cwd_and_hooks(self) -> None:
        mock_factory = self._mock_factory()
        ticks = iter([10.0, 12.5])
        slept: list[float] = []
        cfg = TurnConfig(
            cwd="/tmp/work",
            env={"CUSTOM_KEY": "abc"},
            clock=lambda: next(ticks),
            sleeper=slept.append,
            popen_factory=mock_factory,
            tool_sampler=None,
            child_lister=None,
        )
        result = run_turn(["echo", "hi"], config=cfg)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "ok\n")
        self.assertEqual(result.elapsed_seconds, 2.5)
        mock_factory.assert_called_once()
        call_kwargs = mock_factory.call_args.kwargs
        self.assertEqual(call_kwargs["cwd"], "/tmp/work")
        self.assertEqual(call_kwargs["env"]["CUSTOM_KEY"], "abc")
        self.assertEqual(call_kwargs["env"]["DOCKER_CLI_HINTS"], "false")

    def test_run_turn_with_turn_request_and_keyword_overrides(self) -> None:
        mock_factory = self._mock_factory()
        base = TurnConfig(
            cwd="/tmp/base",
            env={"BASE": "1"},
            clock=lambda: 0.0,
            sleeper=lambda _: None,
            popen_factory=mock_factory,
            tool_sampler=None,
            child_lister=None,
        )
        request = TurnRequest(cmd=["echo", "req"], config=base)
        result = run_turn(request, cwd="/tmp/override")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(mock_factory.call_args.args[0], ["echo", "req"])
        self.assertEqual(mock_factory.call_args.kwargs["cwd"], "/tmp/override")
        self.assertEqual(mock_factory.call_args.kwargs["env"]["DOCKER_CLI_HINTS"], "false")

        with self.assertRaises(TypeError):
            run_turn(["echo", "bad"], config=base, unknown_option=True)

    def test_run_turn_rejects_turn_request_combined_with_config(self) -> None:
        request = TurnRequest(cmd=["echo", "hi"], config=TurnConfig())
        with self.assertRaises(TypeError):
            run_turn(request, config=TurnConfig())


class TestDecomposedTurnHelpers(unittest.TestCase):
    def _harness_config(self, h: Harness, **overrides: object) -> TurnConfig:
        return TurnConfig(
            stage="plan",
            clock=h.clock,
            sleeper=h.sleeper,
            popen_factory=h.popen,
            killpg=h.killpg,
            getpgid=h.getpgid,
            tool_sampler=h.tool_sampler,
            child_lister=h.child_lister,
            **overrides,  # type: ignore[arg-type]
        )

    def test_run_turn_enforces_breaches_via_turn_config_and_turn_request(self) -> None:
        stream = TickStream()
        h_turn = Harness(exit_at=None, stdout=stream)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        h_turn.on_tick = lambda now: stream.feed(b"tok ")
        turn_cfg = self._harness_config(
            h_turn,
            turn_budgets=TurnBudgets(turn_seconds=300, stall_seconds=90),
            watch_path=os.path.join(tmp.name, "plan.md"),
        )
        turn_res = run_turn(["agent"], config=turn_cfg)
        self.assertEqual(turn_res.breach.budget_name, "turn")

        stall_stream = TickStream()
        h_stall = Harness(exit_at=None, stdout=stall_stream)
        h_stall.on_tick = lambda now: stall_stream.feed(b"tok ") if now <= 20 else None
        stall_req = TurnRequest(
            cmd=["agent"],
            config=self._harness_config(h_stall, turn_budgets=TurnBudgets(turn_seconds=300, stall_seconds=60)),
        )
        stall_res = run_turn(stall_req)
        self.assertEqual(stall_res.breach.budget_name, "stall")

        h_stage = Harness(exit_at=None)
        timer = StageTimer("plan", 30, clock=h_stage.clock)
        stage_req = TurnRequest(
            cmd=["agent"],
            config=self._harness_config(
                h_stage,
                turn_budgets=TurnBudgets(turn_seconds=300, stall_seconds=0),
                stage_timer=timer,
            ),
        )
        stage_res = run_turn(stage_req)
        self.assertEqual(stage_res.breach.budget_name, "stage")

    def test_resolve_turn_request_variants_and_errors(self) -> None:
        kw_req = turns._resolve_turn_request(["cmd"], None, {"stage": "execute", "cwd": "/tmp"})
        self.assertEqual(kw_req.cmd, ["cmd"])
        self.assertEqual(kw_req.config.stage, "execute")
        self.assertEqual(kw_req.config.cwd, "/tmp")

        base = TurnConfig(stage="review", cwd="/work")
        cfg_req = turns._resolve_turn_request(["cmd"], base, {})
        self.assertIs(cfg_req.config, base)

        over_req = turns._resolve_turn_request(["cmd"], base, {"cwd": "/override"})
        self.assertEqual(over_req.config.stage, "review")
        self.assertEqual(over_req.config.cwd, "/override")

        wrapped = TurnRequest(cmd=["wrapped"], config=base)
        same_req = turns._resolve_turn_request(wrapped, None, {})
        self.assertEqual(same_req.cmd, ["wrapped"])
        self.assertIs(same_req.config, base)

        with self.assertRaises(TypeError):
            turns._resolve_turn_request(wrapped, base, {})
        with self.assertRaises(TypeError):
            turns._resolve_turn_request(["cmd"], base, {"nonexistent_field": 1})

    def test_evaluate_breach_priority_and_no_breach(self) -> None:
        clock = FakeClock()
        timer = StageTimer("plan", 100, clock=clock)
        cfg = TurnConfig(
            stage="plan",
            turn_budgets=TurnBudgets(turn_seconds=50, stall_seconds=20),
            stage_timer=timer,
            watch_path="/tmp/fake",
        )
        state = turns._init_supervision_state(cfg, start=0.0)
        state.streaming = True

        self.assertIsNone(turns._evaluate_breach(state, cfg, now=10.0))

        clock.now = 120.0
        turn_first = turns._evaluate_breach(state, cfg, now=120.0)
        self.assertEqual(turn_first.budget_name, "turn")

        state.last_progress = 110.0
        stall_second = turns._evaluate_breach(state, cfg, now=120.0)
        self.assertEqual(stall_second.budget_name, "stall")

        state.last_activity = 115.0
        stage_third = turns._evaluate_breach(state, cfg, now=120.0)
        self.assertEqual(stage_third.budget_name, "stage")

    def test_poll_progress_and_tool_progress_reset_streaming_and_last_progress(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        watch_dir = os.path.join(tmp.name, "watch")
        tree_dir = os.path.join(tmp.name, "tree")
        os.makedirs(watch_dir)
        os.makedirs(tree_dir)
        watch_path = os.path.join(watch_dir, "artefact.md")
        tree_file = os.path.join(tree_dir, "src.py")
        samples = [{5001: 0.0}, {5001: 1.0}]
        cfg = TurnConfig(
            watch_path=watch_path,
            watch_tree=tree_dir,
            tool_sampler=lambda _pid: samples.pop(0),
            child_lister=lambda _pid: {5001: "pytest"},
        )
        state = turns._init_supervision_state(cfg, start=0.0)
        reader_out = mock.Mock(count=0)
        reader_err = mock.Mock(count=0)

        reader_out.count = 12
        turns._poll_progress(state, cfg, [reader_out, reader_err], CHILD_PID, now=1.0)
        self.assertTrue(state.streaming)
        self.assertEqual(state.last_activity, 1.0)
        self.assertEqual(state.last_progress, 0.0)
        self.assertEqual(state.live_children, {5001: "pytest"})

        with open(watch_path, "w", encoding="utf-8") as handle:
            handle.write("content\n")
        turns._poll_progress(state, cfg, [reader_out, reader_err], CHILD_PID, now=5.0)
        self.assertFalse(state.streaming)
        self.assertEqual(state.last_progress, 5.0)

        reader_out.count = 24
        turns._poll_progress(state, cfg, [reader_out, reader_err], CHILD_PID, now=11.0)
        self.assertFalse(state.streaming)
        self.assertEqual(state.last_progress, 11.0)

        reader_out.count = 36
        with open(tree_file, "w", encoding="utf-8") as handle:
            handle.write("x = 1\n")
        newer_mtime_ns = max(state.seen_tree or 0, os.stat(tree_file).st_mtime_ns) + 1_000_000_000
        os.utime(tree_file, ns=(newer_mtime_ns, newer_mtime_ns))
        turns._poll_progress(state, cfg, [reader_out, reader_err], CHILD_PID, now=20.0)
        self.assertFalse(state.streaming)
        self.assertEqual(state.last_progress, 20.0)
        self.assertEqual(state.seen_tree, newer_mtime_ns)

    def test_spawn_turn_process_with_and_without_stdin_file(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        stdin_path = os.path.join(tmp.name, "prompt.txt")
        with open(stdin_path, "wb") as handle:
            handle.write(b"hello stdin")

        captured: list[dict[str, object]] = []

        def fake_popen(cmd: list[str], **kwargs: object) -> mock.Mock:
            record = dict(kwargs)
            if "stdin" in kwargs:
                record["stdin_bytes"] = kwargs["stdin"].read()  # type: ignore[union-attr]
            captured.append(record)
            return mock.Mock()

        with_stdin = TurnRequest(
            cmd=["agent"],
            config=TurnConfig(stdin_file=stdin_path, cwd=tmp.name, popen_factory=fake_popen),
        )
        turns._spawn_turn_process(with_stdin)
        self.assertEqual(captured[-1]["stdin_bytes"], b"hello stdin")
        self.assertTrue(captured[-1]["start_new_session"])

        without_stdin = TurnRequest(
            cmd=["agent"],
            config=TurnConfig(stdin_file=os.path.join(tmp.name, "missing.txt"), popen_factory=fake_popen),
        )
        turns._spawn_turn_process(without_stdin)
        self.assertNotIn("stdin", captured[-1])
        self.assertTrue(captured[-1]["start_new_session"])


if __name__ == "__main__":
    unittest.main()
