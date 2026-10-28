"""Turn supervisor tests. Every process, clock, sleep and signal is faked: no real process is spawned or signalled.

The only exception is :class:`TestProcessSamplerOnThisHost`, which checks that the descendant-CPU sampler works on the
host it runs on (``/proc`` on Linux, ``ps`` on macOS) with one short-lived busy child.
"""

import io
import json
import os
import queue
import signal
import tempfile
import unittest
from unittest import mock

from sandbox_executor import turns
from sandbox_executor.artefacts import PLAN_SPEC
from sandbox_executor.budgets import BudgetBreach, StageTimer, TurnBudgets
from sandbox_executor.turns import (
    TurnResult,
    parse_stop_reason,
    record_budget_failure,
    run_turn,
    supervise_authoring_turn,
)

CHILD_PID = 4242
OWN_PGID = 1
MINUTE = 60


class TickStream:
    """A pipe the test feeds one chunk at a time, to model a runner that streams tokens of one long response."""

    def __init__(self):
        self.chunks = queue.Queue()
        self.closed = False

    def feed(self, data):
        self.chunks.put(data)
        self.chunks.join()  # wait until the reader thread has taken it

    def close(self):
        # The EOF sentinel wakes a reader blocked in read1 at once, instead of on its next poll.
        self.closed = True
        self.chunks.put(b"")

    def read1(self, size):
        while True:
            try:
                data = self.chunks.get(timeout=0.01)
            except queue.Empty:
                if self.closed:
                    return b""
                continue
            self.chunks.task_done()
            return data


class RecordedReaders(list):
    """Patches ``turns._StreamReader`` so the test can join exactly the readers one ``run_turn`` call starts.

    ``run_turn`` gives its readers ``READER_JOIN_TIMEOUT_SECONDS`` of real time to see EOF. A fake ``killpg`` that
    closes the pipe joins them itself before returning, so the outcome never depends on thread scheduling.
    """

    def __enter__(self):
        recorded = self

        class Reader(turns._StreamReader):
            def __init__(self, stream):
                super().__init__(stream)
                recorded.append(self)

        self._patch = mock.patch.object(turns, "_StreamReader", Reader)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()

    def join(self):
        for reader in self:
            # A safety net only: the pipe is already at EOF, so this returns at once.
            reader.join(timeout=5.0)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeProc:
    """A child that exits at ``exit_at`` fake seconds, or when signalled (unless it ignores SIGTERM)."""

    def __init__(self, clock, exit_at=None, returncode=0, stdout=b"", stderr=b"", ignores_sigterm=False):
        self.clock = clock
        self.exit_at = exit_at
        self.final_returncode = returncode
        self.pid = CHILD_PID
        self.stdout = stdout if isinstance(stdout, TickStream) else io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.ignores_sigterm = ignores_sigterm
        self.returncode = None
        self.popen_kwargs = {}

    def poll(self):
        if self.returncode is None and self.exit_at is not None and self.clock() >= self.exit_at:
            self.returncode = self.final_returncode
        return self.returncode

    def signal(self, sig):
        if sig == signal.SIGTERM and self.ignores_sigterm:
            return
        self.returncode = -sig
        if isinstance(self.stdout, TickStream):
            self.stdout.close()

    def send_signal(self, sig):
        self.signal(sig)

    def wait(self, timeout=None):
        return self.poll()


class Harness:
    def __init__(self, **proc_kwargs):
        self.clock = FakeClock()
        self.proc = FakeProc(self.clock, **proc_kwargs)
        self.signals = []
        self.sleeps = 0
        self.on_tick = None
        # Fake descendant CPU seconds by pid; None models a host without /proc or ps.
        self.tool_cpu = {}

    def tool_sampler(self, pid):
        self.sampled_pid = pid
        return None if self.tool_cpu is None else dict(self.tool_cpu)

    def popen(self, cmd, **kwargs):
        self.proc.popen_kwargs = kwargs
        return self.proc

    def sleeper(self, seconds):
        self.sleeps += 1
        self.clock.now += seconds
        if self.on_tick:
            self.on_tick(self.clock.now)

    def killpg(self, pgid, sig):
        self.signals.append((pgid, sig))
        if pgid == CHILD_PID:
            self.proc.signal(sig)

    def getpgid(self, pid):
        return CHILD_PID if pid == CHILD_PID else OWN_PGID

    def run(self, turn=300, stall=90, stage_timer=None, watch_path=None, getpgid=None, watch_tree=None):
        return run_turn(
            ["agent", "-p", "prompt"],
            turn_budgets=TurnBudgets(turn_seconds=turn, stall_seconds=stall),
            stage_timer=stage_timer,
            stage="plan",
            watch_path=watch_path,
            watch_tree=watch_tree,
            clock=self.clock,
            sleeper=self.sleeper,
            popen_factory=self.popen,
            killpg=self.killpg,
            getpgid=getpgid or self.getpgid,
            tool_sampler=self.tool_sampler,
        )


