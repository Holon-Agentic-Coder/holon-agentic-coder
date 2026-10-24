"""Supervised runner turns: streamed output, stall detection, salvage and a single resume (Bean 0139).

:func:`run_turn` replaces the blocking ``subprocess.run`` agent call. The agent is started in its own session so its
whole process group can be stopped without signalling the entrypoint itself; stdout and stderr are drained by reader
threads, so a chatty agent can never block on a full pipe. Liveness is "new output *or* growth of the artefact being
written", which keeps agents that buffer their chat output but write the file incrementally (Bean 0138) alive.

Bean 0139 defines a *turn* as a single assistant response, not a runner process: one ``claude -p`` / ``pi -p``
invocation performs a whole plan or execution as many responses. The process as a whole is therefore bounded only by the
stage ceiling (and the stall window). The per-turn budget is applied to what is observable about a single response from
outside a non-streaming CLI: the interval between *artefact progress* events (growth of the watched artefact, or a
change in the watched working tree), which are the side effects of the response boundaries (tool calls). Streamed
stdout/stderr keeps a turn out of the stall window but does not close a response, so a model that streams one
degenerate response for longer than the turn budget without writing anything is still stopped. Without a watched
artefact or tree, no response boundary is observable and the turn budget is not applied.

The stall window bounds silence *within* a response, so it is only stricter than the turn budget once the runner has
shown that it streams: after stdout/stderr output in the current response (since the last artefact progress event).
Non-streaming runners (``claude -p`` / ``pi -p`` in text mode print nothing until they exit) cannot emit a stall
signal, so for them the silence window is ``max(stall_seconds, turn_seconds)`` and a single response gets the full
turn budget; with an observable response boundary the turn budget is the one that fires.

:func:`supervise_authoring_turn` adds the authoring policy on top: validate the artefact after the turn, attempt exactly
one follow-up turn (a resume after a breach, or a targeted re-prompt for missing sections), and report a budget failure
only when the artefact is still unusable. :func:`record_budget_failure` is the single place that writes the ACT-BUDGET
action, so a stage failure yields exactly one record.
"""

import contextlib
import logging
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sandbox_executor.artefacts import ArtefactReport, ArtefactSpec, build_reprompt, validate_artefact
from sandbox_executor.budgets import BudgetBreach, Clock, Sleeper, StageTimer, TurnBudgets
from sandbox_executor.ledger import ActionRecord, append_action_record, generate_action_id

logger = logging.getLogger(__name__)

# Seconds between SIGTERM and SIGKILL when a breached turn is stopped.
TERMINATION_GRACE_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 1.0
# Minimum seconds between working-tree scans when a whole tree is watched for liveness (executor turns edit many
# files, not just their notes). Scans skip VCS metadata and dependency directories.
TREE_SCAN_INTERVAL_SECONDS = 15.0
# Real seconds to wait for the pipe readers after the agent exits before treating a descendant as holding them open.
READER_JOIN_TIMEOUT_SECONDS = 5.0
_TREE_SKIP_DIRS = frozenset({".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"})

# Agent CLIs report why generation stopped in structured JSON events ({"stopReason": "length"}). Only the quoted JSON
# key/value form is accepted, so prose the agent merely printed (e.g. "stop_reason=max_tokens") is never mistaken for a
# runner-reported truncation.
_STOP_REASON = re.compile(r'"stop_?reason"\s*:\s*"([A-Za-z_]+)"', re.IGNORECASE)
_TRUNCATION_REASONS = frozenset({"length", "max_tokens", "maxtokens"})


@dataclass
class TurnResult:
    """Outcome of one supervised runner turn; compatible with ``CompletedProcess`` for the fields callers read."""

    returncode: int
    stdout: str = ""
    stderr: str = ""
    breach: BudgetBreach | None = None
    elapsed_seconds: float = 0.0
    stop_reason: str | None = None

    @property
    def truncated(self) -> bool:
        """True when the runner reported that output was cut off by its token limit."""
        return (self.stop_reason or "").lower() in _TRUNCATION_REASONS


def parse_stop_reason(*texts: str) -> str | None:
    """Last stop reason reported as a structured JSON field in the runner output, if any."""
    found = None
    for text in texts:
        for match in _STOP_REASON.finditer(text or ""):
            found = match.group(1)
    return found


def _file_state(path: str | None) -> tuple[int, int] | None:
    if not path:
        return None
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_size, info.st_mtime_ns


def _tree_mtime(root: str | None) -> int | None:
    """Newest file mtime (ns) under ``root``, skipping VCS and dependency directories; None when unreadable."""
    if not root or not os.path.isdir(root):
        return None
    newest = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _TREE_SKIP_DIRS]
        for name in filenames:
            try:
                newest = max(newest, os.stat(os.path.join(dirpath, name)).st_mtime_ns)
            except OSError:
                continue
    return newest


