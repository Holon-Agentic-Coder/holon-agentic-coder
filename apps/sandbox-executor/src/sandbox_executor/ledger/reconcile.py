"""Ledger reconciliation: pure row operations and file operations preserving append-only invariants."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from typing import Any, Literal, TextIO

from sandbox_executor.ledger.lock import LedgerLock, LedgerRewriteError, assert_ledger_prefix

logger = logging.getLogger(__name__)


def _get_ledger_module() -> Any:
    import sandbox_executor.ledger as _l

    return _l


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
    """Encapsulates pure row operations and thin file operations for append-only ledger reconciliation."""

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
                    logger.warning("Skipping non-dictionary JSON ledger line: %s", stripped[:100])
            except Exception:
                logger.debug("Skipping unparseable ledger line: %s", stripped[:100])
        return rows

    @staticmethod
    def _parse_ledger_timestamp(row: dict[str, Any]) -> str:
        """Extract timestamp string from a ledger row dictionary."""
        return str(row.get("created_at") or row.get("timestamp") or "")

    @classmethod
    def reconcile_ledger_rows(
        cls,
        rows_ours: list[dict[str, Any]],
        rows_theirs: list[dict[str, Any]],
        prefix_side: PrefixSide = "ours",
    ) -> list[dict[str, Any]]:
        """Pure append reconciliation over rows: keeps prefix_side intact and appends missing rows."""
        if prefix_side not in ("ours", "theirs"):
            raise ValueError(f"prefix_side must be 'ours' or 'theirs', not {prefix_side!r}")
        prefix_rows, other_rows = (rows_theirs, rows_ours) if prefix_side == "theirs" else (rows_ours, rows_theirs)

        seen = {json.dumps(r, sort_keys=True) for r in prefix_rows}
        result = list(prefix_rows)
        for r in other_rows:
            key = json.dumps(r, sort_keys=True)
            if key not in seen:
                seen.add(key)
                result.append(r)
        return result

    @staticmethod
    def _has_conflict_marker_lines(content: str) -> bool:
        """Return True when any line of content starts with a git conflict marker."""
        return any(line.startswith(_CONFLICT_MARKERS) for line in content.splitlines())

    @staticmethod
    def _split_conflict_sides(content: str) -> tuple[list[str], list[str]]:
        """Split conflicted content into the ours and theirs files, each keeping shared lines."""
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
    def _append_only_union(prefix_lines: list[str], other_lines: list[str]) -> str:
        """Return the prefix side unchanged, followed by the other side's lines it lacks, ordered by ``created_at``.

        Lines are compared as exact text (ignoring the line ending), so an identical line is not repeated; the prefix
        never moves, and appended rows are never deduplicated by key or re-serialised (see ``_order_appended_rows``).
        """
        from sandbox_executor.flow.ledger_order import _order_appended_rows

        prefix = "".join(prefix_lines)
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        seen = {line.rstrip("\r\n") for line in prefix_lines}
        missing = (line.rstrip("\r\n") for line in other_lines if line.strip())
        extra = _order_appended_rows([line + "\n" for line in dict.fromkeys(missing) if line not in seen])
        return prefix + "".join(extra)

    def resolve_append_only_conflict(self, content: str, prefix_side: PrefixSide = "ours") -> tuple[str, str]:
        """Resolve conflicted ledger content by appending, keeping one side's bytes as an untouched prefix."""
        if prefix_side not in ("ours", "theirs"):
            raise ValueError(f"prefix_side must be 'ours' or 'theirs', not {prefix_side!r}")
        ours, theirs = self._split_conflict_sides(content)
        prefix_lines, other_lines = (theirs, ours) if prefix_side == "theirs" else (ours, theirs)
        return "".join(prefix_lines), self._append_only_union(prefix_lines, other_lines)

    def reconcile_jsonl_ledger_conflict(self, content: str, prefix_side: PrefixSide = "ours") -> str:
        """Resolve JSONL ledger content containing conflict markers by pure append."""
        if not self._has_conflict_marker_lines(content):
            return content
        return self.resolve_append_only_conflict(content, prefix_side)[1]

    def reconcile_ledger_content(self, content: str, prefix_side: PrefixSide = "ours") -> str:
        """Alias for reconcile_jsonl_ledger_conflict."""
        return self.reconcile_jsonl_ledger_conflict(content, prefix_side)

    def _write_resolved_ledger(self, locked: TextIO, file_path: str, prefix: str, resolved: str) -> None:
        """Rewrite a conflicted ledger in place through its held LedgerLock."""
        if not resolved.startswith(prefix):
            raise LedgerRewriteError(f"Resolution of {file_path} does not keep the prefix side byte for byte")
        locked.flush()
        locked.buffer.truncate(0)
        locked.buffer.write(resolved.encode("utf-8"))
        locked.flush()
        flow_rec = sys.modules.get("sandbox_executor.flow.ledger_reconcile")
        if flow_rec is not None and hasattr(flow_rec, "assert_ledger_prefix"):
            assert_fn = flow_rec.assert_ledger_prefix
        else:
            _l = _get_ledger_module()
            assert_fn = getattr(_l, "assert_ledger_prefix", assert_ledger_prefix)
        assert_fn(file_path, prefix.encode("utf-8"))

    def _resolve_ledger_under_lock(self, file_path: str, prefix_side: PrefixSide) -> None:
        """Under LedgerLock, read the ledger and resolve conflict markers by pure append."""
        with LedgerLock(file_path) as locked:
            with open(file_path, encoding="utf-8", newline="") as f:
                content = f.read()
            if self._has_conflict_marker_lines(content):
                prefix, resolved = self.resolve_append_only_conflict(content, prefix_side)
                self._write_resolved_ledger(locked, file_path, prefix, resolved)

    def reconcile_ledger_file(self, file_path: str, prefix_side: PrefixSide = "ours") -> bool:
        """Resolve git conflict markers in a ledger file by pure append."""
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
        """Reconcile all canonical ledgers in repository if conflicted."""
        resolved: list[str] = []
        for rel_path in LEDGER_REL_PATHS:
            full_path = os.path.join(repo_dir, rel_path)
            if os.path.exists(full_path):
                with contextlib.suppress(OSError, UnicodeDecodeError), open(full_path, encoding="utf-8") as f:
                    content = f.read()
                if self._has_conflict_marker_lines(content) and self.reconcile_ledger_file(full_path, prefix_side):
                    resolved.append(rel_path)
        return resolved

    def _ledgers_with_conflict_markers(self, repo_dir: str) -> list[str]:
        """Return the ledgers in working tree holding line-anchored git conflict markers."""
        found: list[str] = []
        for rel_path in LEDGER_REL_PATHS:
            full_path = os.path.join(repo_dir, rel_path)
            with contextlib.suppress(OSError, UnicodeDecodeError), open(full_path, encoding="utf-8") as f:
                if self._has_conflict_marker_lines(f.read()):
                    found.append(rel_path)
        return found

    def _resolve_conflicting_ledger(self, repo_dir: str, rel_path: str) -> bool:
        """Resolve one conflicted working-tree ledger by pure append without staging it."""
        full_path = os.path.join(repo_dir, rel_path)
        return os.path.exists(full_path) and self.reconcile_ledger_file(full_path)


