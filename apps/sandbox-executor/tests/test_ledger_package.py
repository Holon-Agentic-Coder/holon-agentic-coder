"""Tests for sandbox_executor.ledger package decomposition and LedgerReconciler."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import sandbox_executor.ledger as ledger
from sandbox_executor.ledger import (
    LedgerReconciler,
    reconcile_ledger_file,
)


def test_package_reexports_all_core_symbols() -> None:
    expected_symbols = [
        "ActionRecord",
        "SummaryRecord",
        "LedgerLock",
        "LedgerRewriteError",
        "ConflictResolutionError",
        "LedgerReconciler",
        "_describe_id_owner",
        "_extract_parent_ref",
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


def test_reconcile_ledger_file_preserves_content_and_atomicity(tmp_path: Path) -> None:
    ledger_file = tmp_path / "test.jsonl"
    conflict_content = (
        '{"id": "base"}\n<<<<<<< HEAD\n{"id": "ours_new"}\n=======\n{"id": "theirs_new"}\n>>>>>>> upstream/main\n'
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


def test_reconcile_ledgers_skips_an_unreadable_ledger_and_resolves_the_rest(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "holon-knowledge" / "ledger"
    ledger_dir.mkdir(parents=True)
    (ledger_dir / "intents.jsonl").write_bytes(b"\xff\xfe not utf-8\n<<<<<<< HEAD\n")
    (ledger_dir / "executions.jsonl").write_text(
        '{"id": "base"}\n<<<<<<< HEAD\n{"id": "ours"}\n=======\n{"id": "theirs"}\n>>>>>>> main\n', encoding="utf-8"
    )

    resolved = LedgerReconciler().reconcile_ledgers(str(tmp_path))

    assert resolved == [os.path.join("holon-knowledge", "ledger", "executions.jsonl")]
    assert (ledger_dir / "intents.jsonl").read_bytes() == b"\xff\xfe not utf-8\n<<<<<<< HEAD\n"
    assert (ledger_dir / "executions.jsonl").read_text(encoding="utf-8") == (
        '{"id": "base"}\n{"id": "ours"}\n{"id": "theirs"}\n'
    )


def test_reconcile_ledgers_skips_an_unreadable_first_ledger_without_a_conflicted_later_one(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "holon-knowledge" / "ledger"
    ledger_dir.mkdir(parents=True)
    (ledger_dir / "intents.jsonl").write_bytes(b"\xff\xfe\n")

    assert LedgerReconciler().reconcile_ledgers(str(tmp_path)) == []


def test_query_ledger_rejects_a_misspelt_filter(tmp_path: Path) -> None:
    ledger_file = tmp_path / "executions.jsonl"
    ledger_file.write_text('{"entry_type": "action", "outcome": "success"}\n', encoding="utf-8")
    assert ledger.query_ledger(str(ledger_file), outcome="success", limit=1) == [
        {"entry_type": "action", "outcome": "success"}
    ]
    with pytest.raises(TypeError):
        ledger.query_ledger(str(ledger_file), outcom="failure")  # type: ignore[call-arg]


def test_format_commit_trailers_rejects_a_misspelt_trailer() -> None:
    assert ledger.format_commit_trailers(review_disposition="applied", rejection_reason="inaccurate") == (
        "Action-Origin: initial_implementation\nReview-Disposition: applied\nRejection-Reason: inaccurate"
    )
    with pytest.raises(TypeError):
        ledger.format_commit_trailers(review_dispostion="applied")  # type: ignore[call-arg]