class _StreamReader(threading.Thread):
    """Drains one pipe into a buffer and counts what it read; the poll loop turns new counts into activity."""

    def __init__(self, stream: Any) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._chunks: list[bytes] = []
        self._lock = threading.Lock()
        self.count = 0

    def run(self) -> None:
        if self._stream is None:
            return
        # read1 returns as soon as any bytes are available, so output without a trailing newline (progress dots,
        # streamed tokens) still counts as activity.
        read = getattr(self._stream, "read1", None) or self._stream.read
        try:
            while True:
                chunk = read(65536)
                if not chunk:
                    return
                with self._lock:
                    self._chunks.append(chunk)
                    self.count += len(chunk)
        except (OSError, ValueError):
            # The pipe was closed underneath us (process killed); whatever was read is kept.
            return

    def text(self) -> str:
        with self._lock:
            return b"".join(self._chunks).decode("utf-8", errors="replace")


def _terminate_group(
    proc: Any,
    *,
    sleeper: Sleeper,
    clock: Clock,
    killpg: Callable[[int, int], None],
    getpgid: Callable[[int], int],
    grace_seconds: float,
    poll_interval: float,
) -> None:
    """SIGTERM the child's process group, wait ``grace_seconds``, then SIGKILL it.

    Only the group the child leads is signalled. If that group is somehow our own (the child was not started in a new
    session), fall back to signalling the child alone, so the entrypoint and its salvage path survive.
    """
    try:
        child_pgid = getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        child_pgid = None
    try:
        own_pgid = getpgid(0)
    except OSError:
        own_pgid = None

    def send(sig: int) -> None:
        if child_pgid is not None and child_pgid != own_pgid:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                killpg(child_pgid, sig)
        else:
            logger.warning("Agent shares the entrypoint process group; signalling the agent process only")
            with contextlib.suppress(OSError):
                proc.send_signal(sig)

    send(signal.SIGTERM)
    deadline = clock() + grace_seconds
    while proc.poll() is None and clock() < deadline:
        sleeper(poll_interval)
    if proc.poll() is None:
        send(signal.SIGKILL)
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        logger.warning("Agent process %s did not exit after SIGKILL", getattr(proc, "pid", "?"))


