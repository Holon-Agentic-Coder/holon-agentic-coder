"""Ledger reconciliation: resolve git conflicts in the three append-only JSONL ledgers."""

from __future__ import annotations

import contextlib
import json
import logging
import os
from typing import Any, Literal, TextIO

from sandbox_executor.ledger.lock import LedgerLock, LedgerRewriteError, assert_ledger_prefix

logger = logging.getLogger(__name__)


class ConflictResolutionError(RuntimeError):
    """Raised when an unresolvable merge conflict is encountered during a pipeline stage."""


_CONFLICT_MARKERS = ("<<<<<<<", "|||||||", "=======", ">>>>>>>")
_SECTION_AFTER_MARKER = {"<<<<<<<": "ours", "|||||||": "base", "=======": "theirs", ">>>>>>>": "both"}

PrefixSide = Literal["ours", "theirs"]

LEDGER_REL_PATHS = (
    os.path.join("holon-knowledge", "ledger", "intents.jsonl"),
    os.path.join("holon-knowledge", "ledger", "plans.jsonl"),
    os.path.join("holon-knowledge", "ledger", "executions.jsonl"),
)


class LedgerReconciler:
    """Encapsulates pure row operations and file locking shell for ledger reconciliation."""

    @staticmethod
    def has_conflict_marker_lines(content: str) -> bool:
        """Return True when any line of ``content`` starts with a git conflict marker."""
        return any(line.startswith(_CONFLICT_MARKERS) for line in content.splitlines())

    @staticmethod
    def extract_valid_ledger_rows(content: str) -> list[dict[str, Any]]:
        """Extract valid JSON dictionary objects from ledger content, skipping git conflict markers."""
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
                    logger.warning(f"Skipping non-dictionary JSON ledger line: {stripped[:100]}")
            except Exception:
                logger.debug(f"Skipping unparseable ledger line: {stripped[:100]}")
        return rows

    @staticmethod
    def split_conflict_sides(content: str) -> tuple[list[str], list[str]]:
        """Split conflicted content into the ours and theirs files, each keeping the shared lines in place."""
        ours: list[str] = []
        theirs: list[str] = []
        section = "both"
        for line in content.splitlines(keepends=True):
            marker = line[:7]
            if marker in _SECTION_AFTER_MARKER:
                section = _SECTION_AFTER_MARKER[marker]
                continue
            if section in ("both", "ours"):
                ours.append(line)
            if section in ("both", "theirs"):
                theirs.append(line)
        return ours, theirs

    @staticmethod
    def append_only_union(prefix_lines: list[str], other_lines: list[str]) -> str:
        """Return the prefix side unchanged, followed by the other side's lines it lacks, in their original order."""
        prefix = "".join(prefix_lines)
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        seen = {line.rstrip("\r\n") for line in prefix_lines}
        missing = (line.rstrip("\r\n") for line in other_lines if line.strip())
        extra = [line + "\n" for line in dict.fromkeys(missing) if line not in seen]
        return prefix + "".join(extra)

    @classmethod
    def resolve_append_only_conflict(cls, content: str, prefix_side: PrefixSide = "ours") -> tuple[str, str]:
        """Resolve conflicted ledger content by appending, keeping one side's bytes as an untouched prefix."""
        if prefix_side not in ("ours", "theirs"):
            raise ValueError(f"prefix_side must be 'ours' or 'theirs', not {prefix_side!r}")
        ours, theirs = cls.split_conflict_sides(content)
        prefix_lines, other_lines = (theirs, ours) if prefix_side == "theirs" else (ours, theirs)
        return "".join(prefix_lines), cls.append_only_union(prefix_lines, other_lines)

    @classmethod
    def reconcile_jsonl_ledger_conflict(cls, content: str, prefix_side: PrefixSide = "ours") -> str:
        """Resolve JSONL ledger content containing standard or diff3 conflict markers by pure append."""
        if not cls.has_conflict_marker_lines(content):
            return content
        return cls.resolve_append_only_conflict(content, prefix_side)[1]

    @classmethod
    def write_resolved_ledger(cls, locked: TextIO, file_path: str, prefix: str, resolved: str) -> None:
        """Rewrite a conflicted ledger in place through its held ``LedgerLock``, then check prefix survived."""
        if not resolved.startswith(prefix):
            raise LedgerRewriteError(f"Resolution of {file_path} does not keep the prefix side byte for byte")
        locked.flush()
        locked.buffer.truncate(0)
        locked.buffer.write(resolved.encode("utf-8"))
        locked.flush()
        import sys

        flow_rec = sys.modules.get("sandbox_executor.flow.ledger_reconcile")
        checker = getattr(flow_rec, "assert_ledger_prefix", None)
        if checker is None:
            ledger_mod = sys.modules.get("sandbox_executor.ledger")
            checker = getattr(ledger_mod, "assert_ledger_prefix", assert_ledger_prefix)
        checker(file_path, prefix.encode("utf-8"))

    @classmethod
    def resolve_ledger_under_lock(cls, file_path: str, prefix_side: PrefixSide) -> None:
        """Under ``LedgerLock``, read the ledger and resolve any line-anchored conflict markers by pure append."""
        with LedgerLock(file_path) as locked:
            with open(file_path, encoding="utf-8", newline="") as f:
                content = f.read()
            if cls.has_conflict_marker_lines(content):
                cls.write_resolved_ledger(locked, file_path, *cls.resolve_append_only_conflict(content, prefix_side))

    @classmethod
    def reconcile_ledger_file(cls, file_path: str, prefix_side: PrefixSide = "ours") -> bool:
        """Resolve git conflict markers in a ledger file by pure append; a file without markers is untouched."""
        if not os.path.exists(file_path):
            logger.error(f"Failed to reconcile ledger file '{file_path}': it does not exist")
            return False
        try:
            cls.resolve_ledger_under_lock(file_path, prefix_side)
            return True
        except LedgerRewriteError:
            raise
        except Exception as exc:
            logger.error(f"Failed to reconcile ledger file '{file_path}': {exc}")
            return False

    @classmethod
    def ledgers_with_conflict_markers(cls, repo_dir: str) -> list[str]:
        """Return the ledgers in the working tree that hold line-anchored git conflict markers."""
        found: list[str] = []
        for rel_path in LEDGER_REL_PATHS:
            full_path = os.path.join(repo_dir, rel_path)
            with contextlib.suppress(OSError, UnicodeDecodeError), open(full_path, encoding="utf-8") as f:
                if cls.has_conflict_marker_lines(f.read()):
                    found.append(rel_path)
        return found

    @classmethod
    def resolve_conflicting_ledger(cls, repo_dir: str, rel_path: str) -> bool:
        """Resolve one conflicted working-tree ledger by pure append, without staging it."""
        full_path = os.path.join(repo_dir, rel_path)
        return os.path.exists(full_path) and cls.reconcile_ledger_file(full_path)


# Free function re-exports
extract_valid_ledger_rows = LedgerReconciler.extract_valid_ledger_rows
resolve_append_only_conflict = LedgerReconciler.resolve_append_only_conflict
reconcile_jsonl_ledger_conflict = LedgerReconciler.reconcile_jsonl_ledger_conflict
reconcile_ledger_file = LedgerReconciler.reconcile_ledger_file
_has_conflict_marker_lines = LedgerReconciler.has_conflict_marker_lines
_split_conflict_sides = LedgerReconciler.split_conflict_sides
_append_only_union = LedgerReconciler.append_only_union
_write_resolved_ledger = LedgerReconciler.write_resolved_ledger
_resolve_ledger_under_lock = LedgerReconciler.resolve_ledger_under_lock
_ledgers_with_conflict_markers = LedgerReconciler.ledgers_with_conflict_markers
_resolve_conflicting_ledger = LedgerReconciler.resolve_conflicting_ledger
