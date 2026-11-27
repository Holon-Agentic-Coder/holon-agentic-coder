# why: a turn is one assistant response, not a runner process; a non-streaming CLI shows a response boundary only via
# why: its side effects (artefact growth, a working-tree change, tool CPU), so the turn budget runs between those.
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
from sandbox_executor.budgets import DEFAULT_TURN_SECONDS, BudgetBreach, Clock, Sleeper, StageTimer, TurnBudgets
from sandbox_executor.ledger import ActionRecord, append_action_record, generate_action_id

logger = logging.getLogger(__name__)

TERMINATION_GRACE_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 1.0
# why: executor turns edit many files, not just their notes, so the whole tree is scanned, but at most this often.
TREE_SCAN_INTERVAL_SECONDS = 15.0
# why: 5 % of one core between samples counts as tool progress, so idle helpers that wake up now and then do not.
TOOL_SCAN_INTERVAL_SECONDS = 10.0
TOOL_CPU_PROGRESS_SECONDS = 0.5
PS_TIMEOUT_SECONDS = 5.0
READER_JOIN_TIMEOUT_SECONDS = 5.0
# why: a huge argv in a live-child or background-command warning must not flood the log.
COMMAND_DISPLAY_CHARS = 200
_TREE_SKIP_DIRS = frozenset({".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"})

# why: only the quoted JSON key/value form counts, so prose the agent printed ("stop_reason=max_tokens") is never
# why: mistaken for a runner-reported truncation.
_STOP_REASON = re.compile(r'"stop_?reason"\s*:\s*"([A-Za-z_]+)"', re.IGNORECASE)
_TRUNCATION_REASONS = frozenset({"length", "max_tokens", "maxtokens"})


@dataclass
class TurnResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    breach: BudgetBreach | None = None
    elapsed_seconds: float = 0.0
    stop_reason: str | None = None
    background_commands: tuple[str, ...] = ()

    @property
    def truncated(self) -> bool:
        return (self.stop_reason or "").lower() in _TRUNCATION_REASONS


def parse_stop_reason(*texts: str) -> str | None:
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


# why: rows are (ppid, pgid, own CPU, reaped-children CPU); ps cannot report reaped-children CPU, so it is 0.0 on macOS.
ProcessTable = dict[int, tuple[int, int, float, float]]
ToolSampler = Callable[[int], dict[int, float] | None]
ChildLister = Callable[[int], dict[int, str] | None]


def _proc_table(proc_root: str = "/proc") -> ProcessTable | None:
    if not os.path.isdir(proc_root):
        return None
    try:
        ticks = float(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, ValueError, OSError):
        ticks = 100.0
    table: ProcessTable = {}
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return None
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(proc_root, entry, "stat"), encoding="utf-8", errors="replace") as handle:
                stat = handle.read()
        except OSError:
            continue
        # why: the command name (field 2) is parenthesised and may contain spaces or ')'.
        fields = stat[stat.rfind(")") + 2 :].split()
        try:
            # why: fields 14-17 of stat(5) are utime, stime, cutime and cstime.
            own = (int(fields[11]) + int(fields[12])) / ticks
            reaped = (int(fields[13]) + int(fields[14])) / ticks
            table[int(entry)] = (int(fields[1]), int(fields[2]), own, reaped)
        except (IndexError, ValueError):
            continue
    return table or None


def _parse_cpu_time(text: str) -> float:
    days = 0.0
    if "-" in text:
        day_text, text = text.split("-", 1)
        days = float(day_text)
    seconds = 0.0
    for part in text.split(":"):
        seconds = seconds * 60 + float(part)
    return days * 86400 + seconds


def _parse_ps_table(output: str) -> ProcessTable:
    table: ProcessTable = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        try:
            table[int(parts[0])] = (int(parts[1]), int(parts[2]), _parse_cpu_time(parts[3]), 0.0)
        except ValueError:
            continue
    return table


def _ps_table() -> ProcessTable | None:
    try:
        result = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,pgid=,time="],
            capture_output=True,
            text=True,
            timeout=PS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return _parse_ps_table(result.stdout) or None


def process_table() -> ProcessTable | None:
    table = _proc_table()
    return table if table is not None else _ps_table()


# why: verified in the holon/agent-codex image: the codex Node wrapper spawns the native codex binary running the model
# why: loop, so that direct child is the agent and only the CPU of tools it reaped counts as tool progress.
WRAPPED_AGENT_PROCESS_NAMES = frozenset({"codex"})


