import difflib
import json
from collections import Counter

import pytest
from sandbox_executor.flow import extract_valid_ledger_rows, reconcile_jsonl_ledger_conflict, reconcile_ledger_file
from sandbox_executor.flow.ledger_reconcile import insert_incoming_lines
from sandbox_executor.ledger.order import parse_created_at

_SORTED_BASE = [
    '{"execution_id": "B1", "created_at": "2026-10-06T08:00:00.000Z"}',
    '{"execution_id": "B2", "created_at": "2026-10-06T09:00:00.000000+00:00"}',
    '{"execution_id": "B3", "created_at": "2026-10-06T10:00:00.000Z"}',
    '{"execution_id": "B4", "created_at": "2026-10-06T11:00:00.000000+00:00"}',
    '{"execution_id": "B5", "created_at": "2026-10-06T12:00:00.000Z"}',
]
_INCOMING = [
    '{"execution_id": "I-early", "created_at": "2026-10-06T07:00:00.000Z"}',
    '{"execution_id": "I-mid", "created_at": "2026-10-06T09:30:00.000Z"}',
    '{"action_id": "I-untimed", "action": "target_sync"}',
    '{"execution_id": "I-tie", "created_at": "2026-10-06T11:00:00.000Z"}',
    '{"execution_id": "I-late", "created_at": "2026-10-06T13:00:00.000Z"}',
]


def _conflicted(kept: list[str], incoming: list[str], prefix_side: str) -> str:
    ours, theirs = (incoming, kept) if prefix_side == "theirs" else (kept, incoming)
    sections = ["<<<<<<< HEAD", *ours, "=======", *theirs, ">>>>>>> origin/main"]
    return "".join(f"{line}\n" for line in sections)


def _ids(lines: list[str]) -> list[str]:
    return [json.loads(line).get("execution_id") or json.loads(line)["action_id"] for line in lines]


def _is_ordered_subsequence(kept: list[str], merged: list[str]) -> bool:
    remaining = iter(merged)
    return all(line in remaining for line in kept)


def _only_additions(before: list[str], after: list[str]) -> bool:
    diff = list(difflib.ndiff(before, after))
    return not any(line.startswith("- ") for line in diff)


class TestLedgerReconciliation:
    """Tests for deterministic ledger union reconciliation and conflict resolution."""

    def test_extract_valid_ledger_rows_filters_conflict_markers(self):
        conflicted_content = (
            "<<<<<<< HEAD\n"
            '{"branch": "b1", "slug": "s1", "created_at": "2026-10-06T10:00:00Z"}\n'
            "||||||| parent\n"
            "malformed non-json line\n"
            "=======\n"
            '{"branch": "b2", "slug": "s2", "created_at": "2026-10-06T10:05:00Z"}\n'
            ">>>>>>> origin/main\n"
        )
        rows = extract_valid_ledger_rows(conflicted_content)
        assert len(rows) == 2
        assert rows[0]["slug"] == "s1"
        assert rows[1]["slug"] == "s2"

    # The ledgers are append-only: these cases used to assert key-based dedupe and a created_at sort. They now
    # assert that marker-free content is returned byte for byte and that conflicts resolve by pure append.

    def test_reconcile_intents_ledger_keeps_order_and_repeated_keys(self):
        content = (
            '{"slug": "intent-2", "branch": "I-intent-2", "created_at": "2026-10-06T12:00:00Z"}\n'
            '{"slug": "intent-1", "branch": "I-intent-1", "created_at": "2026-10-06T08:00:00Z"}\n'
            '{"slug": "intent-1", "branch": "I-intent-1", "created_at": "2026-10-06T09:00:00Z", "extra": "latest"}\n'
        )
        assert reconcile_jsonl_ledger_conflict(content) == content

    def test_reconcile_plans_ledger_keeps_every_revision(self):
        content = (
            '{"plan_id": "P-100", "created_at": "2026-10-06T10:00:00Z", "status": "proposed"}\n'
            '{"plan_id": "P-100", "created_at": "2026-10-06T10:00:00Z", "status": "superseded", "ledger_revision": 2}\n'
            '{"plan_id": "P-200", "created_at": "2026-10-06T11:00:00Z", "status": "proposed"}\n'
        )
        assert reconcile_jsonl_ledger_conflict(content) == content

    def test_reconcile_executions_ledger_keeps_bytes(self):
        content = (
            '{"execution_id": "E-2", "created_at": "2026-10-06T11:00:00Z", "status": "success"}\n'
            '{"execution_id":"E-1","created_at":"2026-10-06T09:00:00Z","status":"failed"}\n'
        )
        assert reconcile_jsonl_ledger_conflict(content) == content

    def test_reconcile_ledger_with_git_conflict_markers(self, tmp_path):
        ledger_path = tmp_path / "executions.jsonl"
        conflicted = (
            "<<<<<<< HEAD\n"
            '{"execution_id": "E-head", "created_at": "2026-10-06T10:00:00Z"}\n'
            "=======\n"
            '{"execution_id": "E-main", "created_at": "2026-10-06T09:00:00Z"}\n'
            ">>>>>>> origin/main\n"
        )
        ledger_path.write_text(conflicted, encoding="utf-8")
        success = reconcile_ledger_file(str(ledger_path))
        assert success is True

        reconciled = ledger_path.read_text(encoding="utf-8")
        assert "<<<<<<<" not in reconciled
        assert "=======" not in reconciled
        assert ">>>>>>>" not in reconciled
        lines = [json.loads(line) for line in reconciled.splitlines() if line.strip()]
        # Ours (E-head) is kept unchanged and theirs' older row is inserted before it, not appended.
        assert [r["execution_id"] for r in lines] == ["E-main", "E-head"]
        assert reconciled.splitlines()[1] == '{"execution_id": "E-head", "created_at": "2026-10-06T10:00:00Z"}'

    def test_reconcile_ledger_file_atomic(self, tmp_path):
        ledger_file = tmp_path / "plans.jsonl"
        ledger_file.write_text('{"plan_id": "p1", "created_at": "2026-10-06T10:00:00Z"}\n')
        assert reconcile_ledger_file(str(ledger_file)) is True
        # Verify no temp files remain in directory
        dir_files = [f.name for f in tmp_path.iterdir()]
        assert dir_files == ["plans.jsonl"]


