"""Byte-offset incremental sync of a JSONL ledger into the action knowledge index.

The sync runs as named steps: read the watermark, read the new rows from the last byte offset, index each row, then
write the row batch and persist the new watermark in one transaction, so a failed write never advances the watermark.
This module is a leaf: it imports nothing from ``knowledge/index.py``, which re-exports its public names.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

_FAILURE_VERDICTS = ("failure", "failed")


def compute_signature_hash(context_fingerprint: str, action_origin: str = "initial_implementation") -> str:
    """Compute deterministic SHA256 signature hash from context fingerprint and origin."""
    payload = f"{context_fingerprint}:{action_origin}".encode()
    return hashlib.sha256(payload).hexdigest()


def connect_wal(path: str) -> sqlite3.Connection:
    """Open a SQLite connection to ``path`` in WAL mode with the index's standard pragmas."""
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def _derive_context_fingerprint(data: dict[str, Any]) -> str:
    """Compute fallback context fingerprint from record dictionary."""
    if data.get("context_fingerprint"):
        return str(data["context_fingerprint"])
    target = str(data.get("target") or data.get("plan_branch") or data.get("branch") or "")
    action = str(data.get("action") or data.get("slug") or data.get("summary") or "")
    error = str(data.get("error_context") or "")
    raw = f"{target}|{action}|{error}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass(frozen=True)
class SyncWatermark:
    """How far a ledger has been synced: its mtime and byte size at the last sync."""

    mtime: float
    byte_offset: int


def read_watermark(conn: sqlite3.Connection, ledger_path: str) -> SyncWatermark:
    """Return the stored watermark for ``ledger_path``, or ``(0.0, 0)`` when it was never synced."""
    row = conn.execute(
        "SELECT last_mtime, last_byte_offset FROM ledger_sync_state WHERE ledger_path = ?",
        (ledger_path,),
    ).fetchone()
    return SyncWatermark(row[0], row[1]) if row else SyncWatermark(0.0, 0)


def ledger_unchanged(stat: os.stat_result, watermark: SyncWatermark) -> bool:
    """Whether the ledger is unchanged since the watermark: no newer mtime and the same size."""
    return stat.st_mtime <= watermark.mtime and stat.st_size == watermark.byte_offset


def resume_offset(stat: os.stat_result, watermark: SyncWatermark) -> int:
    """Return the byte offset to resume reading from; a ledger that shrank is re-read from the start."""
    return 0 if stat.st_size < watermark.byte_offset else watermark.byte_offset


ParsedRow = tuple[int, str, dict[str, Any]]


def read_new_rows(ledger_path: str, offset: int) -> tuple[list[ParsedRow], int]:
    """Read ``(line_offset, line, data)`` for each JSON row from ``offset`` on, and the new end offset.

    Blank lines and lines that fail to decode are skipped.
    """
    rows: list[ParsedRow] = []
    with open(ledger_path, encoding="utf-8") as f:
        f.seek(offset)
        while True:
            line_offset = f.tell()
            line = f.readline()
            if not line:
                break
            line_str = line.strip()
            if not line_str:
                continue
            try:
                data = json.loads(line_str)
            except Exception:
                continue
            rows.append((line_offset, line_str, data))
        return rows, f.tell()


def _parse_record_labels(raw_labels: Any) -> list[str]:
    if isinstance(raw_labels, list):
        return [str(lbl).strip().lower() for lbl in raw_labels if str(lbl).strip()]
    if isinstance(raw_labels, str):
        return [lbl.strip().lower() for lbl in raw_labels.split(",") if lbl.strip()]
    return []


def _signature_delta_kind(verdict: str, outcome: str) -> str | None:
    """Return ``success``, ``failure`` or None: which signature counter a row with this verdict/outcome bumps."""
    if verdict == "success" or outcome == "success":
        return "success"
    if verdict in _FAILURE_VERDICTS or outcome in _FAILURE_VERDICTS:
        return "failure"
    return None


@dataclass(frozen=True)
class IndexedRow:
    """One ledger row mapped to its index rows: the record, its FTS row, its labels and its signature evidence."""

    record: tuple[Any, ...]
    fts: tuple[str, str, str, str, str]
    labels: list[tuple[str, str]]
    signature_hash: str
    context_fingerprint: str
    action_origin: str
    created_at: str
    delta_kind: str | None


def index_row(line_offset: int, line_str: str, data: dict[str, Any]) -> IndexedRow:
    """Map one decoded ledger row to the index rows it produces."""
    record_id = str(
        data.get("record_id")
        or data.get("action_id")
        or data.get("execution_id")
        or data.get("branch")
        or data.get("plan_id")
        or f"rec-{uuid.uuid4().hex[:12]}"
    )
    action_id = str(data.get("action_id") or "")
    execution_id = str(data.get("execution_id") or "")
    action = str(data.get("action") or data.get("summary") or data.get("slug") or "")
    category = str(data.get("category") or data.get("event_type") or "general")
    target = str(data.get("target") or data.get("plan_branch") or data.get("branch") or "")
    outcome = str(data.get("outcome") or data.get("status") or "unknown").lower()
    verdict = str(data.get("verdict") or outcome).lower()
    error_context = str(data.get("error_context") or "")
    created_at = str(data.get("created_at") or datetime.now(UTC).isoformat())
    context_fp = _derive_context_fingerprint(data)
    action_origin = str(data.get("action_origin") or "initial_implementation")

    return IndexedRow(
        record=(
            record_id,
            action_id,
            execution_id,
            action,
            category,
            target,
            outcome,
            verdict,
            context_fp,
            error_context,
            created_at,
            line_str,
            line_offset,
        ),
        fts=(record_id, action, target, context_fp, error_context),
        labels=[(record_id, label) for label in set(_parse_record_labels(data.get("labels")))],
        signature_hash=compute_signature_hash(context_fp, action_origin),
        context_fingerprint=context_fp,
        action_origin=action_origin,
        created_at=created_at,
        delta_kind=_signature_delta_kind(verdict, outcome),
    )


