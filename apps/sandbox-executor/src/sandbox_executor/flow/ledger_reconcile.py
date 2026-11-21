"""Ledger reconciliation: resolve git conflicts in the three append-only JSONL ledgers.

A conflicted ledger is resolved by keeping one side as an untouched prefix and appending the other
side's new valid rows, under the ledger lock, so the append-only invariant holds.
"""

from __future__ import annotations

import logging
from typing import Any, TextIO

from sandbox_executor.ledger.lock import assert_ledger_prefix
from sandbox_executor.ledger.reconcile import (
    _CONFLICT_MARKERS,
    _DEFAULT_RECONCILER,
    _SECTION_AFTER_MARKER,
    LEDGER_REL_PATHS,
    ConflictResolutionError,
    LedgerReconciler,
    PrefixSide,
)

logger = logging.getLogger(__name__)

__all__ = [
    "LEDGER_REL_PATHS",
    "_CONFLICT_MARKERS",
    "_SECTION_AFTER_MARKER",
    "ConflictResolutionError",
    "LedgerReconciler",
    "PrefixSide",
    "_append_only_union",
    "_has_conflict_marker_lines",
    "_ledgers_with_conflict_markers",
    "_resolve_conflicting_ledger",
    "_resolve_ledger_under_lock",
    "_split_conflict_sides",
    "_write_resolved_ledger",
    "assert_ledger_prefix",
    "extract_valid_ledger_rows",
    "reconcile_jsonl_ledger_conflict",
    "reconcile_ledger_file",
    "resolve_append_only_conflict",
]


def _has_conflict_marker_lines(content: str) -> bool:
    """Return True when any line of content starts with a git conflict marker."""
    return _DEFAULT_RECONCILER._has_conflict_marker_lines(content)


def extract_valid_ledger_rows(content: str) -> list[dict[str, Any]]:
    """Extract valid JSON dictionary objects from ledger content, skipping git conflict markers."""
    return _DEFAULT_RECONCILER.extract_valid_ledger_rows(content)


def _split_conflict_sides(content: str) -> tuple[list[str], list[str]]:
    """Split conflicted content into the ours and theirs files, each keeping shared lines."""
    return _DEFAULT_RECONCILER._split_conflict_sides(content)


def _append_only_union(prefix_lines: list[str], other_lines: list[str]) -> str:
    """Return the prefix side unchanged, followed by the other side's missing lines ordered by ``created_at``."""
    return _DEFAULT_RECONCILER._append_only_union(prefix_lines, other_lines)


def resolve_append_only_conflict(content: str, prefix_side: PrefixSide = "ours") -> tuple[str, str]:
    """Resolve conflicted ledger content by appending, keeping one side's bytes as an untouched prefix."""
    return _DEFAULT_RECONCILER.resolve_append_only_conflict(content, prefix_side)


def reconcile_jsonl_ledger_conflict(content: str, prefix_side: PrefixSide = "ours") -> str:
    """Resolve JSONL ledger content containing conflict markers by pure append."""
    return _DEFAULT_RECONCILER.reconcile_jsonl_ledger_conflict(content, prefix_side)


def _write_resolved_ledger(locked: TextIO, file_path: str, prefix: str, resolved: str) -> None:
    """Rewrite a conflicted ledger in place through its held LedgerLock."""
    _DEFAULT_RECONCILER._write_resolved_ledger(locked, file_path, prefix, resolved)


def _resolve_ledger_under_lock(file_path: str, prefix_side: PrefixSide) -> None:
    """Under LedgerLock, read the ledger and resolve conflict markers by pure append."""
    _DEFAULT_RECONCILER._resolve_ledger_under_lock(file_path, prefix_side)


def reconcile_ledger_file(file_path: str, prefix_side: PrefixSide = "ours") -> bool:
    """Resolve git conflict markers in a ledger file by pure append."""
    return _DEFAULT_RECONCILER.reconcile_ledger_file(file_path, prefix_side)


def _ledgers_with_conflict_markers(repo_dir: str) -> list[str]:
    """Return the ledgers in working tree holding line-anchored git conflict markers."""
    return _DEFAULT_RECONCILER._ledgers_with_conflict_markers(repo_dir)


def _resolve_conflicting_ledger(repo_dir: str, rel_path: str) -> bool:
    """Resolve one conflicted working-tree ledger by pure append without staging it."""
    return _DEFAULT_RECONCILER._resolve_conflicting_ledger(repo_dir, rel_path)
