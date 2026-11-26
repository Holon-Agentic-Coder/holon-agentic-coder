from __future__ import annotations

import contextlib
import json
import logging
import os
from bisect import bisect_right
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Literal, TextIO

from sandbox_executor.ledger.lock import LedgerLock, LedgerRewriteError
from sandbox_executor.ledger.order import _order_appended_rows, parse_created_at

logger = logging.getLogger(__name__)


class ConflictResolutionError(RuntimeError):
    pass


_CONFLICT_MARKERS = ("<<<<<<<", "|||||||", "=======", ">>>>>>>")
_SECTION_AFTER_MARKER = {"<<<<<<<": "ours", "|||||||": "base", "=======": "theirs", ">>>>>>>": "both"}
_VALID_PREFIX_SIDES = ("ours", "theirs")
_CONFLICT_MARKER_LEN = 7
_LOG_PREVIEW_LIMIT = 100

PrefixSide = Literal["ours", "theirs"]
_EARLIEST = datetime.min.replace(tzinfo=UTC)

LEDGER_REL_PATHS = (
    os.path.join("holon-knowledge", "ledger", "intents.jsonl"),
    os.path.join("holon-knowledge", "ledger", "plans.jsonl"),
    os.path.join("holon-knowledge", "ledger", "executions.jsonl"),
)


def _line_instant(line: str) -> datetime | None:
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return parse_created_at(row.get("created_at")) if isinstance(row, dict) else None


def _running_max_instants(lines: list[str]) -> list[datetime]:
    running: list[datetime] = []
    high = _EARLIEST
    for line in lines:
        high = max(high, _line_instant(line) or high)
        running.append(high)
    return running


def _slot_incoming_lines(
    kept_lines: list[str], incoming_lines: list[str]
) -> tuple[defaultdict[int, list[tuple[datetime, int, str]]], list[str]]:
    running_max = _running_max_instants(kept_lines)
    seen = set(kept_lines)
    slots: defaultdict[int, list[tuple[datetime, int, str]]] = defaultdict(list)
    untimed: list[str] = []
    previous: datetime | None = None
    for index, line in enumerate(incoming_lines):
        previous = _line_instant(line) or previous
        if line in seen:
            continue
        seen.add(line)
        if previous is None:
            untimed.append(line)
        else:
            slots[bisect_right(running_max, previous)].append((previous, index, line))
    return slots, untimed


def insert_incoming_lines(kept_lines: list[str], incoming_lines: list[str]) -> list[str]:
    slots, untimed = _slot_incoming_lines(kept_lines, incoming_lines)
    merged: list[str] = []
    for position in range(len(kept_lines) + 1):
        merged.extend(line for _instant, _index, line in sorted(slots.get(position, ())))
        if position < len(kept_lines):
            merged.append(kept_lines[position])
    return merged + untimed


def _keeps_lines_in_order(kept_lines: list[str], resolved_lines: list[str]) -> bool:
    remaining = iter(resolved_lines)
    return all(line in remaining for line in kept_lines if line.strip())


def assert_resolution_keeps_rows(path: str, kept: str, resolved: str) -> None:
    with open(path, encoding="utf-8", newline="") as f:
        written = f.read()
    if written != resolved or not _keeps_lines_in_order(kept.splitlines(), written.splitlines()):
        raise LedgerRewriteError(f"Ledger {path} was rewritten: a kept row was dropped or moved")