def process_name(pid: int, proc_root: str = "/proc") -> str | None:
    try:
        with open(os.path.join(proc_root, str(pid), "comm"), encoding="utf-8", errors="replace") as handle:
            return handle.read().strip() or None
    except OSError:
        pass
    if os.path.isdir(proc_root):
        return None
    try:
        result = subprocess.run(
            ["ps", "-o", "comm=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=PS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    name = result.stdout.strip() if result.returncode == 0 else ""
    return os.path.basename(name)[:15] or None


def _children_of(table: ProcessTable) -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for pid, (ppid, _pgid, _cpu, _reaped) in table.items():
        children.setdefault(ppid, []).append(pid)
    return children


def _descendant_members(table: ProcessTable, root_pid: int) -> set[int]:
    children = _children_of(table)
    members = {root_pid} | {pid for pid, (_ppid, pgid, _cpu, _reaped) in table.items() if pgid == root_pid}
    stack = list(members)
    while stack:
        for child in children.get(stack.pop(), ()):
            if child not in members:
                members.add(child)
                stack.append(child)
    return members


def _agent_pids(
    table: ProcessTable, root_pid: int, name_of: Callable[[int], str | None], agent_names: frozenset[str]
) -> set[int]:
    agent_pids = {root_pid}
    if agent_names:
        agent_pids |= {pid for pid in _children_of(table).get(root_pid, ()) if name_of(pid) in agent_names}
    return agent_pids


def descendant_cpu_seconds(
    root_pid: int,
    table_source: Callable[[], ProcessTable | None] = process_table,
    name_of: Callable[[int], str | None] = process_name,
    agent_names: frozenset[str] = WRAPPED_AGENT_PROCESS_NAMES,
) -> dict[int, float] | None:
    table = table_source()
    if table is None:
        return None
    members = _descendant_members(table, root_pid)
    agent_pids = _agent_pids(table, root_pid, name_of, agent_names)
    sample = {pid: table[pid][2] + table[pid][3] for pid in members - agent_pids if pid in table}
    for pid in agent_pids:
        reaped = table[pid][3] if pid in table else 0.0
        if reaped > 0:
            sample[pid] = reaped
    return sample


def process_command(pid: int, proc_root: str = "/proc") -> str | None:
    try:
        with open(os.path.join(proc_root, str(pid), "cmdline"), "rb") as handle:
            raw = handle.read()
    except OSError:
        raw = None
    if raw is not None:
        command = " ".join(part for part in raw.decode("utf-8", errors="replace").split("\0") if part).strip()
    elif os.path.isdir(proc_root):
        return None
    else:
        command = _ps_command(pid)
    return command[:COMMAND_DISPLAY_CHARS] or None


def _ps_command(pid: int) -> str:
    try:
        result = subprocess.run(
            ["ps", "-o", "args=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=PS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def descendant_commands(
    root_pid: int,
    table_source: Callable[[], ProcessTable | None] = process_table,
    name_of: Callable[[int], str | None] = process_name,
    command_of: Callable[[int], str | None] = process_command,
    agent_names: frozenset[str] = WRAPPED_AGENT_PROCESS_NAMES,
) -> dict[int, str] | None:
    table = table_source()
    if table is None:
        return None
    members = _descendant_members(table, root_pid) - _agent_pids(table, root_pid, name_of, agent_names)
    commands = {pid: command_of(pid) for pid in sorted(members) if pid in table}
    return {pid: command for pid, command in commands.items() if command}


def describe_commands(commands: Mapping[int, str]) -> str:
    return "; ".join(f"{commands[pid]} (pid {pid})" for pid in sorted(commands))


def tool_cpu_used(previous: Mapping[int, float], current: Mapping[int, float]) -> float:
    return sum(max(0.0, cpu - previous.get(pid, 0.0)) for pid, cpu in current.items())


class _StreamReader(threading.Thread):
    def __init__(self, stream: Any) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._chunks: list[bytes] = []
        self._lock = threading.Lock()
        self.count = 0

    def run(self) -> None:
        if self._stream is None:
            return
        # why: read1 returns as soon as any bytes arrive, so output with no newline (progress dots) is activity.
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
            # why: the pipe closes underneath the reader when the process is killed; what was read is kept.
            return

    def text(self) -> str:
        with self._lock:
            return b"".join(self._chunks).decode("utf-8", errors="replace")


# why: only the group the child leads is signalled, so the entrypoint and its salvage path survive.
def _terminate_group(  # noqa: PLR0913
    proc: Any,
    *,
    sleeper: Sleeper,
    clock: Clock,
    killpg: Callable[[int, int], None],
    getpgid: Callable[[int], int],
    grace_seconds: float,
    poll_interval: float,
) -> None:
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


# why: docker's promotional and diagnostic hints would otherwise pollute captured agent output.
def build_turn_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    effective_env = dict(os.environ) if env is None else dict(env)
    effective_env["DOCKER_CLI_HINTS"] = "false"
    return effective_env


# why: a non-streaming runner cannot signal liveness mid-response, so it gets the turn budget, and silence stays bounded
# why: by DEFAULT_TURN_SECONDS when the turn timeout is unlimited.
def silence_window(budgets: TurnBudgets, streaming: bool) -> float:
    if streaming:
        return budgets.stall_seconds
    turn = DEFAULT_TURN_SECONDS if budgets.turn_seconds is None else budgets.turn_seconds
    return max(budgets.stall_seconds, turn)


def _turn_breach(
    budgets: TurnBudgets, turn_observable: bool, now: float, last_progress: float, stage: str
) -> BudgetBreach | None:
    if budgets.turn_seconds is None or not turn_observable or now - last_progress < budgets.turn_seconds:
        return None
    return BudgetBreach(stage, "turn", budgets.turn_seconds, now - last_progress)


def _live_children(child_lister: ChildLister | None, pid: int) -> dict[int, str]:
    if child_lister is None:
        return {}
    try:
        return child_lister(pid) or {}
    except (OSError, subprocess.SubprocessError):
        return {}


# why: a live child (a quiet foreground tool or a persistent helper) disables the short streaming stall window, leaving
# why: the turn timeout to bound the turn.
def _stall_window(budgets: TurnBudgets, streaming: bool, has_live_child: bool) -> float:
    return silence_window(budgets, streaming and not has_live_child)


# why: a command backgrounded with `&` stays in the runner's process group and cannot report back after the turn.
def _report_background_commands(child_lister: ChildLister | None, pid: int) -> tuple[str, ...]:
    leftovers = _live_children(child_lister, pid)
    if leftovers:
        logger.warning(
            "Agent turn ended with background command(s) still running: %s; a backgrounded command cannot report back "
            "after the turn ends, so run tests and lint in the foreground",
            describe_commands(leftovers),
        )
    return tuple(leftovers[child] for child in sorted(leftovers))


def run_turn(  # noqa: C901, PLR0912, PLR0913, PLR0915
    cmd: list[str],
    *,
    stdin_file: str | None = None,
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
    tool_sampler: ToolSampler | None = descendant_cpu_seconds,
    child_lister: ChildLister | None = descendant_commands,
) -> TurnResult:
    budgets = turn_budgets or TurnBudgets()
    start = clock()
    effective_env = build_turn_env(env)
    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "env": effective_env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "start_new_session": True,
    }
    try:
        if stdin_file is not None and os.path.exists(stdin_file):
            with open(stdin_file, "rb") as stdin_stream:
                proc = popen_factory(cmd, stdin=stdin_stream, **popen_kwargs)
        else:
            proc = popen_factory(cmd, **popen_kwargs)
    except OSError as exc:
        return TurnResult(returncode=127, stderr=f"Failed to start agent: {exc}", elapsed_seconds=0.0)

    readers = [_StreamReader(proc.stdout), _StreamReader(proc.stderr)]
    for reader in readers:
        reader.start()

    last_activity = start
    # why: streamed output does not close a response, so only artefact, tree or tool progress resets this.
    last_progress = start
    turn_observable = bool(watch_path or watch_tree)
    # why: the shorter stall window is a meaningful liveness check only once the runner streamed in this response.
    streaming = False
    seen_counts = [0, 0]
    seen_file = _file_state(watch_path)
    seen_tree = _tree_mtime(watch_tree)
    last_tree_scan = start
    seen_tools: dict[int, float] | None = None
    last_tool_scan: float | None = None
    live_children: dict[int, str] = {}
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
            if (tool_sampler is not None or child_lister is not None) and (
                last_tool_scan is None or now - last_tool_scan >= TOOL_SCAN_INTERVAL_SECONDS
            ):
                last_tool_scan = now
                live_children = _live_children(child_lister, proc.pid)
                sample = tool_sampler(proc.pid) if tool_sampler is not None else None
                if sample is not None:
                    # why: a tool call is a response boundary, so its CPU time is progress, not part of a response.
                    if seen_tools is not None and tool_cpu_used(seen_tools, sample) >= TOOL_CPU_PROGRESS_SECONDS:
                        last_activity = last_progress = now
                        streaming = False
                    seen_tools = sample

            stall_window = _stall_window(budgets, streaming, bool(live_children))
            breach = _turn_breach(budgets, turn_observable, now, last_progress, stage)
            if breach is None and budgets.stall_seconds > 0 and now - last_activity >= stall_window:
                breach = BudgetBreach(stage, "stall", stall_window, now - last_activity)
            if breach is None and stage_timer is not None and stage_timer.breached():
                breach = stage_timer.breach()
            if breach is not None:
                logger.warning("Stopping agent turn: %s", breach.describe())
                if live_children:
                    logger.warning("Live command(s) at stop: %s", describe_commands(live_children))
                stop_group()
                break
            sleeper(poll_interval)
    except BaseException:
        # why: never leave the agent's process group running when supervision itself fails or is interrupted.
        with contextlib.suppress(Exception):
            if proc.poll() is None:
                stop_group()
        raise

    # why: observed before the pipe-holder path below may stop the leftovers.
    background_commands = _report_background_commands(child_lister, proc.pid) if breach is None else ()
    for reader in readers:
        reader.join(timeout=READER_JOIN_TIMEOUT_SECONDS)
    if any(reader.is_alive() for reader in readers):
        logger.warning("Agent pipes still open after exit; terminating the remaining process group")
        # why: start_new_session=True makes the agent's pid its process group id, which outlives the reaped agent.
        with contextlib.suppress(OSError):
            if proc.pid != getpgid(0):
                # why: SIGKILL only for a holder that ignores SIGTERM; the final wait lets the readers drain the pipes.
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
        background_commands=background_commands,
    )


@dataclass
class AuthoringOutcome:
    turns: list[TurnResult] = field(default_factory=list)
    report: ArtefactReport | None = None
    follow_up: str | None = None
    budget_failure: BudgetBreach | None = None

    @property
    def last(self) -> TurnResult:
        return self.turns[-1]

    @property
    def first(self) -> TurnResult:
        return self.turns[0]

    @property
    def breach(self) -> BudgetBreach | None:
        breaches = [result.breach for result in self.turns if result.breach is not None]
        return breaches[-1] if breaches else None


TurnRunner = Callable[..., TurnResult]


# why: runner CLIs start every invocation as a fresh session, so the context is prepended to the one follow-up; callers
# why: whose follow-up cannot finish the stage's real work treat a breach of the first turn as a failure regardless.
def supervise_authoring_turn(  # noqa: PLR0913
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
    stdin_file: str | None = None,
    prompt_file: str | None = None,
    on_turn_boundary: Callable[[int], str | None] | None = None,
) -> AuthoringOutcome:
    outcome = AuthoringOutcome()

    def turn(text: str) -> TurnResult:
        if prompt_file is not None:
            prompt_dir = os.path.dirname(os.path.abspath(prompt_file))
            if prompt_dir:
                os.makedirs(prompt_dir, exist_ok=True)
            with open(prompt_file, "w", encoding="utf-8") as pf:
                pf.write(text)
        result = run(
            build_cmd(text),
            stdin_file=stdin_file,
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
        notice = (on_turn_boundary(len(outcome.turns)) or "").strip() if on_turn_boundary is not None else ""
        full_instruction = f"{instruction}\n\n{notice}" if notice else instruction
        return turn(f"{context}\n\n{full_instruction}" if context else full_instruction)

    first = turn(prompt)
    report = validate_artefact(artefact_path, spec)
    if first.breach is not None:
        logger.warning(
            "Agent turn breached its %s budget; salvaged %s bytes of %s",
            first.breach.budget_name,
            report.size,
            artefact_rel,
        )
        if not report.complete and stage_timer.has_time_left():
            outcome.follow_up = "resume"
            run_follow_up(build_reprompt(report.missing_labels(), artefact_rel, partial=True, modify_code=modify_code))
            report = validate_artefact(artefact_path, spec)
    elif report.exists and not report.complete and stage_timer.has_time_left():
        outcome.follow_up = "reprompt"
        logger.info("Re-prompting once for missing content: %s", report.describe())
        run_follow_up(build_reprompt(report.missing_labels(), artefact_rel, modify_code=modify_code))
        report = validate_artefact(artefact_path, spec)

    outcome.report = report
    breaches = [result.breach for result in outcome.turns if result.breach is not None]
    if breaches and not report.usable:
        outcome.budget_failure = breaches[-1]
    return outcome


# why: the single writer of the ACT-BUDGET action, so a stage failure yields exactly one record.
def record_budget_failure(  # noqa: PLR0913
    ledger_path: str,
    breach: BudgetBreach,
    *,
    record_id: str,
    target: str,
    clock_elapsed: float | None = None,
    reason: str = "resume did not recover",
) -> dict[str, Any]:
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
