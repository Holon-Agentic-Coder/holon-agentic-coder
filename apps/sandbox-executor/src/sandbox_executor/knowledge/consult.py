from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.knowledge.consult_filters import ConsultQuery, build_where, clean_labels, row_to_record
from sandbox_executor.knowledge.index import DEFAULT_PROJECT_DB_PATH, ActionIndexDatabase
from sandbox_executor.knowledge.ledger_fragment_sync import sync_ledger_and_fragments

logger = logging.getLogger(__name__)

DEFAULT_LEDGERS = [
    "holon-knowledge/ledger/executions.jsonl",
    "holon-knowledge/ledger/intents.jsonl",
    "holon-knowledge/ledger/plans.jsonl",
]

_CHARS_PER_TOKEN_ESTIMATE = 4.0
_MAX_CONSULT_TOP_K = 6
_CACHE_HIT_LATENCY_MS_THRESHOLD = 5.0
_RECORD_ID_DISPLAY_WIDTH = 14
_FINGERPRINT_DISPLAY_WIDTH = 12
_CREATED_AT_DISPLAY_WIDTH = 19
_ACTION_DISPLAY_MAX_WIDTH = 35
_TARGET_DISPLAY_MAX_WIDTH = 25
_ELLIPSIS = "..."


@dataclass
class ConsultResult:
    records: list[dict[str, Any]]
    total_matched: int
    latency_ms: float
    tokens_injected: int
    truncated: bool = False
    consulted_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "records": self.records,
            "total_matched": self.total_matched,
            "latency_ms": round(self.latency_ms, 3),
            "tokens_injected": self.tokens_injected,
            "truncated": self.truncated,
            "consulted_ids": self.consulted_ids,
        }


def estimate_record_tokens(record: dict[str, Any]) -> int:
    raw_str = record.get("payload_json")
    if not raw_str:
        raw_str = json.dumps(record)
    return max(1, math.ceil(len(raw_str) / _CHARS_PER_TOKEN_ESTIMATE))


class ConsultEngine:
    def __init__(
        self,
        db: ActionIndexDatabase | None = None,
        project_db_path: str | None = None,
        global_db_path: str | None = None,
        auto_sync_ledgers: list[str] | None = None,
    ) -> None:
        self.db = db or ActionIndexDatabase(
            db_path=project_db_path or DEFAULT_PROJECT_DB_PATH,
            global_db_path=global_db_path,
        )
        self.ledgers = auto_sync_ledgers if auto_sync_ledgers is not None else list(DEFAULT_LEDGERS)

    def _sync_if_needed(self) -> None:
        for ledger in self.ledgers:
            sync_ledger_and_fragments(self.db.db_path, ledger)

    def consult_records(self, **filters: Any) -> ConsultResult:
        query = ConsultQuery(**filters)
        self._sync_if_needed()
        start_time = time.perf_counter()

        conn = self.db.get_connection()
        try:
            rows = _fetch_matches(conn, query)
            allocation = _allocate_within_budget(rows, query.top_k, query.max_tokens)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0

            query_labels = ",".join(clean_labels(query.all_labels) + clean_labels(query.any_labels))
            _record_telemetry(conn, query_labels, elapsed_ms, len(rows), allocation.tokens_injected)

            return ConsultResult(
                records=allocation.records,
                total_matched=len(rows),
                latency_ms=elapsed_ms,
                tokens_injected=allocation.tokens_injected,
                truncated=allocation.truncated or (len(rows) > len(allocation.records)),
                consulted_ids=allocation.consulted_ids,
            )
        finally:
            conn.close()


def _fetch_matches(conn: sqlite3.Connection, query: ConsultQuery) -> list[sqlite3.Row]:
    where_sql, params = build_where(query)
    query_sql = f"""
        SELECT ar.record_id, ar.action_id, ar.execution_id, ar.action, ar.category,
               ar.target, ar.outcome, ar.verdict, ar.context_fingerprint,
               ar.error_context, ar.created_at, ar.payload_json
        FROM action_records ar
        {where_sql}
        ORDER BY ar.created_at DESC
    """
    cur = conn.cursor()
    cur.execute(query_sql, params)
    return cur.fetchall()


@dataclass
class _BudgetAllocation:
    records: list[dict[str, Any]] = field(default_factory=list)
    consulted_ids: list[str] = field(default_factory=list)
    tokens_injected: int = 0
    truncated: bool = False


def _allocate_within_budget(rows: list[sqlite3.Row], top_k: int, max_tokens: int) -> _BudgetAllocation:
    allocation = _BudgetAllocation()
    effective_top_k = min(top_k, _MAX_CONSULT_TOP_K)
    for row in rows:
        if len(allocation.records) >= effective_top_k:
            allocation.truncated = True
            break

        rec_dict = row_to_record(row)
        rec_tokens = estimate_record_tokens(rec_dict)
        if allocation.tokens_injected + rec_tokens > max_tokens and allocation.records:
            allocation.truncated = True
            break

        allocation.records.append(rec_dict)
        allocation.consulted_ids.append(row["record_id"])
        allocation.tokens_injected += rec_tokens
    return allocation


def _record_telemetry(
    conn: sqlite3.Connection, query_labels: str, elapsed_ms: float, matched: int, tokens_injected: int
) -> None:
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO consult_telemetry (
                    timestamp, query_labels, latency_ms, matched_count, tokens_injected, cache_hit
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    datetime.now(UTC).isoformat(),
                    query_labels,
                    elapsed_ms,
                    matched,
                    tokens_injected,
                    1 if elapsed_ms < _CACHE_HIT_LATENCY_MS_THRESHOLD else 0,
                ),
            )
    except (sqlite3.Error, OSError) as e:
        logger.debug("Failed to record consult telemetry: %s", e)


def _truncate_cell(value: str, max_width: int) -> str:
    if len(value) > max_width:
        return value[: max_width - len(_ELLIPSIS)] + _ELLIPSIS
    return value


def format_consult_results_table(result: ConsultResult) -> str:
    if not result.records:
        return f"No records found (matched: 0, latency: {result.latency_ms:.2f}ms)."

    headers = ["Record ID", "Verdict", "Action", "Target", "Fingerprint", "Created At"]
    rows: list[list[str]] = []

    for r in result.records:
        record_id = str(r.get("record_id") or "")[:_RECORD_ID_DISPLAY_WIDTH]
        verdict = str(r.get("verdict") or r.get("outcome") or "-")
        action = _truncate_cell(str(r.get("action") or "-"), _ACTION_DISPLAY_MAX_WIDTH)
        target = _truncate_cell(str(r.get("target") or "-"), _TARGET_DISPLAY_MAX_WIDTH)
        fingerprint = str(r.get("context_fingerprint") or "-")[:_FINGERPRINT_DISPLAY_WIDTH]
        created_at = str(r.get("created_at") or "")[:_CREATED_AT_DISPLAY_WIDTH]

        rows.append([record_id, verdict, action, target, fingerprint, created_at])

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    def format_row(cells: list[str]) -> str:
        padded = [cell.ljust(col_widths[i]) for i, cell in enumerate(cells)]
        return "| " + " | ".join(padded) + " |"

    separator = "|-" + "-|-".join(["-" * w for w in col_widths]) + "-|"
    header_line = format_row(headers)

    meta_line = (
        f"Matched: {result.total_matched} | Returned: {len(result.records)} | "
        f"Tokens: {result.tokens_injected} | Latency: {result.latency_ms:.2f}ms"
    )
    if result.truncated:
        meta_line += " (capped)"

    return "\n".join([header_line, separator] + [format_row(r) for r in rows] + ["", meta_line])
