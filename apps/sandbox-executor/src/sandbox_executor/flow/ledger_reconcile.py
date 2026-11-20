"""Ledger reconciliation: resolve git conflicts in the three append-only JSONL ledgers.

A conflicted ledger is resolved by keeping one side as an untouched prefix and appending the other
side's new valid rows, under the ledger lock, so the append-only invariant holds.
"""

from __future__ import annotations

from typing import Any

from sandbox_executor.ledger.lock import (
    LedgerLock,
    LedgerRewriteError,
    assert_ledger_prefix,
)
from sandbox_executor.ledger.reconcile import (
    _CONFLICT_MARKERS,
    _SECTION_AFTER_MARKER,
    LEDGER_REL_PATHS,
    ConflictResolutionError,
    LedgerReconciler,
    PrefixSide,
    _append_only_union,
    _has_conflict_marker_lines,
    _ledgers_with_conflict_markers,
    _resolve_conflicting_ledger,
    _resolve_ledger_under_lock,
    _split_conflict_sides,
    _write_resolved_ledger,
)


def extract_valid_ledger_rows(content: str) -> list[dict[str, Any]]:
    """Extract valid JSON dictionary objects from ledger content, skipping git conflict markers."""
    return LedgerReconciler.extract_valid_ledger_rows(content)


def resolve_append_only_conflict(content: str, prefix_side: PrefixSide = "ours") -> tuple[str, str]:
    """Resolve conflicted ledger content by appending, keeping one side's bytes as an untouched prefix."""
    return LedgerReconciler.resolve_append_only_conflict(content, prefix_side)


def reconcile_jsonl_ledger_conflict(content: str, prefix_side: PrefixSide = "ours") -> str:
    """Resolve JSONL ledger content containing standard or diff3 conflict markers by pure append."""
    return LedgerReconciler.reconcile_jsonl_ledger_conflict(content, prefix_side)


def reconcile_ledger_file(file_path: str, prefix_side: PrefixSide = "ours") -> bool:
    """Resolve git conflict markers in a ledger file by pure append; a file without markers is left untouched."""
    return LedgerReconciler.reconcile_ledger_file(file_path, prefix_side)


__all__ = [
    "LEDGER_REL_PATHS",
    "_CONFLICT_MARKERS",
    "_SECTION_AFTER_MARKER",
    "ConflictResolutionError",
    "LedgerLock",
    "LedgerReconciler",
    "LedgerRewriteError",
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