class TestRunTurn(unittest.TestCase):
    def test_clean_turn_captures_output_and_starts_new_session(self):
        h = Harness(exit_at=5, stdout=b"hello\n", stderr=b"warn\n")
        result = h.run()
        self.assertIsNone(result.breach)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "hello\n")
        self.assertEqual(result.stderr, "warn\n")
        self.assertTrue(h.proc.popen_kwargs["start_new_session"])
        self.assertEqual(h.signals, [])

    def test_turn_timeout_breach_terminates_the_process_group(self):
        # One degenerate response: tokens keep streaming (so the stall window never fires) but no tool call ever
        # lands on disk, so the response never ends and the per-turn budget fires.
        stream = TickStream()
        h = Harness(exit_at=None, stdout=stream)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "plan.md")
        h.on_tick = lambda now: stream.feed(b"tok ")
        result = h.run(turn=300, stall=90, watch_path=path)
        self.assertIsNotNone(result.breach)
        self.assertEqual(result.breach.budget_name, "turn")
        self.assertEqual(result.breach.budget_seconds, 300)
        self.assertGreaterEqual(result.breach.elapsed_seconds, 300)
        self.assertLess(h.clock.now, 400)
        self.assertEqual(h.signals, [(CHILD_PID, signal.SIGTERM)])
        self.assertEqual(result.returncode, -signal.SIGTERM)
        self.assertTrue(result.stdout.startswith("tok "))

    def test_long_process_with_steady_artefact_progress_is_not_killed(self):
        # C1: a 30-minute runner process (many responses) inside a 45-minute stage, writing its artefact every minute,
        # runs to completion under the default turn and stall budgets.
        h = Harness(exit_at=30 * MINUTE)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "notes.md")

        def grow(now):
            if int(now) % MINUTE == 0:
                with open(path, "a") as f:
                    f.write(f"section at {now}\n")

        h.on_tick = grow
        timer = StageTimer("execute", 45 * MINUTE, clock=h.clock)
        result = run_turn(
            ["agent", "-p", "prompt"],
            turn_budgets=TurnBudgets(),
            stage_timer=timer,
            stage="execute",
            watch_path=path,
            clock=h.clock,
            sleeper=h.sleeper,
            popen_factory=h.popen,
            tool_sampler=h.tool_sampler,
            killpg=h.killpg,
            getpgid=h.getpgid,
        )
        self.assertIsNone(result.breach)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(h.signals, [])
        self.assertGreaterEqual(result.elapsed_seconds, 30 * MINUTE)

    def test_long_process_with_working_tree_progress_only_is_not_killed(self):
        # Executor shape: the notes are written once, then only the working tree changes (every 75 s, inside the stall
        # window, observed by the 15 s tree scan).
        h = Harness(exit_at=30 * MINUTE)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        source = os.path.join(tmp.name, "module.py")

        def edit(now):
            if int(now) % 75 == 0:
                with open(source, "a") as f:
                    f.write("# edit\n")
                os.utime(source, ns=(int(now * 1e9) + 1, int(now * 1e9) + 1))

        h.on_tick = edit
        result = h.run(turn=300, stall=90, watch_tree=tmp.name, stage_timer=StageTimer("execute", 2700, clock=h.clock))
        self.assertIsNone(result.breach)
        self.assertEqual(result.returncode, 0)

    def test_silent_process_that_stops_progressing_is_stopped_by_the_turn_budget(self):
        # A non-streaming runner cannot signal liveness mid-response, so after its last write it gets the full turn
        # budget (not the 90 s stall window) before it is stopped.
        h = Harness(exit_at=None)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "notes.md")

        def grow(now):
            if now <= 10 * MINUTE and int(now) % MINUTE == 0:
                with open(path, "a") as f:
                    f.write("section\n")

        h.on_tick = grow
        result = h.run(turn=300, stall=90, watch_path=path, stage_timer=StageTimer("execute", 2700, clock=h.clock))
        self.assertEqual(result.breach.budget_name, "turn")
        self.assertEqual(result.breach.budget_seconds, 300)
        self.assertGreaterEqual(h.clock.now, 10 * MINUTE + 300)
        self.assertLess(h.clock.now, 10 * MINUTE + 330)
        self.assertEqual(h.signals, [(CHILD_PID, signal.SIGTERM)])

    def test_silent_runner_with_slow_responses_gets_the_full_turn_budget(self):
        # I2: a non-streaming runner (claude -p / pi -p print nothing until exit) whose responses each take 200 s
        # before their write lands is inside the 300 s turn budget and must not be cut off by the 90 s stall window.
        h = Harness(exit_at=20 * MINUTE)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "plan.md")

        def grow(now):
            if int(now) % 200 == 0:
                with open(path, "a") as f:
                    f.write(f"section at {now}\n")

        h.on_tick = grow
        timer = StageTimer("plan", 35 * MINUTE, clock=h.clock)
        result = run_turn(
            ["agent", "-p", "prompt"],
            turn_budgets=TurnBudgets(),
            stage_timer=timer,
            stage="plan",
            watch_path=path,
            clock=h.clock,
            sleeper=h.sleeper,
            popen_factory=h.popen,
            tool_sampler=h.tool_sampler,
            killpg=h.killpg,
            getpgid=h.getpgid,
        )
        self.assertIsNone(result.breach)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(h.signals, [])

    def test_silent_runner_without_progress_is_stopped_by_the_turn_budget(self):
        h = Harness(exit_at=None)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        result = h.run(turn=300, stall=90, watch_path=os.path.join(tmp.name, "plan.md"))
        self.assertEqual(result.breach.budget_name, "turn")
        self.assertGreaterEqual(result.breach.elapsed_seconds, 300)
        self.assertLess(h.clock.now, 330)

    def test_long_tool_call_within_a_turn_is_not_a_breach(self):
        # C4-I3: the model thinks for 150 s, then runs the test suite for 190 s (a busy descendant of the runner),
        # then writes its notes at 400 s and exits. Without tool progress the 300 s turn clock would fire at 300 s.
        h = Harness(exit_at=420)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "notes.md")

        def tick(now):
            if 150 < now <= 340:
                h.tool_cpu[5001] = h.tool_cpu.get(5001, 0.0) + 0.9  # pytest: ~90 % of one core
            elif now == 400:
                with open(path, "a") as f:
                    f.write("## Tests Run\n")

        h.on_tick = tick
        result = h.run(turn=300, stall=90, watch_path=path, stage_timer=StageTimer("execute", 2700, clock=h.clock))
        self.assertIsNone(result.breach)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(h.signals, [])
        self.assertEqual(h.sampled_pid, CHILD_PID)

    def test_long_tool_call_is_not_judged_by_the_streaming_stall_window(self):
        # A runner that streamed before starting the tool is silent while the tool runs; that silence is not a stall.
        stream = TickStream()
        h = Harness(exit_at=300, stdout=stream)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        def tick(now):
            if now <= 20:
                stream.feed(b"tok ")
            elif now <= 210:
                h.tool_cpu[5001] = h.tool_cpu.get(5001, 0.0) + 1.0

        h.on_tick = tick
        result = h.run(turn=300, stall=90, watch_path=os.path.join(tmp.name, "notes.md"))
        self.assertIsNone(result.breach)

    def test_idle_runner_with_idle_descendants_still_breaches_the_turn_budget(self):
        # A long-lived helper that only wakes up occasionally (below the CPU threshold) is not tool progress.
        h = Harness(exit_at=None)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        h.tool_cpu = {5002: 3.0}

        def tick(now):
            h.tool_cpu[5002] += 0.001

        h.on_tick = tick
        result = h.run(turn=300, stall=90, watch_path=os.path.join(tmp.name, "notes.md"))
        self.assertEqual(result.breach.budget_name, "turn")
        self.assertLess(h.clock.now, 330)

    def test_busy_tool_that_never_ends_is_bounded_by_the_stage_ceiling(self):
        h = Harness(exit_at=None)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        def tick(now):
            h.tool_cpu[5003] = h.tool_cpu.get(5003, 0.0) + 1.0

        h.on_tick = tick
        timer = StageTimer("execute", 20 * MINUTE, clock=h.clock)
        result = h.run(turn=300, stall=90, watch_path=os.path.join(tmp.name, "notes.md"), stage_timer=timer)
        self.assertEqual(result.breach.budget_name, "stage")

    def test_unavailable_process_table_keeps_artefact_only_progress(self):
        h = Harness(exit_at=None)
        h.tool_cpu = None
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        result = h.run(turn=300, stall=90, watch_path=os.path.join(tmp.name, "notes.md"))
        self.assertEqual(result.breach.budget_name, "turn")

    def test_streaming_runner_that_goes_silent_is_stalled(self):
        # A runner that streams tokens has shown it can signal liveness, so silence within its response is bounded by
        # the stall window rather than the turn budget.
        stream = TickStream()
        h = Harness(exit_at=None, stdout=stream)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        h.on_tick = lambda now: stream.feed(b"tok ") if now <= 60 else None
        result = h.run(turn=300, stall=90, watch_path=os.path.join(tmp.name, "plan.md"))
        self.assertEqual(result.breach.budget_name, "stall")
        self.assertEqual(result.breach.budget_seconds, 90)
        self.assertGreaterEqual(h.clock.now, 60 + 90)
        self.assertLess(h.clock.now, 60 + 120)
        self.assertEqual(h.signals, [(CHILD_PID, signal.SIGTERM)])

    def test_artefact_progress_resets_the_streaming_stall_window(self):
        # Output marks only the current response as streaming: after a write, a silent next response (prefill before
        # its first token) gets the turn budget again rather than the stall window.
        stream = TickStream()
        h = Harness(exit_at=None, stdout=stream)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "plan.md")

        def tick(now):
            if now <= 30:
                stream.feed(b"tok ")
            elif now == 40:
                with open(path, "a") as f:
                    f.write("section\n")

        h.on_tick = tick
        result = h.run(turn=300, stall=90, watch_path=path)
        self.assertEqual(result.breach.budget_name, "turn")
        self.assertGreaterEqual(h.clock.now, 40 + 300)
        self.assertLess(h.clock.now, 40 + 330)

    def test_stage_ceiling_bounds_a_progressing_process(self):
        h = Harness(exit_at=None)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "plan.md")

        def grow(now):
            if int(now) % MINUTE == 0:
                with open(path, "a") as f:
                    f.write("section\n")

        h.on_tick = grow
        timer = StageTimer("plan", 35 * MINUTE, clock=h.clock)
        result = h.run(turn=300, stall=90, watch_path=path, stage_timer=timer)
        self.assertEqual(result.breach.budget_name, "stage")
        self.assertEqual(result.breach.budget_seconds, 35 * MINUTE)
        self.assertGreaterEqual(h.clock.now, 35 * MINUTE)
        self.assertEqual(h.signals, [(CHILD_PID, signal.SIGTERM)])

    def test_turn_budget_is_not_applied_without_an_observable_response_boundary(self):
        h = Harness(exit_at=20 * MINUTE)
        result = h.run(turn=300, stall=0)
        self.assertIsNone(result.breach)

    def test_supervisor_failure_terminates_the_process_group(self):
        h = Harness(exit_at=None)

        def explode(now):
            if now >= 5:
                raise KeyboardInterrupt

        h.on_tick = explode
        with self.assertRaises(KeyboardInterrupt):
            h.run(turn=300, stall=0)
        self.assertEqual(h.signals, [(CHILD_PID, signal.SIGTERM)])
        self.assertIsNotNone(h.proc.returncode)

    def test_silent_stall_window_is_never_stricter_than_the_turn_budget(self):
        # No output, no watched artefact: nothing could have signalled liveness, so silence is bounded by
        # max(stall, turn) and reported as a stall.
        h = Harness(exit_at=None)
        result = h.run(turn=300, stall=90)
        self.assertEqual(result.breach.budget_name, "stall")
        self.assertEqual(result.breach.budget_seconds, 300)
        self.assertGreaterEqual(result.breach.elapsed_seconds, 300)
        self.assertLess(h.clock.now, 330)

    def test_lingering_pipe_holder_group_is_terminated_after_exit(self):
        # N5: the agent exits but a descendant keeps stdout open, so the reader never sees EOF; the rest of the
        # agent's process group (pgid == agent pid) gets SIGTERM, and the agent's own exit status is kept.
        stream = TickStream()
        h = Harness(exit_at=5, stdout=stream)
        self.addCleanup(stream.close)
        signals = []
        readers = RecordedReaders()

        def killpg(pgid, sig):
            signals.append((pgid, sig))
            stream.close()
            readers.join()

        with readers, mock.patch.object(turns, "READER_JOIN_TIMEOUT_SECONDS", 0.05):
            result = run_turn(
                ["agent", "-p", "prompt"],
                turn_budgets=TurnBudgets(turn_seconds=300, stall_seconds=90),
                clock=h.clock,
                sleeper=h.sleeper,
                popen_factory=h.popen,
                tool_sampler=h.tool_sampler,
                killpg=killpg,
                getpgid=h.getpgid,
            )
        self.assertEqual(signals, [(CHILD_PID, signal.SIGTERM)])
        self.assertIsNone(result.breach)
        self.assertEqual(result.returncode, 0)

    def test_lingering_pipe_holder_ignoring_sigterm_gets_sigkill(self):
        # C3-N2: a descendant that ignores SIGTERM keeps the pipe open; after a bounded reader wait the group gets
        # SIGKILL, and the readers are joined again before the output is read.
        stream = TickStream()
        h = Harness(exit_at=5, stdout=stream)
        self.addCleanup(stream.close)
        signals = []
        readers = RecordedReaders()

        def killpg(pgid, sig):
            signals.append((pgid, sig))
            if sig == signal.SIGKILL:
                stream.close()
                readers.join()

        with readers, mock.patch.object(turns, "READER_JOIN_TIMEOUT_SECONDS", 0.05):
            result = run_turn(
                ["agent", "-p", "prompt"],
                turn_budgets=TurnBudgets(turn_seconds=300, stall_seconds=90),
                clock=h.clock,
                sleeper=h.sleeper,
                popen_factory=h.popen,
                tool_sampler=h.tool_sampler,
                killpg=killpg,
                getpgid=h.getpgid,
            )
        self.assertEqual(signals, [(CHILD_PID, signal.SIGTERM), (CHILD_PID, signal.SIGKILL)])
        self.assertEqual(result.returncode, 0)

    def test_lingering_pipe_holder_that_survives_sigkill_does_not_hang(self):
        stream = TickStream()
        h = Harness(exit_at=5, stdout=stream)
        self.addCleanup(stream.close)
        signals = []
        with mock.patch.object(turns, "READER_JOIN_TIMEOUT_SECONDS", 0.05):
            result = run_turn(
                ["agent", "-p", "prompt"],
                turn_budgets=TurnBudgets(turn_seconds=300, stall_seconds=90),
                clock=h.clock,
                sleeper=h.sleeper,
                popen_factory=h.popen,
                tool_sampler=h.tool_sampler,
                killpg=lambda pgid, sig: signals.append((pgid, sig)),
                getpgid=h.getpgid,
            )
        self.assertEqual(signals, [(CHILD_PID, signal.SIGTERM), (CHILD_PID, signal.SIGKILL)])
        self.assertEqual(result.returncode, 0)

    def test_lingering_pipe_holder_never_signals_its_own_process_group(self):
        stream = TickStream()
        h = Harness(exit_at=5, stdout=stream)
        self.addCleanup(stream.close)
        signals = []
        with mock.patch.object(turns, "READER_JOIN_TIMEOUT_SECONDS", 0.05):
            result = run_turn(
                ["agent", "-p", "prompt"],
                turn_budgets=TurnBudgets(turn_seconds=300, stall_seconds=90),
                clock=h.clock,
                sleeper=h.sleeper,
                popen_factory=h.popen,
                tool_sampler=h.tool_sampler,
                killpg=lambda pgid, sig: signals.append((pgid, sig)),
                getpgid=lambda pid: CHILD_PID,
            )
        self.assertEqual(signals, [])
        self.assertEqual(result.returncode, 0)

    def test_stall_averted_by_artefact_growth(self):
        h = Harness(exit_at=200)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "plan.md")

        def grow(now):
            if int(now) % 60 == 0:
                with open(path, "a") as f:
                    f.write("section\n")

        h.on_tick = grow
        result = h.run(turn=300, stall=90, watch_path=path)
        self.assertIsNone(result.breach)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(h.signals, [])

    def test_zero_stall_disables_stall_detection(self):
        h = Harness(exit_at=200)
        result = h.run(turn=300, stall=0)
        self.assertIsNone(result.breach)

    def test_stage_ceiling_breach(self):
        h = Harness(exit_at=None)
        timer = StageTimer("plan", 30, clock=h.clock)
        result = h.run(turn=300, stall=0, stage_timer=timer)
        self.assertEqual(result.breach.budget_name, "stage")
        self.assertEqual(result.breach.budget_seconds, 30)

    def test_sigkill_after_grace_when_sigterm_is_ignored(self):
        h = Harness(exit_at=None, ignores_sigterm=True)
        result = h.run(turn=300, stall=10)
        self.assertEqual(h.signals, [(CHILD_PID, signal.SIGTERM), (CHILD_PID, signal.SIGKILL)])
        self.assertEqual(result.returncode, -signal.SIGKILL)

    def test_never_signals_its_own_process_group(self):
        h = Harness(exit_at=None)
        result = h.run(turn=300, stall=10, getpgid=lambda pid: OWN_PGID)
        self.assertEqual(h.signals, [])
        self.assertEqual(result.returncode, -signal.SIGTERM)

    def test_start_failure_is_reported_not_raised(self):
        h = Harness()

        def failing_popen(cmd, **kwargs):
            raise FileNotFoundError("agent")

        result = run_turn(["agent"], popen_factory=failing_popen, clock=h.clock, sleeper=h.sleeper)
        self.assertEqual(result.returncode, 127)

    def test_stop_reason_parsing(self):
        self.assertEqual(parse_stop_reason('{"stopReason":"length"}'), "length")
        self.assertEqual(parse_stop_reason('{"stop_reason": "max_tokens"}'), "max_tokens")
        # Prose the agent merely printed is not a runner-reported truncation.
        self.assertIsNone(parse_stop_reason("stop_reason=max_tokens"))
        self.assertIsNone(parse_stop_reason("all good"))
        self.assertTrue(TurnResult(returncode=1, stop_reason="length").truncated)


