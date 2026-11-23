"""Live-child guard and background-command tests for the turn supervisor. Every process, clock and signal is faked."""

import os
import signal
import tempfile
import unittest

from sandbox_executor import turns
from sandbox_executor.budgets import DEFAULT_TURN_SECONDS

from tests.test_turn_supervisor import CHILD_PID, Harness, TickStream


class TestLiveChildGuard(unittest.TestCase):
    """A quiet foreground tool (a live child burning no CPU) is not a stall; the turn timeout still bounds it."""

    TEST_PID = CHILD_PID + 1

    def streamed_once(self, children):
        # A resolver-style runner: it streams once (so the short stall window would apply), then waits on a quiet
        # foreground test that burns no CPU and never exits.
        stream = TickStream()
        h = Harness(exit_at=None, stdout=stream)
        h.children = children
        h.on_tick = lambda now: stream.feed(b"tok ") if now <= 1 else None
        return h

    def test_quiet_foreground_test_is_not_a_stall_but_is_bounded_by_the_turn_timeout(self):
        h = self.streamed_once({self.TEST_PID: "uv run task test"})
        result = h.run(turn=300, stall=90)
        self.assertEqual(result.breach.budget_name, "stall")
        self.assertEqual(result.breach.budget_seconds, 300)
        self.assertGreaterEqual(h.clock.now, 300)
        self.assertLess(h.clock.now, 330)
        self.assertEqual(h.signals, [(CHILD_PID, signal.SIGTERM)])

    def test_watched_turn_with_a_quiet_foreground_test_breaches_the_turn_budget(self):
        h = self.streamed_once({self.TEST_PID: "uv run task test"})
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        result = h.run(turn=300, stall=90, watch_path=os.path.join(tmp.name, "notes.md"))
        self.assertEqual(result.breach.budget_name, "turn")
        self.assertGreaterEqual(h.clock.now, 300)
        self.assertLess(h.clock.now, 330)

    def test_without_a_live_child_the_stall_still_fires_at_the_stall_window(self):
        h = self.streamed_once({})
        result = h.run(turn=300, stall=90)
        self.assertEqual(result.breach.budget_name, "stall")
        self.assertEqual(result.breach.budget_seconds, 90)
        self.assertLess(h.clock.now, 1 + 120)

    def test_unlimited_turn_with_a_live_child_is_bounded_by_the_default_turn(self):
        h = self.streamed_once({self.TEST_PID: "uv run task test"})
        result = h.run(turn=None, stall=90)
        self.assertEqual(result.breach.budget_name, "stall")
        self.assertEqual(result.breach.budget_seconds, DEFAULT_TURN_SECONDS)
        self.assertLess(h.clock.now, DEFAULT_TURN_SECONDS + 30)

    def test_breach_warning_names_the_live_command(self):
        h = self.streamed_once({self.TEST_PID: "uv run task test"})
        with self.assertLogs("sandbox_executor.turns", "WARNING") as logs:
            h.run(turn=300, stall=90)
        self.assertTrue(any("uv run task test" in line for line in logs.output), logs.output)

    def test_failing_lister_does_not_fail_the_turn(self):
        h = Harness(exit_at=5)

        def broken(pid):
            raise OSError("proc vanished")

        h.child_lister = broken
        result = h.run()
        self.assertIsNone(result.breach)
        self.assertEqual(result.background_commands, ())


class TestBackgroundCommandWarning(unittest.TestCase):
    def test_command_left_running_after_exit_is_named_in_a_warning(self):
        h = Harness(exit_at=5)
        h.children = {CHILD_PID + 1: "uv run task test"}
        with self.assertLogs("sandbox_executor.turns", "WARNING") as logs:
            result = h.run(turn=300, stall=90)
        self.assertIsNone(result.breach)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.background_commands, ("uv run task test",))
        warnings = [line for line in logs.output if "uv run task test" in line]
        self.assertTrue(warnings, logs.output)
        self.assertIn("background", warnings[0])
        self.assertNotIn("stall", warnings[0])
        self.assertEqual(h.signals, [])

    def test_clean_exit_without_leftovers_records_no_background_commands(self):
        h = Harness(exit_at=5)
        self.assertEqual(h.run().background_commands, ())


class TestDescendantCommands(unittest.TestCase):
    def test_process_command_reads_proc_cmdline(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.makedirs(os.path.join(tmp.name, "77"))
        with open(os.path.join(tmp.name, "77", "cmdline"), "wb") as f:
            f.write(b"uv\0run\0task\0test\0")
        os.makedirs(os.path.join(tmp.name, "79"))
        with open(os.path.join(tmp.name, "79", "cmdline"), "wb") as f:
            f.write(b"x" * 500 + b"\0")
        self.assertEqual(turns.process_command(77, proc_root=tmp.name), "uv run task test")
        self.assertIsNone(turns.process_command(78, proc_root=tmp.name))
        self.assertEqual(len(turns.process_command(79, proc_root=tmp.name)), turns.COMMAND_DISPLAY_CHARS)

    def test_descendant_commands_exclude_the_agent_and_unreadable_processes(self):
        table = {
            100: (1, 100, 1.0, 0.0),  # the runner (a wrapper)
            101: (100, 100, 1.0, 0.0),  # the wrapped agent binary
            102: (101, 100, 0.0, 0.0),  # a foreground test
            103: (1, 100, 0.0, 0.0),  # a backgrounded command, re-parented but still in the group
            104: (101, 100, 0.0, 0.0),  # exited before its command line was read
            200: (1, 200, 0.0, 0.0),  # unrelated
        }
        names = {100: "MainThread", 101: "codex"}
        commands = {100: "node codex", 101: "codex", 102: "uv run task test", 103: "sleep 30", 200: "other"}
        self.assertEqual(
            turns.descendant_commands(100, lambda: table, name_of=names.get, command_of=commands.get),
            {102: "uv run task test", 103: "sleep 30"},
        )
        self.assertIsNone(turns.descendant_commands(100, lambda: None))

    def test_describe_commands_is_sorted_by_pid(self):
        described = turns.describe_commands({9: "sleep 30", 3: "uv run task test"})
        self.assertEqual(described, "uv run task test (pid 3); sleep 30 (pid 9)")
