"""Tests for sandbox_executor.ledger package decomposition and LedgerReconciler."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sandbox_executor.ledger as ledger
from sandbox_executor.ledger import (
    ActionRecord,
    ConflictResolutionError,
    LedgerLock,
    LedgerReconciler,
    LedgerRewriteError,
    SummaryRecord,
    append_action_record,
    append_summary_record,
    format_commit_trailers,
    format_query_results_table,
    parse_commit_trailers,
    query_ledger,
    reconcile_jsonl_ledger_conflict,
    reconcile_ledger_file,
    resolve_append_only_conflict,
    try_append_action_record,
    try_append_ledger_line,
    validate_ledger_file_unique_ids,
    validate_ledger_unique_ids,
)
from sandbox_executor.ledger.reconcile import reconcile_ledger_rows


def test_package_reexports_all_core_symbols() -> None:
    expected_symbols = [
        "ActionRecord",
        "SummaryRecord",
        "LedgerLock",
        "LedgerRewriteError",
        "ConflictResolutionError",
        "LedgerReconciler",
        "append_action_record",
        "append_summary_record",
        "query_ledger",
        "format_query_results_table",
        "validate_ledger_unique_ids",
        "validate_ledger_file_unique_ids",
        "parse_commit_trailers",
        "format_commit_trailers",
        "reconcile_ledger_file",
        "reconcile_jsonl_ledger_conflict",
        "resolve_append_only_conflict",
        "try_append_action_record",
        "try_append_ledger_line",
    ]
    for sym in expected_symbols:
        assert hasattr(ledger, sym), f"sandbox_executor.ledger is missing symbol '{sym}'"


def test_ledger_reconciler_pure_row_reconciliation() -> None:
    reconciler = LedgerReconciler()

    ours = [
        {"id": "1", "data": "row1"},
        {"id": "2", "data": "row2"},
    ]
    theirs = [
        {"id": "1", "data": "row1"},
        {"id": "3", "data": "row3"},
    ]

    # Reconciling with ours as prefix keeps ours first, then appends theirs
    result_ours = reconciler.reconcile_ledger_rows(ours, theirs, prefix_side="ours")
    assert result_ours == [
        {"id": "1", "data": "row1"},
        {"id": "2", "data": "row2"},
        {"id": "3", "data": "row3"},
    ]

    # Reconciling with theirs as prefix keeps theirs first, then appends ours
    result_theirs = reconciler.reconcile_ledger_rows(ours, theirs, prefix_side="theirs")
    assert result_theirs == [
        {"id": "1", "data": "row1"},
        {"id": "3", "data": "row3"},
        {"id": "2", "data": "row2"},
    ]


def test_ledger_reconciler_invalid_prefix_side() -> None:
    reconciler = LedgerReconciler()
    with pytest.raises(ValueError, match="prefix_side must be 'ours' or 'theirs'"):
        reconciler.reconcile_ledger_rows([], [], prefix_side="invalid")  # type: ignore[arg-type]


def test_reconcile_ledger_file_preserves_content_and_atomicity(tmp_path: Path) -> None:
    ledger_file = tmp_path / "test.jsonl"
    conflict_content = (
        '{"id": "base"}\n'
        "<<<<<<< HEAD\n"
        '{"id": "ours_new"}\n'
        "=======\n"
        '{"id": "theirs_new"}\n'
        ">>>>>>> upstream/main\n"
    )
    ledger_file.write_text(conflict_content, encoding="utf-8")

    success = reconcile_ledger_file(str(ledger_file), prefix_side="ours")
    assert success is True

    resolved_lines = ledger_file.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in resolved_lines]
    assert rows == [
        {"id": "base"},
        {"id": "ours_new"},
        {"id": "theirs_new"},
    ]


def test_reconcile_nonexistent_file() -> None:
    assert reconcile_ledger_file("/path/to/nonexistent/file.jsonl") is False