VALID_PLAN = (
    "# Plan for I-1\n\n## Overall Plan Metrics\n\n| metric | value |\n| --- | --- |\n| p_success_pred | 0.8 |\n"
    "| entropy_pred | 2 |\n| impact_pred | 10 |\n| cost_pred | 1 |\n| learning_value_pred | 1 |\n| ev_pred | 6 |\n\n"
    "## Step 1: Work\n\n" + "Detail. " * 120 + "\n\n### Strategy Rationale\n\nBecause.\n"
)


class TestSuperviseAuthoringTurn(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.path = os.path.join(tmp.name, "plan.md")
        self.ledger = os.path.join(tmp.name, "holon-knowledge", "ledger", "executions.jsonl")
        self.clock = FakeClock()
        self.timer = StageTimer("plan", 2100, clock=self.clock)
        self.prompts = []

    def supervise(self, turns):
        scripted = list(turns)

        def run(cmd, **kwargs):
            self.prompts.append(cmd[-1])
            return scripted.pop(0)()

        return supervise_authoring_turn(
            lambda text: ["agent", text],
            "ORIGINAL",
            artefact_path=self.path,
            artefact_rel="plans/P-1.md",
            spec=PLAN_SPEC,
            stage="plan",
            turn_budgets=TurnBudgets(),
            stage_timer=self.timer,
            run=run,
            context="CONTEXT",
        )

    def write(self, content, breach=None):
        def turn():
            if content is not None:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(content)
            return TurnResult(returncode=-15 if breach else 0, breach=breach)

        return turn

    def test_salvage_plus_successful_resume_records_nothing(self):
        stall = BudgetBreach("plan", "stall", 90, 95)
        half = VALID_PLAN.split("## Step 1")[0]
        outcome = self.supervise(
            [self.write(half, breach=stall), self.write("## Step 1" + VALID_PLAN.split("## Step 1")[1])]
        )
        self.assertEqual(outcome.follow_up, "resume")
        self.assertIsNone(outcome.budget_failure)
        self.assertTrue(outcome.report.complete)
        self.assertTrue(self.prompts[1].startswith("CONTEXT"))
        self.assertIn("partial", self.prompts[1])
        self.assertFalse(os.path.exists(self.ledger))

    def test_salvage_plus_failed_resume_writes_exactly_one_budget_record(self):
        turn_breach = BudgetBreach("plan", "turn", 300, 301)
        outcome = self.supervise(
            [self.write("# Plan for I-1\n", breach=turn_breach), self.write(None, breach=turn_breach)]
        )
        self.assertEqual(len(outcome.turns), 2)
        self.assertIs(outcome.budget_failure, turn_breach)
        # Salvage: the partial file is left on disk untouched.
        with open(self.path) as f:
            self.assertEqual(f.read(), "# Plan for I-1\n")

        record_budget_failure(self.ledger, outcome.budget_failure, record_id="P-1", target="plans/P-1.md")
        with open(self.ledger) as f:
            lines = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(lines), 1)
        record = lines[0]
        self.assertRegex(record["action_id"], r"^ACT-BUDGET-\d+-[0-9a-f]{8}$")
        self.assertEqual(record["entry_type"], "action")
        self.assertEqual(record["outcome"], "failure")
        self.assertEqual(record["category"], "budget")
        self.assertEqual(record["stage"], "plan")
        self.assertEqual(record["budget_name"], "turn")
        self.assertEqual(record["budget_seconds"], 300)
        self.assertEqual(record["elapsed_seconds"], 301)
        self.assertEqual(record["execution_id"], "P-1")

    def test_no_resume_when_stage_has_no_time_left(self):
        stage_breach = BudgetBreach("plan", "stage", 2100, 2100)
        self.clock.now = 2100
        outcome = self.supervise([self.write("# Plan for I-1\n", breach=stage_breach)])
        self.assertEqual(len(outcome.turns), 1)
        self.assertIs(outcome.budget_failure, stage_breach)

    def test_no_reprompt_when_stage_has_no_time_left(self):
        # C3-I1: a clean first turn that finishes at the stage ceiling must not launch a re-prompt that would breach
        # the stage budget on its first poll.
        def finish_at_ceiling():
            with open(self.path, "a", encoding="utf-8") as f:
                f.write("# Plan for I-1\n")
            self.clock.now = 2100
            return TurnResult(returncode=0)

        outcome = self.supervise([finish_at_ceiling])
        self.assertEqual(len(outcome.turns), 1)
        self.assertIsNone(outcome.follow_up)
        self.assertIsNone(outcome.budget_failure)

    def test_breach_with_usable_artefact_is_not_a_failure(self):
        stall = BudgetBreach("plan", "stall", 90, 95)
        outcome = self.supervise([self.write(VALID_PLAN, breach=stall)])
        self.assertEqual(len(outcome.turns), 1)
        self.assertIsNone(outcome.budget_failure)

    def test_clean_turn_with_complete_file_runs_once(self):
        outcome = self.supervise([self.write(VALID_PLAN)])
        self.assertEqual(len(outcome.turns), 1)
        self.assertIsNone(outcome.follow_up)

    def test_clean_turn_without_file_does_not_reprompt(self):
        outcome = self.supervise([self.write(None)])
        self.assertEqual(len(outcome.turns), 1)
        self.assertFalse(outcome.report.exists)


