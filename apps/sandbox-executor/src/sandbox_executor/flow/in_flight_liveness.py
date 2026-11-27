from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.github import merge
from sandbox_executor.git import run_git as _run_git
from sandbox_executor.redaction import redact_agent_secrets

GitRunner = Callable[[list[str], str], subprocess.CompletedProcess[str]]
ProcessCmdlines = Callable[[], Iterable[str]]

_PATH_LEADING_BOUNDARIES = frozenset({" ", "\t", "\n", "\r", "=", ":", "'", '"'})
_PATH_TRAILING_BOUNDARIES = frozenset({" ", "\t", "\n", "\r", "/", "=", ":", "'", '"'})
_CLOSED_PR_STATES = frozenset({"MERGED", "CLOSED"})


@dataclass(frozen=True)
class AdmissionUnavailable:
    reason: str


def host_git(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return _run_git(args, cwd=cwd, check=False)


def command_failure(command: str, result: subprocess.CompletedProcess[str]) -> AdmissionUnavailable:
    detail = redact_agent_secrets((result.stderr or "").strip())[:300]
    return AdmissionUnavailable(f"'{command}' failed: {detail or 'no output'}")


def fetch_prune_origin(repo_dir: str, run_git: GitRunner | None = None) -> AdmissionUnavailable | None:
    try:
        result = (run_git or host_git)(["fetch", "--prune", "origin"], repo_dir)
    except (OSError, TimeoutError) as exc:
        detail = redact_agent_secrets(str(exc))[:300]
        return AdmissionUnavailable(f"'git fetch --prune origin' failed: {detail or 'no output'}")
    if result.returncode != 0:
        return command_failure("git fetch --prune origin", result)
    return None


def origin_branch_ref(repo_dir: str, branch: str, run_git: GitRunner | None = None) -> str | None:
    if not branch or not branch.startswith("I-"):
        return None
    ref = f"refs/remotes/origin/{branch}"
    result = (run_git or host_git)(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], repo_dir)
    return ref if result.returncode == 0 else None


def _is_zombie_proc(pid_dir: str) -> bool:
    try:
        with open(os.path.join(pid_dir, "stat"), encoding="utf-8", errors="replace") as handle:
            stat = handle.read()
    except OSError:
        return True
    closing = stat.rfind(")")
    if closing < 0:
        return False
    after = stat[closing + 1 :].lstrip()
    return after.startswith("Z")


def _read_proc_cmdline(pid_dir: str) -> str | None:
    try:
        with open(os.path.join(pid_dir, "cmdline"), "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    if not raw:
        return None
    parts = [part.decode("utf-8", errors="replace") for part in raw.split(b"\x00") if part]
    return " ".join(parts) if parts else None


def _proc_command_lines(own_pid: int) -> list[str]:
    lines: list[str] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return lines
    for entry in entries:
        if not entry.isdigit() or int(entry) == own_pid:
            continue
        pid_dir = os.path.join("/proc", entry)
        if _is_zombie_proc(pid_dir):
            continue
        cmdline = _read_proc_cmdline(pid_dir)
        if cmdline:
            lines.append(cmdline)
    return lines


def _ps_command_lines(own_pid: int) -> list[str]:
    try:
        result = subprocess.run(["ps", "-A", "-o", "pid=,stat=,args="], capture_output=True, text=True, check=False)
    except OSError:
        return []
    if result.returncode != 0:
        return []
    lines: list[str] = []
    for raw in (result.stdout or "").splitlines():
        parts = raw.strip().split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or int(parts[0]) == own_pid:
            continue
        if "Z" in parts[1]:
            continue
        lines.append(parts[2])
    return lines


def process_command_lines() -> list[str]:
    own_pid = os.getpid()
    if os.path.isdir("/proc"):
        return _proc_command_lines(own_pid)
    return _ps_command_lines(own_pid)


def _normalised_worktree_paths(worktree: str) -> tuple[str, ...]:
    candidates: list[str] = []
    for raw in (os.path.abspath(worktree), os.path.realpath(worktree)):
        stripped = raw.rstrip(os.sep) or raw
        if stripped and stripped not in candidates:
            candidates.append(stripped)
    return tuple(candidates)


def _cmdline_mentions_path(cmdline: str, path: str) -> bool:
    start = 0
    while (index := cmdline.find(path, start)) != -1:
        end = index + len(path)
        leading_ok = index == 0 or cmdline[index - 1] in _PATH_LEADING_BOUNDARIES
        trailing_ok = end == len(cmdline) or cmdline[end] in _PATH_TRAILING_BOUNDARIES
        if leading_ok and trailing_ok:
            return True
        start = index + 1
    return False


def worktree_has_live_runner(worktree: str, cmdlines: ProcessCmdlines | None = None) -> bool:
    candidates = _normalised_worktree_paths(worktree)
    if not candidates:
        return False
    source = cmdlines if cmdlines is not None else process_command_lines
    return any(_cmdline_mentions_path(line, path) for line in source() for path in candidates)


def _parse_pr_list(result: subprocess.CompletedProcess[str]) -> list[dict[str, Any]] | AdmissionUnavailable:
    if result.returncode != 0:
        return command_failure("gh pr list", result)
    try:
        data = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return AdmissionUnavailable("'gh pr list' returned output that is not JSON")
    if not isinstance(data, list):
        return AdmissionUnavailable("'gh pr list' returned JSON that is not a list")
    return [item for item in data if isinstance(item, dict)]


def pull_request_is_closed_or_merged(
    repo_dir: str, head: str, run_gh: merge.GhRunner | None = None
) -> bool | AdmissionUnavailable:
    gh = merge.gh_runner(run_gh)
    args = ["pr", "list", "--head", head, "--state", "all", "--limit", "10", "--json", "number,state,headRefName"]
    listed = _parse_pr_list(gh(args, repo_dir))
    if isinstance(listed, AdmissionUnavailable):
        return listed
    matching = [item for item in listed if str(item.get("headRefName") or "") == head]
    if not matching:
        return False
    states = [str(item.get("state") or "OPEN").upper() for item in matching]
    if any(state == "OPEN" for state in states):
        return False
    return all(state in _CLOSED_PR_STATES for state in states)
