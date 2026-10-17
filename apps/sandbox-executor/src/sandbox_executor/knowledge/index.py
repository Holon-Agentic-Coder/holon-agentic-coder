"""Durable SQLite indexing engine with FTS5, inverted labels, and byte-offset incremental sync (Bean 0052 Goal 1)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PROJECT_DB_PATH = "holon-knowledge/.index/action_index.db"
DEFAULT_GLOBAL_DB_PATH = os.path.expanduser("~/.holon/index/action_index.db")


def compute_signature_hash(context_fingerprint: str, action_origin: str = "initial_implementation") -> str:
    """Compute deterministic SHA256 signature hash from context fingerprint and origin."""
    payload = f"{context_fingerprint}:{action_origin}".encode()
    return hashlib.sha256(payload).hexdigest()


class ActionIndexDatabase:
    """Manages dual-tier SQLite action knowledge index."""

    def __init__(self, db_path: str | None = None, global_db_path: str | None = None) -> None:
        self.db_path = db_path or DEFAULT_PROJECT_DB_PATH
        self.global_db_path = global_db_path or DEFAULT_GLOBAL_DB_PATH
        self._init_db(self.db_path)

    def _init_db(self, path: str) -> None:
        """Initialise schema and tables with WAL mode enabled."""
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        conn = sqlite3.connect(path, timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA busy_timeout=5000;")

            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ledger_sync_state (
                        ledger_path TEXT PRIMARY KEY,
                        last_mtime REAL NOT NULL,
                        last_byte_offset INTEGER NOT NULL,
                        last_sync_ts TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS action_records (
                        record_id TEXT PRIMARY KEY,
                        action_id TEXT,
                        execution_id TEXT,
                        action TEXT,
                        category TEXT,
                        target TEXT,
                        outcome TEXT,
                        verdict TEXT,
                        context_fingerprint TEXT,
                        error_context TEXT,
                        created_at TEXT,
                        payload_json TEXT,
                        raw_byte_offset INTEGER
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_action_records_action_id
                    ON action_records(action_id);
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_action_records_verdict
                    ON action_records(verdict);
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_action_records_fingerprint
                    ON action_records(context_fingerprint);
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_action_records_created_at
                    ON action_records(created_at);
                    """
                )

                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS action_records_fts USING fts5(
                        record_id UNINDEXED,
                        action,
                        target,
                        context_fingerprint,
                        error_context
                    );
                    """
                )

                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS record_labels (
                        record_id TEXT NOT NULL,
                        label TEXT NOT NULL,
                        PRIMARY KEY (record_id, label)
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_record_labels_label
                    ON record_labels(label);
                    """
                )

                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS action_signatures (
                        signature_hash TEXT PRIMARY KEY,
                        context_fingerprint TEXT NOT NULL,
                        action_origin TEXT NOT NULL,
                        success_count INTEGER NOT NULL DEFAULT 0,
                        failure_count INTEGER NOT NULL DEFAULT 0,
                        last_seen_ts TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_action_signatures_fingerprint
                    ON action_signatures(context_fingerprint);
                    """
                )

                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS active_vetoes (
                        context_fingerprint TEXT PRIMARY KEY,
                        signature_hash TEXT NOT NULL,
                        reproduction_count INTEGER NOT NULL DEFAULT 0,
                        state TEXT NOT NULL,
                        decay_weight REAL NOT NULL DEFAULT 1.0,
                        is_shadow BOOLEAN NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """
                )

                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS consult_telemetry (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        query_labels TEXT,
                        latency_ms REAL NOT NULL,
                        matched_count INTEGER NOT NULL,
                        tokens_injected INTEGER NOT NULL,
                        cache_hit BOOLEAN NOT NULL DEFAULT 0
                    );
                    """
                )
        finally:
            conn.close()

    def get_connection(self) -> sqlite3.Connection:
        """Return an active connection to the project SQLite database."""
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def sync_ledger(self, ledger_path: str) -> int:
        """Incrementally synchronise a ledger file into this index database."""
        return sync_ledger_incremental(self.db_path, ledger_path)


def _derive_context_fingerprint(data: dict[str, Any]) -> str:
    """Compute fallback context fingerprint from record dictionary."""
    if data.get("context_fingerprint"):
        return str(data["context_fingerprint"])
    target = str(data.get("target") or data.get("plan_branch") or data.get("branch") or "")
    action = str(data.get("action") or data.get("slug") or data.get("summary") or "")
    error = str(data.get("error_context") or "")
    raw = f"{target}|{action}|{error}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def sync_ledger_incremental(db_path: str, ledger_path: str) -> int:
    """Incrementally read new JSONL rows from ledger_path seeking from last byte offset.

    Returns the count of newly ingested rows.
    """
    if not os.path.exists(ledger_path):
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT last_mtime, last_byte_offset FROM ledger_sync_state WHERE ledger_path = ?",
            (ledger_path,),
        )
        row = cur.fetchone()
        last_mtime = row[0] if row else 0.0
        last_byte_offset = row[1] if row else 0

        stat = os.stat(ledger_path)
        current_mtime = stat.st_mtime
        current_size = stat.st_size

        if current_mtime <= last_mtime and current_size == last_byte_offset:
            return 0

        if current_size < last_byte_offset:
            last_byte_offset = 0

        ingested_count = 0
        records_to_insert: list[tuple[Any, ...]] = []
        labels_to_insert: list[tuple[str, str]] = []
        fts_to_insert: list[tuple[str, str, str, str, str]] = []
        signatures_to_update: dict[str, dict[str, Any]] = {}

        with open(ledger_path, encoding="utf-8") as f:
            f.seek(last_byte_offset)
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

                records_to_insert.append(
                    (
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
                    )
                )

                fts_to_insert.append((record_id, action, target, context_fp, error_context))

                raw_labels = data.get("labels")
                extracted_labels: list[str] = []
                if isinstance(raw_labels, list):
                    extracted_labels = [str(lbl).strip().lower() for lbl in raw_labels if str(lbl).strip()]
                elif isinstance(raw_labels, str):
                    extracted_labels = [lbl.strip().lower() for lbl in raw_labels.split(",") if lbl.strip()]

                for label in set(extracted_labels):
                    labels_to_insert.append((record_id, label))

                sig_hash = compute_signature_hash(context_fp, action_origin)
                if sig_hash not in signatures_to_update:
                    signatures_to_update[sig_hash] = {
                        "context_fingerprint": context_fp,
                        "action_origin": action_origin,
                        "success_delta": 0,
                        "failure_delta": 0,
                        "last_seen_ts": created_at,
                    }

                if verdict == "success" or outcome == "success":
                    signatures_to_update[sig_hash]["success_delta"] += 1
                elif verdict in ("failure", "failed") or outcome in ("failure", "failed"):
                    signatures_to_update[sig_hash]["failure_delta"] += 1

                ingested_count += 1

            new_byte_offset = f.tell()

        with conn:
            if records_to_insert:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO action_records (
                        record_id, action_id, execution_id, action, category, target, outcome,
                        verdict, context_fingerprint, error_context, created_at, payload_json, raw_byte_offset
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    records_to_insert,
                )

            if fts_to_insert:
                conn.executemany(
                    """
                    INSERT INTO action_records_fts (
                        record_id, action, target, context_fingerprint, error_context
                    ) VALUES (?, ?, ?, ?, ?);
                    """,
                    fts_to_insert,
                )

            if labels_to_insert:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO record_labels (record_id, label)
                    VALUES (?, ?);
                    """,
                    labels_to_insert,
                )

            for sig_hash, sig_data in signatures_to_update.items():
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
                (ledger_path, current_mtime, new_byte_offset, now_iso),
            )

        return ingested_count
    finally:
        conn.close()
