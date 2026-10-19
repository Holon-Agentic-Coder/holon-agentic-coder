"""Multi-label retrieval engine for holon consult with token bounding and session deduplication (Bean 0052 Goal 2)."""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.knowledge.index import DEFAULT_PROJECT_DB_PATH, ActionIndexDatabase

logger = logging.getLogger(__name__)

DEFAULT_LEDGERS = [
    "holon-knowledge/ledger/executions.jsonl",
    "holon-knowledge/ledger/intents.jsonl",
    "holon-knowledge/ledger/plans.jsonl",
]


@dataclass
class ConsultResult:
    """Encapsulates retrieved action knowledge records with telemetry metadata."""

    records: list[dict[str, Any]]
    total_matched: int
    latency_ms: float
    tokens_injected: int
    truncated: bool = False
    consulted_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary representation."""
        return {
            "records": self.records,
            "total_matched": self.total_matched,
            "latency_ms": round(self.latency_ms, 3),
            "tokens_injected": self.tokens_injected,
            "truncated": self.truncated,
            "consulted_ids": self.consulted_ids,
        }


def estimate_record_tokens(record: dict[str, Any]) -> int:
    """Estimate token count for a record payload using character heuristic (~4 chars/token)."""
    raw_str = record.get("payload_json")
    if not raw_str:
        raw_str = json.dumps(record)
    return max(1, math.ceil(len(raw_str) / 4.0))


class ConsultEngine:
    """Executes multi-label queries with sub-15ms warm index latency and budget bounding."""

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
        """Incrementally sync registered ledgers if file mtimes have updated."""
        for ledger_path in self.ledgers:
            try:
                self.db.sync_ledger(ledger_path)
            except Exception as e:
                logger.debug("Failed incremental sync for %s: %s", ledger_path, e)

    def consult_records(
        self,
        all_labels: list[str] | None = None,
        any_labels: list[str] | None = None,
        not_labels: list[str] | None = None,
        query_text: str | None = None,
        top_k: int = 6,
        max_tokens: int = 1200,
        session_seen_ids: set[str] | list[str] | None = None,
    ) -> ConsultResult:
        """Consult knowledge records matching conjunctive, disjunctive, negated labels, and text query."""
        self._sync_if_needed()
        start_time = time.perf_counter()

        clean_all = [lbl.strip().lower() for lbl in (all_labels or []) if lbl.strip()]
        clean_any = [lbl.strip().lower() for lbl in (any_labels or []) if lbl.strip()]
        clean_not = [lbl.strip().lower() for lbl in (not_labels or []) if lbl.strip()]
        seen_set = set(session_seen_ids or [])

        conn = self.db.get_connection()
        try:
            where_clauses: list[str] = []
            params: list[Any] = []

            # 1. Conjunctive: Must contain ALL specified labels
            if clean_all:
                placeholders = ",".join(["?"] * len(clean_all))
                where_clauses.append(
                    f"""
                    ar.record_id IN (
                        SELECT record_id FROM record_labels
                        WHERE label IN ({placeholders})
                        GROUP BY record_id
                        HAVING COUNT(DISTINCT label) = ?
                    )
                    """
                )
                params.extend(clean_all)
                params.append(len(set(clean_all)))

            # 2. Disjunctive: Must contain ANY of the specified labels
            if clean_any:
                placeholders = ",".join(["?"] * len(clean_any))
                where_clauses.append(
                    f"""
                    ar.record_id IN (
                        SELECT DISTINCT record_id FROM record_labels
                        WHERE label IN ({placeholders})
                    )
                    """
                )
                params.extend(clean_any)

            # 3. Negated: Must NOT contain any of the specified labels
            if clean_not:
                placeholders = ",".join(["?"] * len(clean_not))
                where_clauses.append(
                    f"""
                    ar.record_id NOT IN (
                        SELECT DISTINCT record_id FROM record_labels
                        WHERE label IN ({placeholders})
                    )
                    """
                )
                params.extend(clean_not)

            # 4. Text search via FTS5
            if query_text and query_text.strip():
                clean_query = query_text.strip().replace('"', '""')
                where_clauses.append(
                    """
                    ar.record_id IN (
                        SELECT record_id FROM action_records_fts
                        WHERE action_records_fts MATCH ?
                    )
                    """
                )
                params.append(f'"{clean_query}"')

            # 5. Session-level deduplication
            if seen_set:
                placeholders = ",".join(["?"] * len(seen_set))
                where_clauses.append(f"ar.record_id NOT IN ({placeholders})")
                params.extend(list(seen_set))

            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
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
            rows = cur.fetchall()

            total_matched = len(rows)
            allocated_records: list[dict[str, Any]] = []
            consulted_ids: list[str] = []
            tokens_injected = 0
            truncated = False

            eff_top_k = min(top_k, 6)

            for row in rows:
                if len(allocated_records) >= eff_top_k:
                    truncated = True
                    break

                rec_dict = {
                    "record_id": row["record_id"],
                    "action_id": row["action_id"],
                    "execution_id": row["execution_id"],
                    "action": row["action"],
                    "category": row["category"],
                    "target": row["target"],
                    "outcome": row["outcome"],
                    "verdict": row["verdict"],
                    "context_fingerprint": row["context_fingerprint"],
                    "error_context": row["error_context"],
                    "created_at": row["created_at"],
                    "payload_json": row["payload_json"],
                }

                rec_tokens = estimate_record_tokens(rec_dict)
                if tokens_injected + rec_tokens > max_tokens and allocated_records:
                    truncated = True
                    break

                allocated_records.append(rec_dict)
                consulted_ids.append(row["record_id"])
                tokens_injected += rec_tokens

            elapsed_ms = (time.perf_counter() - start_time) * 1000.0

            # Record telemetry asynchronously / in table
            try:
                all_lbls_str = ",".join(clean_all + clean_any)
                with conn:
                    conn.execute(
                        """
                        INSERT INTO consult_telemetry (
                            timestamp, query_labels, latency_ms, matched_count, tokens_injected, cache_hit
                        ) VALUES (?, ?, ?, ?, ?, ?);
                        """,
                        (
                            datetime.now(UTC).isoformat(),
                            all_lbls_str,
                            elapsed_ms,
                            total_matched,
                            tokens_injected,
                            1 if elapsed_ms < 5.0 else 0,
                        ),
                    )
            except Exception as e:
                logger.debug("Failed to record consult telemetry: %s", e)

            return ConsultResult(
                records=allocated_records,
                total_matched=total_matched,
                latency_ms=elapsed_ms,
                tokens_injected=tokens_injected,
                truncated=truncated or (total_matched > len(allocated_records)),
                consulted_ids=consulted_ids,
            )
        finally:
            conn.close()


def format_consult_results_table(result: ConsultResult) -> str:
    """Format consult results into an ANSI/ASCII summary table."""
    if not result.records:
        return f"No records found (matched: 0, latency: {result.latency_ms:.2f}ms)."

    headers = ["Record ID", "Verdict", "Action", "Target", "Fingerprint", "Created At"]
    rows: list[list[str]] = []

    for r in result.records:
        rid = str(r.get("record_id") or "")[:14]
        vrd = str(r.get("verdict") or r.get("outcome") or "-")
        act = str(r.get("action") or "-")
        tgt = str(r.get("target") or "-")
        fp = str(r.get("context_fingerprint") or "-")[:12]
        cat = str(r.get("created_at") or "")[:19]

        if len(act) > 35:
            act = act[:32] + "..."
        if len(tgt) > 25:
            tgt = tgt[:22] + "..."

        rows.append([rid, vrd, act, tgt, fp, cat])

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