def accumulate_signatures(rows: Iterable[IndexedRow]) -> dict[str, dict[str, Any]]:
    """Sum success/failure deltas per signature, keeping each signature's first-seen ``last_seen_ts``."""
    signatures: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.signature_hash not in signatures:
            signatures[row.signature_hash] = {
                "context_fingerprint": row.context_fingerprint,
                "action_origin": row.action_origin,
                "success_delta": 0,
                "failure_delta": 0,
                "last_seen_ts": row.created_at,
            }
        if row.delta_kind is not None:
            signatures[row.signature_hash][f"{row.delta_kind}_delta"] += 1
    return signatures


@dataclass
class RowBatch:
    """Everything one sync writes: record, FTS and label rows plus the per-signature deltas."""

    records: list[tuple[Any, ...]] = field(default_factory=list)
    fts: list[tuple[str, str, str, str, str]] = field(default_factory=list)
    labels: list[tuple[str, str]] = field(default_factory=list)
    signatures: dict[str, dict[str, Any]] = field(default_factory=dict)


def build_row_batch(parsed_rows: Iterable[ParsedRow]) -> RowBatch:
    """Index every parsed row and collect the results into one batch."""
    indexed = [index_row(line_offset, line_str, data) for line_offset, line_str, data in parsed_rows]
    return RowBatch(
        records=[row.record for row in indexed],
        fts=[row.fts for row in indexed],
        labels=[pair for row in indexed for pair in row.labels],
        signatures=accumulate_signatures(indexed),
    )


def write_row_batch(conn: sqlite3.Connection, batch: RowBatch) -> None:
    """Insert the batch's records, FTS rows and labels, and upsert its signature counters."""
    if batch.records:
        conn.executemany(
            """
            INSERT OR REPLACE INTO action_records (
                record_id, action_id, execution_id, action, category, target, outcome,
                verdict, context_fingerprint, error_context, created_at, payload_json, raw_byte_offset
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            batch.records,
        )

    if batch.fts:
        conn.executemany(
            """
            INSERT INTO action_records_fts (
                record_id, action, target, context_fingerprint, error_context
            ) VALUES (?, ?, ?, ?, ?);
            """,
            batch.fts,
        )

    if batch.labels:
        conn.executemany(
            """
            INSERT OR IGNORE INTO record_labels (record_id, label)
            VALUES (?, ?);
            """,
            batch.labels,
        )

    for sig_hash, sig_data in batch.signatures.items():
        conn.execute(
            """
            INSERT INTO action_signatures (
                signature_hash, context_fingerprint, action_origin,
                success_count, failure_count, last_seen_ts
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(signature_hash) DO UPDATE SET
                success_count = success_count + excluded.success_count,
                failure_count = failure_count + excluded.failure_count,
                last_seen_ts = excluded.last_seen_ts;
            """,
            (
                sig_hash,
                sig_data["context_fingerprint"],
                sig_data["action_origin"],
                sig_data["success_delta"],
                sig_data["failure_delta"],
                sig_data["last_seen_ts"],
            ),
        )


def persist_watermark(conn: sqlite3.Connection, ledger_path: str, mtime: float, offset: int) -> None:
    """Record that ``ledger_path`` is synced up to ``offset`` as of ``mtime``."""
    now_iso = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO ledger_sync_state (
            ledger_path, last_mtime, last_byte_offset, last_sync_ts
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(ledger_path) DO UPDATE SET
            last_mtime = excluded.last_mtime,
            last_byte_offset = excluded.last_byte_offset,
            last_sync_ts = excluded.last_sync_ts;
        """,
        (ledger_path, mtime, offset, now_iso),
    )


def sync_ledger_incremental(db_path: str, ledger_path: str) -> int:
    """Incrementally read new JSONL rows from ledger_path seeking from last byte offset.

    Returns the count of newly ingested rows.
    """
    if not os.path.exists(ledger_path):
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = connect_wal(db_path)
    try:
        watermark = read_watermark(conn, ledger_path)
        stat = os.stat(ledger_path)
        if ledger_unchanged(stat, watermark):
            return 0

        parsed_rows, new_byte_offset = read_new_rows(ledger_path, resume_offset(stat, watermark))
        batch = build_row_batch(parsed_rows)
        with conn:
            write_row_batch(conn, batch)
            persist_watermark(conn, ledger_path, stat.st_mtime, new_byte_offset)
        return len(batch.records)
    finally:
        conn.close()