def run_turn(
    cmd: list[str],
    *,
    turn_budgets: TurnBudgets | None = None,
    stage_timer: StageTimer | None = None,
    stage: str = "turn",
    watch_path: str | None = None,
    watch_tree: str | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    clock: Clock = time.monotonic,
    sleeper: Sleeper = time.sleep,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    killpg: Callable[[int, int], None] = os.killpg,
    getpgid: Callable[[int], int] = os.getpgid,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    grace_seconds: float = TERMINATION_GRACE_SECONDS,
) -> TurnResult:
    """Run one agent turn under the turn, stall and stage budgets.

    Args:
        cmd: Runner command line.
        turn_budgets: Turn and stall limits; defaults to :class:`TurnBudgets` defaults.
        stage_timer: Running stage timer; it bounds the whole runner process, which is stopped when the stage has no
            time left.
        stage: Stage name recorded on a breach.
        watch_path: Artefact the agent is writing; growth counts as liveness and closes the current response for the
            turn budget.
        watch_tree: Optional working tree whose newest file mtime also counts as liveness and response progress
            (scanned at most every ``TREE_SCAN_INTERVAL_SECONDS``).
        cwd: Working directory for the agent.
        env: Environment for the agent (``None`` inherits).
        clock, sleeper, popen_factory, killpg, getpgid: Injection seams for tests.
        poll_interval: Seconds between supervision checks.
        grace_seconds: Seconds between SIGTERM and SIGKILL.

    Returns:
        A :class:`TurnResult`; ``breach`` is set when a budget fired and the turn was stopped.

    Budgets: ``turn`` fires when no artefact progress (a response boundary) has been seen for ``turn_seconds`` (only
    when ``watch_path`` or ``watch_tree`` is given); ``stall`` fires after ``stall_seconds`` with no output and no
    artefact progress once the runner has streamed output in the current response, and otherwise (a non-streaming
    runner, which cannot signal liveness mid-response) after ``max(stall_seconds, turn_seconds)``; ``stage`` fires
    when ``stage_timer`` reaches its ceiling. The process elapsed time on its own is never compared with the turn
    budget.
    """
    budgets = turn_budgets or TurnBudgets()
    start = clock()
    try:
        proc = popen_factory(
            cmd,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        return TurnResult(returncode=127, stderr=f"Failed to start agent: {exc}", elapsed_seconds=0.0)

    readers = [_StreamReader(proc.stdout), _StreamReader(proc.stderr)]
    for reader in readers:
        reader.start()

    last_activity = start
    # Start of the current observable response: reset by artefact or tree progress, never by streamed output.
    last_progress = start
    turn_observable = bool(watch_path or watch_tree)
    # True once the runner has streamed stdout/stderr in the current response; only then is the (shorter) stall window
    # a meaningful liveness check. Reset by every artefact or tree progress event (a response boundary).
    streaming = False
    silent_stall_seconds = max(budgets.stall_seconds, budgets.turn_seconds)
    seen_counts = [0, 0]
    seen_file = _file_state(watch_path)
    seen_tree = _tree_mtime(watch_tree)
    last_tree_scan = start
    breach: BudgetBreach | None = None

    def stop_group() -> None:
        _terminate_group(
            proc,
            sleeper=sleeper,
            clock=clock,
            killpg=killpg,
            getpgid=getpgid,
            grace_seconds=grace_seconds,
            poll_interval=poll_interval,
        )

    try:
        while proc.poll() is None:
            now = clock()
            counts = [reader.count for reader in readers]
            if counts != seen_counts:
                seen_counts = counts
                last_activity = now
                streaming = True
            file_state = _file_state(watch_path)
            if file_state is not None and file_state != seen_file:
                seen_file = file_state
                last_activity = last_progress = now
                streaming = False
            if watch_tree and now - last_tree_scan >= TREE_SCAN_INTERVAL_SECONDS:
                last_tree_scan = now
                tree_state = _tree_mtime(watch_tree)
                if tree_state is not None and tree_state != seen_tree:
                    seen_tree = tree_state
                    last_activity = last_progress = now
                    streaming = False

            stall_window = budgets.stall_seconds if streaming else silent_stall_seconds
            if turn_observable and now - last_progress >= budgets.turn_seconds:
                breach = BudgetBreach(stage, "turn", budgets.turn_seconds, now - last_progress)
            elif budgets.stall_seconds > 0 and now - last_activity >= stall_window:
                breach = BudgetBreach(stage, "stall", stall_window, now - last_activity)
            elif stage_timer is not None and stage_timer.breached():
                breach = stage_timer.breach()
            if breach is not None:
                logger.warning("Stopping agent turn: %s", breach.describe())
                stop_group()
                break
            sleeper(poll_interval)
    except BaseException:
        # Never leave the agent's process group running when supervision itself fails or is interrupted.
        with contextlib.suppress(Exception):
            if proc.poll() is None:
                stop_group()
        raise

    for reader in readers:
        reader.join(timeout=READER_JOIN_TIMEOUT_SECONDS)
    if any(reader.is_alive() for reader in readers):
        # A grandchild still holds stdout/stderr open after the agent exited: stop the rest of its group.
        logger.warning("Agent pipes still open after exit; terminating the remaining process group")
        # start_new_session=True makes the agent's pid its process group id, which outlives the reaped agent.
        with contextlib.suppress(OSError):
            if proc.pid != getpgid(0):
                # SIGTERM, a bounded wait for the readers, then SIGKILL for a holder that ignores SIGTERM (mirrors
                # _terminate_group); a final bounded wait lets the readers drain what the holder left in the pipes.
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    killpg(proc.pid, sig)
                    for reader in readers:
                        reader.join(timeout=READER_JOIN_TIMEOUT_SECONDS)
                    if not any(reader.is_alive() for reader in readers):
                        break
        if any(reader.is_alive() for reader in readers):
            logger.warning("Agent pipes still open after SIGKILL; returning the output read so far")
    stdout, stderr = readers[0].text(), readers[1].text()
    returncode = proc.returncode if proc.returncode is not None else -1
    return TurnResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        breach=breach,
        elapsed_seconds=max(0.0, clock() - start),
        stop_reason=parse_stop_reason(stdout, stderr),
    )


@dataclass
class AuthoringOutcome:
    """What :func:`supervise_authoring_turn` did and the state of the artefact afterwards."""

    turns: list[TurnResult] = field(default_factory=list)
    report: ArtefactReport | None = None
    follow_up: str | None = None  # "resume" after a breach, "reprompt" for missing sections, or None
    budget_failure: BudgetBreach | None = None

    @property
    def last(self) -> TurnResult:
        """The most recent turn."""
        return self.turns[-1]

    @property
    def first(self) -> TurnResult:
        """The original turn (the one that did the stage's work; follow-ups only repair the artefact)."""
        return self.turns[0]

    @property
    def breach(self) -> BudgetBreach | None:
        """The last budget that fired in any turn, recovered or not."""
        breaches = [result.breach for result in self.turns if result.breach is not None]
        return breaches[-1] if breaches else None