class TestProcessSampler(unittest.TestCase):
    def test_descendants_include_the_tree_and_the_group_but_not_the_runner(self):
        table = {
            100: (1, 100, 50.0, 0.0),  # the runner
            101: (100, 100, 2.0, 0.0),  # a tool
            102: (101, 102, 3.0, 0.0),  # its child in a new session
            103: (1, 100, 4.0, 0.0),  # re-parented, still in the runner's group
            200: (1, 200, 9.0, 0.0),  # unrelated
        }
        self.assertEqual(turns.descendant_cpu_seconds(100, lambda: table), {101: 2.0, 102: 3.0, 103: 4.0})
        self.assertIsNone(turns.descendant_cpu_seconds(100, lambda: None))

    def test_reaped_children_cpu_counts_but_the_runners_own_cpu_does_not(self):
        table = {
            100: (1, 100, 50.0, 7.0),  # the runner: own CPU excluded, its reaped tools' CPU kept
            101: (100, 100, 0.5, 30.0),  # a shell that reaped many short commands
            200: (1, 200, 9.0, 9.0),  # unrelated
        }
        self.assertEqual(turns.descendant_cpu_seconds(100, lambda: table), {100: 7.0, 101: 30.5})
        table[100] = (1, 100, 50.0, 0.0)
        self.assertEqual(turns.descendant_cpu_seconds(100, lambda: table), {101: 30.5})

    def test_wrapper_spawned_agent_binary_is_not_tool_progress(self):
        # C6-N2: the codex Node wrapper (100) spawns the native codex binary (101), which parents the tools.
        table = {
            100: (1, 100, 1.0, 0.0),  # node wrapper ("MainThread")
            101: (100, 100, 80.0, 6.0),  # the real agent: own CPU excluded, its reaped tools' CPU kept
            102: (101, 100, 4.0, 0.0),  # a tool the agent runs
            103: (100, 100, 2.0, 0.0),  # a direct child of the wrapper that is not the agent
        }
        names = {100: "MainThread", 101: "codex", 102: "bash", 103: "git"}
        self.assertEqual(
            turns.descendant_cpu_seconds(100, lambda: table, name_of=names.get), {101: 6.0, 102: 4.0, 103: 2.0}
        )
        # A "codex" process deeper in the tree is a tool, not the agent.
        table[104] = (102, 100, 9.0, 0.0)
        names[104] = "codex"
        self.assertEqual(turns.descendant_cpu_seconds(100, lambda: table, name_of=names.get)[104], 9.0)
        # Without the wrapper exclusion the agent's own CPU would count.
        self.assertEqual(
            turns.descendant_cpu_seconds(100, lambda: table, name_of=names.get, agent_names=frozenset())[101], 86.0
        )
        table[101] = (100, 100, 80.0, 0.0)
        self.assertNotIn(101, turns.descendant_cpu_seconds(100, lambda: table, name_of=names.get))

    def test_process_name_reads_proc_comm(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.makedirs(os.path.join(tmp.name, "77"))
        with open(os.path.join(tmp.name, "77", "comm"), "w") as f:
            f.write("codex\n")
        self.assertEqual(turns.process_name(77, proc_root=tmp.name), "codex")
        self.assertIsNone(turns.process_name(78, proc_root=tmp.name))

    def test_tool_cpu_used_counts_new_processes_in_full_and_ignores_exits(self):
        self.assertEqual(turns.tool_cpu_used({1: 1.0, 2: 5.0}, {1: 1.5, 3: 0.25}), 0.75)

    def test_ps_time_formats(self):
        self.assertEqual(turns._parse_cpu_time("0:01.14"), 1.14)
        self.assertEqual(turns._parse_cpu_time("197:36.23"), 197 * 60 + 36.23)
        self.assertEqual(turns._parse_cpu_time("01:02:03"), 3723.0)
        self.assertEqual(turns._parse_cpu_time("2-00:00:01"), 2 * 86400 + 1.0)
        self.assertEqual(
            turns._parse_ps_table("  1     0     1 197:36.23\n 42 1 42 0:00.50\nbogus\n"),
            {1: (0, 1, 197 * 60 + 36.23, 0.0), 42: (1, 42, 0.5, 0.0)},
        )

    def test_proc_table_parses_linux_stat_files(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        stat = "77 (py thon) (x) S 42 77 77 0 -1 0 0 0 0 0 250 50 120 30 20 0 1 0 0 0 0\n"
        os.makedirs(os.path.join(tmp.name, "77"))
        with open(os.path.join(tmp.name, "77", "stat"), "w") as f:
            f.write(stat)
        os.makedirs(os.path.join(tmp.name, "self"))
        with mock.patch.object(turns.os, "sysconf", return_value=100):
            self.assertEqual(turns._proc_table(tmp.name), {77: (42, 77, 3.0, 1.5)})
        self.assertIsNone(turns._proc_table(os.path.join(tmp.name, "missing")))


class FakeProcFs:
    """A fake Linux ``/proc`` (100 ticks per second) whose stat files a test rewrites as fake time passes."""

    def __init__(self, test):
        self._tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(turns.os, "sysconf", return_value=100)
        patcher.start()
        test.addCleanup(patcher.stop)

    def write(self, pid, ppid, pgid, own_seconds, reaped_seconds):
        os.makedirs(os.path.join(self._tmp.name, str(pid)), exist_ok=True)
        own, reaped = int(own_seconds * 100), int(reaped_seconds * 100)
        with open(os.path.join(self._tmp.name, str(pid), "stat"), "w") as f:
            f.write(f"{pid} (sh) S {ppid} {pgid} {pgid} 0 -1 0 0 0 0 0 {own} 0 {reaped} 0 20 0 1 0 0 0 0\n")

    def sampler(self, pid):
        return turns.descendant_cpu_seconds(pid, lambda: turns._proc_table(self._tmp.name))


class TestReapedChildrenProgress(unittest.TestCase):
    """C5-N1: CPU of descendants that finished between two samples counts through ``cutime``/``cstime`` on Linux."""

    SHELL_PID = CHILD_PID + 1

    def run_with(self, runner_cpu, shell_reaped):
        h = Harness(exit_at=10 * MINUTE)
        proc_fs = FakeProcFs(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        def tick(now):
            proc_fs.write(CHILD_PID, 1, CHILD_PID, runner_cpu(now), 0.0)
            # The tool's shell stays alive; each short command it runs exits before any sample sees it.
            proc_fs.write(self.SHELL_PID, CHILD_PID, CHILD_PID, 0.01, shell_reaped(now))

        tick(0)
        h.on_tick = tick
        h.tool_sampler = proc_fs.sampler
        return h.run(turn=300, stall=90, watch_path=os.path.join(tmp.name, "notes.md"))

    def test_loop_of_short_processes_counts_as_progress(self):
        # About one core spent in 0.3 s commands for the whole 10 min: no turn breach.
        result = self.run_with(runner_cpu=lambda now: 1.0, shell_reaped=lambda now: 0.3 * int(now / 0.3))
        self.assertIsNone(result.breach)
        self.assertEqual(result.returncode, 0)

    def test_idle_runner_still_breaches(self):
        result = self.run_with(runner_cpu=lambda now: 1.0, shell_reaped=lambda now: 2.0)
        self.assertEqual(result.breach.budget_name, "turn")
        self.assertGreaterEqual(result.breach.elapsed_seconds, 300)

    def test_runners_own_cpu_is_not_progress(self):
        # The runner itself burning a core (a degenerate response) is not tool progress.
        result = self.run_with(runner_cpu=lambda now: now, shell_reaped=lambda now: 2.0)
        self.assertEqual(result.breach.budget_name, "turn")
        self.assertGreaterEqual(result.breach.elapsed_seconds, 300)


class TestProcessSamplerOnThisHost(unittest.TestCase):
    def test_busy_grandchild_is_sampled_on_this_host(self):
        # Portability check for the real sampler (/proc on Linux, ps on macOS): a shell runner with a busy child.
        import contextlib
        import subprocess
        import sys
        import time

        # The child burns CPU until it has used 3 s of its own (or 30 s of wall time), and the sampler is polled until
        # it reports more than the asserted 0.3 s, so a loaded host only makes the test slower, never wrong.
        busy = "import time\nend = time.time() + 30\nwhile time.process_time() < 3.0 and time.time() < end: pass\n"
        proc = subprocess.Popen(
            ["/bin/sh", "-c", f'"{sys.executable}" -c "$0"; sleep 0.5', busy], start_new_session=True
        )
        self.addCleanup(proc.wait)

        def stop_group():
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)

        self.addCleanup(stop_group)
        deadline = time.monotonic() + 20
        while True:
            sample = turns.descendant_cpu_seconds(proc.pid)
            if sample is None or (sample and max(sample.values()) > 0.3) or time.monotonic() > deadline:
                break
            time.sleep(0.05)
        if sample is None:
            self.skipTest("no /proc and no ps on this host")
        self.assertNotIn(proc.pid, sample)
        self.assertTrue(sample, "the busy child was not found")
        self.assertGreater(max(sample.values()), 0.3)
