"""Supervised runner turns: streamed output, stall detection, salvage and a single resume (Bean 0139).

:func:`run_turn` replaces the blocking ``subprocess.run`` agent call. The agent is started in its own session so its
whole process group can be stopped without signalling the entrypoint itself; stdout and stderr are drained by reader
threads, so a chatty agent can never block on a full pipe. Liveness is "new output *or* growth of the artefact being
written", which keeps agents that buffer their chat output but write the file incrementally (Bean 0138) alive.

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
_TREE_SKIP_DIRS = frozenset({".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"})

# Agent CLIs report why generation stopped in JSON events or summaries ("stopReason": "length", stop_reason=max_tokens).
_STOP_REASON = re.compile(r"""["']?stop_?reason["']?\s*[:=]\s*["']?([A-Za-z_]+)""", re.IGNORECASE)
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
    """Last stop reason mentioned in the runner output, if any."""
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
        stage_timer: Running stage timer; the turn is also stopped when the stage has no time left.
        stage: Stage name recorded on a breach.
        watch_path: Artefact the agent is writing; growth counts as liveness.
        watch_tree: Optional working tree whose newest file mtime also counts as liveness (scanned at most every
            ``TREE_SCAN_INTERVAL_SECONDS``).
        cwd: Working directory for the agent.
        env: Environment for the agent (``None`` inherits).
        clock, sleeper, popen_factory, killpg, getpgid: Injection seams for tests.
        poll_interval: Seconds between supervision checks.
        grace_seconds: Seconds between SIGTERM and SIGKILL.

    Returns:
        A :class:`TurnResult`; ``breach`` is set when a budget fired and the turn was stopped.
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
    seen_counts = [0, 0]
    seen_file = _file_state(watch_path)
    seen_tree = _tree_mtime(watch_tree)
    last_tree_scan = start
    breach: BudgetBreach | None = None

    while proc.poll() is None:
        now = clock()
        counts = [reader.count for reader in readers]
        if counts != seen_counts:
            seen_counts = counts
            last_activity = now
        file_state = _file_state(watch_path)
        if file_state is not None and file_state != seen_file:
            seen_file = file_state
            last_activity = now
        if watch_tree and now - last_tree_scan >= TREE_SCAN_INTERVAL_SECONDS:
            last_tree_scan = now
            tree_state = _tree_mtime(watch_tree)
            if tree_state is not None and tree_state != seen_tree:
                seen_tree = tree_state
                last_activity = now

        elapsed = now - start
        if elapsed >= budgets.turn_seconds:
            breach = BudgetBreach(stage, "turn", budgets.turn_seconds, elapsed)
        elif budgets.stall_seconds > 0 and now - last_activity >= budgets.stall_seconds:
            breach = BudgetBreach(stage, "stall", budgets.stall_seconds, now - last_activity)
        elif stage_timer is not None and stage_timer.breached():
            breach = stage_timer.breach()
        if breach is not None:
            logger.warning("Stopping agent turn: %s", breach.describe())
            _terminate_group(
                proc,
                sleeper=sleeper,
                clock=clock,
                killpg=killpg,
                getpgid=getpgid,
                grace_seconds=grace_seconds,
                poll_interval=poll_interval,
            )
            break
        sleeper(poll_interval)

    for reader in readers:
        reader.join(timeout=5.0)
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
      partial file and listing what is missing; it is bounded by the turn budget and the stage remainder;
    - after a clean turn that left the file with missing sections or metric rows, exactly one targeted re-prompt is
      issued;
    - a budget failure is reported only when a turn breached and the artefact is still not usable afterwards.

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
    elif report.exists and not report.complete:
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
) -> dict[str, Any]:
    """Append the single ACT-BUDGET action for an unrecovered budget failure via ``append_action_record``.

    Args:
        ledger_path: ``holon-knowledge/ledger/executions.jsonl`` of the workspace.
        breach: The budget that fired last.
        record_id: Plan or execution id the failure belongs to.
        target: Artefact (or other object) the stage was producing.
        clock_elapsed: Stage elapsed seconds, recorded in the error context for diagnosis.
    """
    context = breach.describe()
    if clock_elapsed is not None:
        context += f"; stage elapsed {clock_elapsed:.1f} s"
    record = ActionRecord(
        action_id=generate_action_id("ACT-BUDGET"),
        execution_id=record_id,
        action=f"{breach.stage} stage stopped: {breach.budget_name} budget exceeded and resume did not recover",
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