TurnRunner = Callable[..., TurnResult]


def supervise_authoring_turn(
    build_cmd: Callable[[str], list[str]],
    prompt: str,
    *,
    artefact_path: str,
    artefact_rel: str,
    spec: ArtefactSpec,
    stage: str,
    turn_budgets: TurnBudgets,
    stage_timer: StageTimer,
    run: TurnRunner = run_turn,
    modify_code: bool = True,
    context: str = "",
    watch_tree: str | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> AuthoringOutcome:
    """Run an authoring turn and at most one follow-up, then report whether a budget failure must be recorded.

    Policy:

    - after a breached turn, whatever is on disk is kept (salvage) and exactly one resume turn is attempted, naming the
      partial file and listing what is missing; it runs under the same stall, turn and stage budgets;
    - after a clean turn that left the file with missing sections or metric rows, exactly one targeted re-prompt is
      issued, provided the stage still has time left (like the resume);
    - a budget failure is reported only when a turn breached and the artefact is still not usable afterwards. Callers
      whose follow-up cannot finish the stage's real work (the executor's resume only touches its notes) must treat a
      breach of :attr:`AuthoringOutcome.first` as a failure regardless, and a breach confined to a follow-up of a clean
      first turn as a notes-only problem (see the executor entrypoint).

    Runner CLIs start every invocation as a fresh session, so ``context`` (the task description without the original
    write protocol) is prepended to the follow-up instruction. The caller records the failure (see
    :func:`record_budget_failure`) so that it happens exactly once.
    """
    outcome = AuthoringOutcome()

    def turn(text: str) -> TurnResult:
        result = run(
            build_cmd(text),
            turn_budgets=turn_budgets,
            stage_timer=stage_timer,
            stage=stage,
            watch_path=artefact_path,
            watch_tree=watch_tree,
            cwd=cwd,
            env=env,
        )
        outcome.turns.append(result)
        return result

    def run_follow_up(instruction: str) -> TurnResult:
        return turn(f"{context}\n\n{instruction}" if context else instruction)

    first = turn(prompt)
    report = validate_artefact(artefact_path, spec)
    if first.breach is not None:
        logger.warning(
            "Agent turn breached its %s budget; salvaged %s bytes of %s",
            first.breach.budget_name,
            report.size,
            artefact_rel,
        )
        if not report.complete and stage_timer.remaining() > 0:
            outcome.follow_up = "resume"
            run_follow_up(build_reprompt(report.missing_labels(), artefact_rel, partial=True, modify_code=modify_code))
            report = validate_artefact(artefact_path, spec)
    elif report.exists and not report.complete and stage_timer.remaining() > 0:
        outcome.follow_up = "reprompt"
        logger.info("Re-prompting once for missing content: %s", report.describe())
        run_follow_up(build_reprompt(report.missing_labels(), artefact_rel, modify_code=modify_code))
        report = validate_artefact(artefact_path, spec)

    outcome.report = report
    breaches = [result.breach for result in outcome.turns if result.breach is not None]
    if breaches and not report.usable:
        outcome.budget_failure = breaches[-1]
    return outcome


def record_budget_failure(
    ledger_path: str,
    breach: BudgetBreach,
    *,
    record_id: str,
    target: str,
    clock_elapsed: float | None = None,
    reason: str = "resume did not recover",
) -> dict[str, Any]:
    """Append the single ACT-BUDGET action for an unrecovered budget failure via ``append_action_record``.

    Args:
        ledger_path: ``holon-knowledge/ledger/executions.jsonl`` of the workspace.
        breach: The budget that fired last.
        record_id: Plan or execution id the failure belongs to.
        target: Artefact (or other object) the stage was producing.
        clock_elapsed: Stage elapsed seconds, recorded in the error context for diagnosis.
        reason: Why the breach was not recovered, appended to the action text.
    """
    context = breach.describe()
    if clock_elapsed is not None:
        context += f"; stage elapsed {clock_elapsed:.1f} s"
    record = ActionRecord(
        action_id=generate_action_id("ACT-BUDGET"),
        execution_id=record_id,
        action=f"{breach.stage} stage stopped: {breach.budget_name} budget exceeded and {reason}",
        category="budget",
        target=target,
        outcome="failure",
        event_type="budget_breach",
        labels=["budget", breach.stage, breach.budget_name],
        error_context=context,
        stage=breach.stage,
        budget_name=breach.budget_name,
        budget_seconds=round(breach.budget_seconds, 3),
        elapsed_seconds=round(breach.elapsed_seconds, 3),
    )
    os.makedirs(os.path.dirname(ledger_path) or ".", exist_ok=True)
    return append_action_record(ledger_path, record)
