"""Unit tests for the named steps of the incremental ledger sync in ``knowledge/ledger_sync.py``."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.knowledge import ledger_sync
from sandbox_executor.knowledge.index import ActionIndexDatabase
from sandbox_executor.knowledge.ledger_sync import (
    IndexedRow,
    SyncWatermark,
    accumulate_signatures,
    index_row,
    ledger_unchanged,
    read_new_rows,
    read_watermark,
    resume_offset,
    sync_ledger_incremental,
)


def _write_ledger(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _stat(size: int, mtime: float) -> os.stat_result:
    return os.stat_result((0, 0, 0, 0, 0, 0, size, 0, mtime, 0))


def test_read_new_rows_resumes_from_offset_and_skips_blank_and_corrupt_lines(tmp_path: Path) -> None:
    ledger = tmp_path / "executions.jsonl"
    first = json.dumps({"action_id": "a1"}) + "\n"
    ledger.write_text(first + "\n{corrupt\n" + json.dumps({"action_id": "a2"}) + "\n", encoding="utf-8")

    rows, end = read_new_rows(str(ledger), len(first))

    assert [data["action_id"] for _, _, data in rows] == ["a2"]
    assert rows[0][0] == len(first) + len("\n{corrupt\n")
    assert end == ledger.stat().st_size
    assert read_new_rows(str(ledger), end) == ([], end)


def test_ledger_unchanged_and_shrunk_ledger_resets_to_start() -> None:
    watermark = SyncWatermark(mtime=100.0, byte_offset=50)

    assert ledger_unchanged(_stat(50, 100.0), watermark)
    assert not ledger_unchanged(_stat(50, 101.0), watermark)
    assert not ledger_unchanged(_stat(60, 99.0), watermark)
    assert resume_offset(_stat(60, 101.0), watermark) == 50
    assert resume_offset(_stat(20, 101.0), watermark) == 0


def test_index_row_deduplicates_labels_and_derives_fields() -> None:
    data = {"action_id": "a1", "labels": "UI, ui ,db", "outcome": "FAILED", "created_at": "2026-01-01T00:00:00"}

    row = index_row(7, json.dumps(data), data)

    assert row.record[0] == "a1"
    assert row.record[6:8] == ("failed", "failed")
    assert row.record[-2:] == (json.dumps(data), 7)
    assert sorted(row.labels) == [("a1", "db"), ("a1", "ui")]
    assert row.fts[0] == "a1"
    assert row.delta_kind == "failure"
    assert row.action_origin == "initial_implementation"


def test_index_row_without_ids_gets_a_generated_record_id() -> None:
    row = index_row(0, "{}", {})

    assert row.record[0].startswith("rec-")
    assert row.delta_kind is None


def _indexed(signature: str, created_at: str, delta_kind: str | None) -> IndexedRow:
    return IndexedRow(
        record=(),
        fts=("", "", "", "", ""),
        labels=[],
        signature_hash=signature,
        context_fingerprint=f"fp-{signature}",
        action_origin="pr_review",
        created_at=created_at,
        delta_kind=delta_kind,
    )


def test_accumulate_signatures_sums_deltas_and_keeps_first_seen_timestamp() -> None:
    signatures = accumulate_signatures(
        [_indexed("s1", "t1", "success"), _indexed("s2", "t2", None), _indexed("s1", "t3", "failure")]
    )

    assert list(signatures) == ["s1", "s2"]
    assert signatures["s1"] == {
        "context_fingerprint": "fp-s1",
        "action_origin": "pr_review",
        "success_delta": 1,
        "failure_delta": 1,
        "last_seen_ts": "t1",
    }
    assert (signatures["s2"]["success_delta"], signatures["s2"]["failure_delta"]) == (0, 0)


def test_consecutive_syncs_ingest_new_rows_then_nothing(tmp_path: Path) -> None:
    db_path = str(tmp_path / "index.db")
    ledger = tmp_path / "executions.jsonl"
    ActionIndexDatabase(db_path=db_path)
    _write_ledger(ledger, [{"action_id": "a1", "outcome": "success"}, {"action_id": "a2", "outcome": "failure"}])

    assert sync_ledger_incremental(db_path, str(ledger)) == 2
    assert sync_ledger_incremental(db_path, str(ledger)) == 0


def test_failed_batch_write_rolls_back_and_keeps_the_watermark(tmp_path: Path) -> None:
    db_path = str(tmp_path / "index.db")
    ledger = tmp_path / "executions.jsonl"
    ActionIndexDatabase(db_path=db_path)
    _write_ledger(ledger, [{"action_id": "a1", "outcome": "success"}])

    def write_then_fail(conn, batch) -> None:
        conn.execute("INSERT INTO record_labels (record_id, label) VALUES ('a1', 'partial')")
        raise RuntimeError("disk full")

    with patch.object(ledger_sync, "write_row_batch", write_then_fail), pytest.raises(RuntimeError):
        sync_ledger_incremental(db_path, str(ledger))

    conn = ledger_sync.connect_wal(db_path)
    try:
        assert read_watermark(conn, str(ledger)) == SyncWatermark(0.0, 0)
        assert conn.execute("SELECT COUNT(*) FROM record_labels").fetchone()[0] == 0
    finally:
        conn.close()
    assert sync_ledger_incremental(db_path, str(ledger)) == 1