class LedgerReconciler:
    @staticmethod
    def extract_valid_ledger_rows(content: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(_CONFLICT_MARKERS):
                continue
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    rows.append(parsed)
                else:
                    logger.warning("Skipping non-dictionary JSON ledger line: %s", stripped[:_LOG_PREVIEW_LIMIT])
            except Exception:
                logger.debug("Skipping unparseable ledger line: %s", stripped[:_LOG_PREVIEW_LIMIT])
        return rows

    @staticmethod
    def _has_conflict_marker_lines(content: str) -> bool:
        return any(line.startswith(_CONFLICT_MARKERS) for line in content.splitlines())

    @staticmethod
    def _split_conflict_sides(content: str) -> tuple[list[str], list[str]]:
        ours: list[str] = []
        theirs: list[str] = []
        section = "both"
        for line in content.splitlines(keepends=True):
            marker = line[:_CONFLICT_MARKER_LEN]
            if marker in _SECTION_AFTER_MARKER:
                section = _SECTION_AFTER_MARKER[marker]
                continue
            if section in ("both", "ours"):
                ours.append(line)
            if section in ("both", "theirs"):
                theirs.append(line)
        return ours, theirs

    @staticmethod
    def _append_only_union(prefix_lines: list[str], other_lines: list[str]) -> str:
        prefix = "".join(prefix_lines)
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        seen = {line.rstrip("\r\n") for line in prefix_lines}
        missing = (line.rstrip("\r\n") for line in other_lines if line.strip())
        extra = _order_appended_rows([line + "\n" for line in dict.fromkeys(missing) if line not in seen])
        return prefix + "".join(extra)

    @staticmethod
    def _insertion_union(prefix_lines: list[str], other_lines: list[str]) -> str:
        kept = [line.rstrip("\r\n") for line in prefix_lines]
        incoming = [line.rstrip("\r\n") for line in other_lines if line.strip()]
        return "".join(f"{line}\n" for line in insert_incoming_lines(kept, incoming))

    def resolve_append_only_conflict(self, content: str, prefix_side: PrefixSide = "ours") -> tuple[str, str]:
        if prefix_side not in _VALID_PREFIX_SIDES:
            raise ValueError(f"prefix_side must be 'ours' or 'theirs', not {prefix_side!r}")
        ours, theirs = self._split_conflict_sides(content)
        prefix_lines, other_lines = (theirs, ours) if prefix_side == "theirs" else (ours, theirs)
        return "".join(prefix_lines), self._insertion_union(prefix_lines, other_lines)

    def reconcile_jsonl_ledger_conflict(self, content: str, prefix_side: PrefixSide = "ours") -> str:
        if not self._has_conflict_marker_lines(content):
            return content
        return self.resolve_append_only_conflict(content, prefix_side)[1]

    def reconcile_ledger_content(self, content: str, prefix_side: PrefixSide = "ours") -> str:
        return self.reconcile_jsonl_ledger_conflict(content, prefix_side)

    def _write_resolved_ledger(self, locked: TextIO, file_path: str, prefix: str, resolved: str) -> None:
        if not _keeps_lines_in_order(prefix.splitlines(), resolved.splitlines()):
            raise LedgerRewriteError(f"Resolution of {file_path} drops or moves a row of the kept side")
        locked.flush()
        locked.buffer.truncate(0)
        locked.buffer.write(resolved.encode("utf-8"))
        locked.flush()
        assert_resolution_keeps_rows(file_path, prefix, resolved)

    def _resolve_ledger_under_lock(self, file_path: str, prefix_side: PrefixSide) -> None:
        with LedgerLock(file_path) as locked:
            with open(file_path, encoding="utf-8", newline="") as f:
                content = f.read()
            if self._has_conflict_marker_lines(content):
                prefix, resolved = self.resolve_append_only_conflict(content, prefix_side)
                self._write_resolved_ledger(locked, file_path, prefix, resolved)

    def reconcile_ledger_file(self, file_path: str, prefix_side: PrefixSide = "ours") -> bool:
        if not os.path.exists(file_path):
            logger.error("Failed to reconcile ledger file '%s': it does not exist", file_path)
            return False
        try:
            self._resolve_ledger_under_lock(file_path, prefix_side)
            return True
        except LedgerRewriteError:
            raise
        except Exception as exc:
            logger.error("Failed to reconcile ledger file '%s': %s", file_path, exc)
            return False

    def reconcile_ledgers(self, repo_dir: str, prefix_side: PrefixSide = "ours") -> list[str]:
        resolved: list[str] = []
        for rel_path in LEDGER_REL_PATHS:
            full_path = os.path.join(repo_dir, rel_path)
            try:
                with open(full_path, encoding="utf-8") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            if self._has_conflict_marker_lines(content) and self.reconcile_ledger_file(full_path, prefix_side):
                resolved.append(rel_path)
        return resolved

    def _ledgers_with_conflict_markers(self, repo_dir: str) -> list[str]:
        found: list[str] = []
        for rel_path in LEDGER_REL_PATHS:
            full_path = os.path.join(repo_dir, rel_path)
            with contextlib.suppress(OSError, UnicodeDecodeError), open(full_path, encoding="utf-8") as f:
                if self._has_conflict_marker_lines(f.read()):
                    found.append(rel_path)
        return found

    def _resolve_conflicting_ledger(self, repo_dir: str, rel_path: str) -> bool:
        full_path = os.path.join(repo_dir, rel_path)
        return os.path.exists(full_path) and self.reconcile_ledger_file(full_path)


_DEFAULT_RECONCILER = LedgerReconciler()

extract_valid_ledger_rows = _DEFAULT_RECONCILER.extract_valid_ledger_rows
_has_conflict_marker_lines = _DEFAULT_RECONCILER._has_conflict_marker_lines
_split_conflict_sides = _DEFAULT_RECONCILER._split_conflict_sides
_append_only_union = _DEFAULT_RECONCILER._append_only_union
_insertion_union = _DEFAULT_RECONCILER._insertion_union
resolve_append_only_conflict = _DEFAULT_RECONCILER.resolve_append_only_conflict
reconcile_jsonl_ledger_conflict = _DEFAULT_RECONCILER.reconcile_jsonl_ledger_conflict
reconcile_ledger_content = _DEFAULT_RECONCILER.reconcile_ledger_content
reconcile_ledger_file = _DEFAULT_RECONCILER.reconcile_ledger_file
reconcile_ledgers = _DEFAULT_RECONCILER.reconcile_ledgers
_write_resolved_ledger = _DEFAULT_RECONCILER._write_resolved_ledger
_resolve_ledger_under_lock = _DEFAULT_RECONCILER._resolve_ledger_under_lock
_ledgers_with_conflict_markers = _DEFAULT_RECONCILER._ledgers_with_conflict_markers
_resolve_conflicting_ledger = _DEFAULT_RECONCILER._resolve_conflicting_ledger
