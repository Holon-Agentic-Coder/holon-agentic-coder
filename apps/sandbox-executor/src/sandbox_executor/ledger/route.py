from __future__ import annotations

import contextlib
import io
import json
import os
from collections.abc import Iterator
from typing import Any, TextIO

from sandbox_executor.ledger.fragments import (
    UNSCOPED_ROOT,
    fragment_path,
    ledger_name_of,
    pending_dir_for,
    root_intent_id,
)
from sandbox_executor.ledger.lock import LedgerLock, _read_ledger_bytes, assert_ledger_prefix
from sandbox_executor.ledger.order import to_utc_z, utc_now_z

_HEAD_REF_PREFIX = "ref: refs/heads/"
_INTENT_ROOT_PREFIX = "I-"


class RoutedLedgerBuffer(io.StringIO):
    def __init__(self, filepath: str, lock_options: dict[str, float]) -> None:
        super().__init__()
        self.filepath = filepath
        self.lock_options = lock_options


def _git_dir(repo_dir: str) -> str | None:
    dot_git = os.path.join(repo_dir, ".git")
    if os.path.isdir(dot_git):
        return dot_git
    try:
        with open(dot_git, encoding="utf-8") as f:
            pointer = f.read().strip()
    except OSError:
        return None
    if not pointer.startswith("gitdir:"):
        return None
    return os.path.join(repo_dir, pointer.removeprefix("gitdir:").strip())


def checked_out_root(canonical_path: str) -> str | None:
    repo_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(canonical_path))))
    git_dir = _git_dir(repo_dir)
    if git_dir is None:
        return None
    try:
        with open(os.path.join(git_dir, "HEAD"), encoding="utf-8") as f:
            head = f.read().strip()
    except OSError:
        return None
    if not head.startswith(_HEAD_REF_PREFIX):
        return None
    root = root_intent_id({"branch": head.removeprefix(_HEAD_REF_PREFIX)})
    return root if root.startswith(_INTENT_ROOT_PREFIX) else None


def _stamped_line(line: str, row: dict[str, Any]) -> str:
    created_at = row.get("created_at")
    if created_at is None:
        stamped = utc_now_z()
    elif isinstance(created_at, str) and created_at.endswith("Z"):
        return line
    else:
        stamped = to_utc_z(created_at)
    if stamped is None:
        return line
    return json.dumps({**row, "created_at": stamped})


def _route_line(canonical_path: str, line: str, fallback_root: str | None) -> tuple[str, str]:
    try:
        row = json.loads(line)
    except ValueError:
        row = None
    if not isinstance(row, dict):
        return os.path.join(pending_dir_for(canonical_path), f"{UNSCOPED_ROOT}.jsonl"), line
    target = fragment_path(canonical_path, row)
    if fallback_root and root_intent_id(row) == UNSCOPED_ROOT:
        target = os.path.join(pending_dir_for(canonical_path), f"{fallback_root}.jsonl")
    return target, _stamped_line(line, row)


def route_lines(canonical_path: str, text: str) -> dict[str, str]:
    fallback_root = checked_out_root(canonical_path)
    grouped: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        target, routed = _route_line(canonical_path, line, fallback_root)
        grouped.setdefault(target, []).append(routed + "\n")
    return {target: "".join(lines) for target, lines in grouped.items()}


def _append_locked(target: str, text: str, lock_options: dict[str, float]) -> None:
    with LedgerLock(target, **lock_options) as f:
        before = _read_ledger_bytes(target)
        f.write(text)
        f.flush()
        assert_ledger_prefix(target, before)


def flush_routed(buffer: RoutedLedgerBuffer) -> None:
    text = buffer.getvalue()
    if ledger_name_of(buffer.filepath) is None:
        _append_locked(buffer.filepath, text, buffer.lock_options)
        return
    for target, routed in route_lines(buffer.filepath, text).items():
        _append_locked(target, routed, buffer.lock_options)


@contextlib.contextmanager
def atomic_ledger_append(
    filepath: str,
    timeout: float = 10.0,
    initial_delay: float = 0.02,
    max_delay: float = 0.5,
) -> Iterator[TextIO]:
    buffer = RoutedLedgerBuffer(filepath, {"timeout": timeout, "initial_delay": initial_delay, "max_delay": max_delay})
    yield buffer
    flush_routed(buffer)
