"""Hermetic unit tests validating the sandbox_executor.ledger package structure and exports."""

from __future__ import annotations

from pathlib import Path

from sandbox_executor.flow.ledger_reconcile import (
    reconcile_ledger_file as flow_reconcile_ledger_file,
)
from sandbox_executor.ledger import (
    ActionRecord,
    LedgerLock,
    LedgerReconciler,
    LedgerRewriteError,
    SummaryRecord,
    append_action_record,
    extract_valid_ledger_rows,
    format_query_results_table,
    query_ledger,
    reconcile_ledger_file,
)
from sandbox_executor.ledger.lock import (
    LedgerLock as SubLedgerLock,
)
from sandbox_executor.ledger.lock import (
    LedgerRewriteError as SubLedgerRewriteError,
)
from sandbox_executor.ledger.query import (
    format_query_results_table as sub_format_table,
)
from sandbox_executor.ledger.query import (
    query_ledger as sub_query_ledger,
)
from sandbox_executor.ledger.reconcile import (
    LedgerReconciler as SubLedgerReconciler,
)
from sandbox_executor.ledger.reconcile import (
    extract_valid_ledger_rows as sub_extract,
)
from sandbox_executor.ledger.reconcile import (
    reconcile_ledger_file as sub_reconcile_file,
)
from sandbox_executor.ledger.rows import (
    ActionRecord as SubActionRecord,
)
from sandbox_executor.ledger.rows import (
    SummaryRecord as SubSummaryRecord,
)
from sandbox_executor.ledger.rows import (
    append_action_record as sub_append_action,
)


def test_package_exports_match_submodules():
    """Verify that symbols exported from ledger package match submodule definitions."""
    assert LedgerLock is SubLedgerLock
    assert LedgerRewriteError is SubLedgerRewriteError
    assert ActionRecord is SubActionRecord
    assert SummaryRecord is SubSummaryRecord
    assert append_action_record is sub_append_action
    assert query_ledger is sub_query_ledger
    assert format_query_results_table is sub_format_table
    assert LedgerReconciler is SubLedgerReconciler
    assert extract_valid_ledger_rows is sub_extract
    assert reconcile_ledger_file is sub_reconcile_file


def test_ledger_reconciler_pure_operations():
    """LedgerReconciler correctly resolves conflict markers preserving untouched prefix."""
    conflict_text = (
        "<<<<<<< ours\n"
        '{"entry_type": "action", "action_id": "ACT-1"}\n'
        "=======\n"
        '{"entry_type": "action", "action_id": "ACT-1"}\n'
        '{"entry_type": "action", "action_id": "ACT-2"}\n'
        ">>>>>>> theirs\n"
    )

    prefix, resolved = LedgerReconciler.resolve_append_only_conflict(conflict_text, prefix_side="ours")
    assert '{"entry_type": "action", "action_id": "ACT-1"}\n' in prefix
    assert resolved.startswith(prefix)
    assert '{"entry_type": "action", "action_id": "ACT-2"}' in resolved

    rows = LedgerReconciler.extract_valid_ledger_rows(conflict_text)
    assert len(rows) == 3
    assert rows[0]["action_id"] == "ACT-1"
    assert rows[1]["action_id"] == "ACT-1"
    assert rows[2]["action_id"] == "ACT-2"


def test_flow_ledger_reconcile_delegates_to_ledger_package(tmp_path: Path):
    """sandbox_executor.flow.ledger_reconcile functions delegate cleanly to LedgerReconciler."""
    ledger_path = str(tmp_path / "test.jsonl")
    content = (
        '{"entry_type": "action", "action_id": "ACT-1"}\n'
        "<<<<<<< ours\n"
        '{"entry_type": "action", "action_id": "ACT-2"}\n'
        "=======\n"
        '{"entry_type": "action", "action_id": "ACT-3"}\n'
        ">>>>>>> theirs\n"
    )
    Path(ledger_path).write_text(content, encoding="utf-8")

    assert flow_reconcile_ledger_file(ledger_path) is True
    resolved_text = Path(ledger_path).read_text(encoding="utf-8")
    assert "<<<<<<<" not in resolved_text
    assert '{"entry_type": "action", "action_id": "ACT-2"}' in resolved_text
    assert '{"entry_type": "action", "action_id": "ACT-3"}' in resolved_text


def test_rows_and_query_integration(tmp_path: Path):
    """Writing action and summary records and querying them via ledger package."""
    ledger_path = str(tmp_path / "executions.jsonl")
    act = ActionRecord(
        action_id="ACT-100",
        execution_id="E-100",
        action="test action",
        category="code",
        target="src/module.py",
        outcome="success",
    )
    append_action_record(ledger_path, act)

    records = query_ledger(ledger_path, entry_type="action", category="code")
    assert len(records) == 1
    assert records[0]["action_id"] == "ACT-100"

    table = format_query_results_table(records)
    assert "ACT-100" in table or "test action" in table
