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
    insert_incoming_lines,
)

logger = logging.getLogger(__name__)

__all__ = [
    "LEDGER_REL_PATHS",
    "_CONFLICT_MARKERS",
    "_SECTION_AFTER_MARKER",
    "ConflictResolutionError",
    "LedgerReconciler",
    "PrefixSide",
    "_has_conflict_marker_lines",
    "_ledgers_with_conflict_markers",
    "_resolve_conflicting_ledger",
    "_resolve_ledger_under_lock",
    "_split_conflict_sides",
    "_write_resolved_ledger",
    "assert_ledger_prefix",
    "extract_valid_ledger_rows",
    "insert_incoming_lines",
    "reconcile_jsonl_ledger_conflict",
    "reconcile_ledger_file",
    "resolve_append_only_conflict",
]


def _has_conflict_marker_lines(content: str) -> bool:
    return _DEFAULT_RECONCILER._has_conflict_marker_lines(content)


def extract_valid_ledger_rows(content: str) -> list[dict[str, Any]]:
    return _DEFAULT_RECONCILER.extract_valid_ledger_rows(content)


def _split_conflict_sides(content: str) -> tuple[list[str], list[str]]:
    return _DEFAULT_RECONCILER._split_conflict_sides(content)


def resolve_append_only_conflict(content: str, prefix_side: PrefixSide = "ours") -> tuple[str, str]:
    return _DEFAULT_RECONCILER.resolve_append_only_conflict(content, prefix_side)


def reconcile_jsonl_ledger_conflict(content: str, prefix_side: PrefixSide = "ours") -> str:
    return _DEFAULT_RECONCILER.reconcile_jsonl_ledger_conflict(content, prefix_side)


def _write_resolved_ledger(locked: TextIO, file_path: str, prefix: str, resolved: str) -> None:
    _DEFAULT_RECONCILER._write_resolved_ledger(locked, file_path, prefix, resolved)


def _resolve_ledger_under_lock(file_path: str, prefix_side: PrefixSide) -> None:
    _DEFAULT_RECONCILER._resolve_ledger_under_lock(file_path, prefix_side)


def reconcile_ledger_file(file_path: str, prefix_side: PrefixSide = "ours") -> bool:
    return _DEFAULT_RECONCILER.reconcile_ledger_file(file_path, prefix_side)


def _ledgers_with_conflict_markers(repo_dir: str) -> list[str]:
    return _DEFAULT_RECONCILER._ledgers_with_conflict_markers(repo_dir)


def _resolve_conflicting_ledger(repo_dir: str, rel_path: str) -> bool:
    return _DEFAULT_RECONCILER._resolve_conflicting_ledger(repo_dir, rel_path)
