from __future__ import annotations

import contextlib
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

try:
    from scripts.module_budget_freeze import run_git
except ImportError:
    from module_budget_freeze import run_git

HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
NEW_FILE_HEADER_PREFIX = "+++ b/"
DIFF_OPTIONS = ("-c", "core.quotePath=false", "diff", "-U0", "--no-color", "--no-ext-diff")
CHANGED_FILE_FILTER = ("--diff-filter=ACMR", "--ignore-submodules=all")
TARGET_REF = "origin/main"
MAIN_FETCH_REFSPEC = "+refs/heads/main:refs/remotes/origin/main"
FETCH_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class ChangedFile:
    path: str
    added_lines: frozenset[int]
    text: str


def git_output_bytes(root: Path, *args: str) -> bytes | None:
    try:
        completed = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    except OSError:
        return None
    return completed.stdout if completed.returncode == 0 else None


def existing_merge_base(root: Path) -> str | None:
    completed = run_git(root, "merge-base", "HEAD", TARGET_REF)
    if completed is None or completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def has_origin_remote(root: Path) -> bool:
    completed = run_git(root, "remote", "get-url", "origin")
    return completed is not None and completed.returncode == 0


def is_shallow_repository(root: Path) -> bool:
    completed = run_git(root, "rev-parse", "--is-shallow-repository")
    return completed is not None and completed.stdout.strip() == "true"


def fetch_from_origin(root: Path, *args: str) -> None:
    # why: a credential prompt would hang CI and the containers
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(
            ["git", "fetch", "--quiet", "origin", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=FETCH_TIMEOUT_SECONDS,
            env=environment,
        )


def deepen_towards_origin_main(root: Path) -> None:
    fetch_from_origin(root, MAIN_FETCH_REFSPEC)
    if is_shallow_repository(root):
        fetch_from_origin(root, "--unshallow", MAIN_FETCH_REFSPEC)


def merge_base(root: Path) -> str | None:
    base = existing_merge_base(root)
    if base is not None:
        return base
    if not has_origin_remote(root):
        return None
    deepen_towards_origin_main(root)
    return existing_merge_base(root)


def hunk_added_lines(hunk_header: str) -> range:
    match = HUNK_HEADER.match(hunk_header)
    if match is None:
        return range(0)
    start = int(match.group(1))
    count = 1 if match.group(2) is None else int(match.group(2))
    return range(start, start + count)


def added_line_numbers(diff_text: str) -> dict[str, frozenset[int]]:
    added: dict[str, set[int]] = {}
    current_path: str | None = None
    in_file_header = False
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current_path, in_file_header = None, True
        elif in_file_header and line.startswith(NEW_FILE_HEADER_PREFIX):
            current_path = line.removeprefix(NEW_FILE_HEADER_PREFIX)
            added.setdefault(current_path, set())
        elif line.startswith("@@"):
            in_file_header = False
            if current_path is not None:
                added[current_path].update(hunk_added_lines(line))
    return {path: frozenset(lines) for path, lines in added.items()}


def decode_text(content: bytes | None) -> str | None:
    if content is None:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def read_worktree_text(root: Path, path: str) -> str | None:
    file_path = root / path
    if not file_path.is_file():
        return None
    try:
        return decode_text(file_path.read_bytes())
    except OSError:
        return None


def read_index_text(root: Path, path: str) -> str | None:
    return decode_text(git_output_bytes(root, "show", f":{path}"))


def every_line_number(text: str) -> frozenset[int]:
    return frozenset(range(1, len(text.splitlines()) + 1))


def untracked_changed_files(root: Path) -> list[ChangedFile]:
    completed = run_git(root, "-c", "core.quotePath=false", "ls-files", "--others", "--exclude-standard")
    if completed is None or completed.returncode != 0:
        return []
    changed = []
    for path in completed.stdout.splitlines():
        text = read_worktree_text(root, path)
        if text is not None:
            changed.append(ChangedFile(path, every_line_number(text), text))
    return changed


def changed_files_from_diff(diff_output: bytes | None, read_text: Callable[[str], str | None]) -> list[ChangedFile]:
    if diff_output is None:
        return []
    changed = []
    for path, lines in added_line_numbers(diff_output.decode("utf-8", errors="replace")).items():
        text = read_text(path)
        if lines and text is not None:
            changed.append(ChangedFile(path, lines, text))
    return changed


def changed_files_against_merge_base(root: Path, base: str) -> list[ChangedFile]:
    diff_output = git_output_bytes(root, *DIFF_OPTIONS, *CHANGED_FILE_FILTER, base)
    tracked = changed_files_from_diff(diff_output, lambda path: read_worktree_text(root, path))
    return tracked + untracked_changed_files(root)


def staged_changed_files(root: Path) -> list[ChangedFile]:
    diff_output = git_output_bytes(root, *DIFF_OPTIONS, "--cached", *CHANGED_FILE_FILTER)
    return changed_files_from_diff(diff_output, lambda path: read_index_text(root, path))