_DEFAULT_RECONCILER = LedgerReconciler()

extract_valid_ledger_rows = _DEFAULT_RECONCILER.extract_valid_ledger_rows
reconcile_ledger_rows = _DEFAULT_RECONCILER.reconcile_ledger_rows
_parse_ledger_timestamp = _DEFAULT_RECONCILER._parse_ledger_timestamp
_has_conflict_marker_lines = _DEFAULT_RECONCILER._has_conflict_marker_lines
_split_conflict_sides = _DEFAULT_RECONCILER._split_conflict_sides
_append_only_union = _DEFAULT_RECONCILER._append_only_union
resolve_append_only_conflict = _DEFAULT_RECONCILER.resolve_append_only_conflict
reconcile_jsonl_ledger_conflict = _DEFAULT_RECONCILER.reconcile_jsonl_ledger_conflict
reconcile_ledger_content = _DEFAULT_RECONCILER.reconcile_ledger_content
reconcile_ledger_file = _DEFAULT_RECONCILER.reconcile_ledger_file
reconcile_ledgers = _DEFAULT_RECONCILER.reconcile_ledgers
_write_resolved_ledger = _DEFAULT_RECONCILER._write_resolved_ledger
_resolve_ledger_under_lock = _DEFAULT_RECONCILER._resolve_ledger_under_lock
_ledgers_with_conflict_markers = _DEFAULT_RECONCILER._ledgers_with_conflict_markers
_resolve_conflicting_ledger = _DEFAULT_RECONCILER._resolve_conflicting_ledger
