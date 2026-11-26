from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable
from typing import Any

from sandbox_executor.knowledge.fragment_identities import RowIdentity
from sandbox_executor.knowledge.ledger_sync import compute_signature_hash, connect_wal, sync_ledger_incremental

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_GLOBAL_DB_PATH",
    "DEFAULT_PROJECT_DB_PATH",
    "ActionIndexDatabase",
    "compute_signature_hash",
    "sync_ledger_incremental",
]

DEFAULT_PROJECT_DB_PATH = "holon-knowledge/.index/action_index.db"
DEFAULT_GLOBAL_DB_PATH = os.path.expanduser("~/.holon/index/action_index.db")


class ActionIndexDatabase:
    def __init__(self, db_path: str | None = None, global_db_path: str | None = None) -> None:
        self.db_path = db_path or DEFAULT_PROJECT_DB_PATH
        self.global_db_path = global_db_path or DEFAULT_GLOBAL_DB_PATH
        self._init_db(self.db_path)

    def _init_db(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        conn = connect_wal(path)
        try:
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
        conn = connect_wal(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def sync_ledger(
        self,
        ledger_path: str,
        skip_row: Callable[[dict[str, Any]], bool] | None = None,
        identity: RowIdentity | None = None,
    ) -> int:
        return sync_ledger_incremental(self.db_path, ledger_path, skip_row, identity)