@pytest.mark.parametrize("prefix_side", ["ours", "theirs"])
def test_merging_into_a_sorted_base_keeps_every_base_row_and_only_inserts(prefix_side: str) -> None:
    resolved = reconcile_jsonl_ledger_conflict(_conflicted(_SORTED_BASE, _INCOMING, prefix_side), prefix_side)
    merged = resolved.splitlines()

    assert _ids(merged) == ["I-early", "B1", "B2", "I-mid", "I-untimed", "B3", "B4", "I-tie", "B5", "I-late"]
    assert _is_ordered_subsequence(_SORTED_BASE, merged)
    assert Counter(merged) == Counter(_SORTED_BASE + _INCOMING)
    assert _only_additions(_SORTED_BASE, merged)
    instants = [parse_created_at(json.loads(line).get("created_at")) for line in merged]
    timed = [instant for instant in instants if instant is not None]
    assert timed == sorted(timed)


def test_an_unsorted_kept_side_is_never_reordered() -> None:
    kept = [_SORTED_BASE[2], _SORTED_BASE[0], _SORTED_BASE[4], _SORTED_BASE[1]]

    merged = insert_incoming_lines(kept, [_INCOMING[1], _INCOMING[4], kept[1]])

    assert [line for line in merged if line in kept] == kept
    assert _only_additions(kept, merged)
    # Running maximum of the kept instants is 10:00, 10:00, 12:00, 12:00, so 09:30 goes first and 13:00 last.
    assert _ids(merged) == ["I-mid", "B3", "B1", "B5", "B2", "I-late"]


def test_reconcile_ledger_file_with_theirs_as_prefix_inserts_branch_rows_on_disk(tmp_path) -> None:
    ledger_path = tmp_path / "executions.jsonl"
    ledger_path.write_text(_conflicted(_SORTED_BASE, _INCOMING[:2], "theirs"), encoding="utf-8")

    assert reconcile_ledger_file(str(ledger_path), prefix_side="theirs") is True

    merged = ledger_path.read_text(encoding="utf-8").splitlines()
    assert _ids(merged) == ["I-early", "B1", "B2", "I-mid", "B3", "B4", "B5"]
    assert _only_additions(_SORTED_BASE, merged)
