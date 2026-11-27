from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.knowledge.index import DEFAULT_PROJECT_DB_PATH, ActionIndexDatabase

logger = logging.getLogger(__name__)


def _percentile(vals: list[float], pct: float) -> float:
    if not vals:
        return 0.0
    idx = round((len(vals) - 1) * pct)
    return vals[idx]


def _query_consult_telemetry(cur: sqlite3.Cursor) -> dict[str, Any]:
    cur.execute(
        """
        SELECT latency_ms, tokens_injected, cache_hit
        FROM consult_telemetry
        ORDER BY latency_ms ASC;
        """
    )
    telemetry_rows = cur.fetchall()
    total_consults = len(telemetry_rows)
    latencies = [float(r["latency_ms"]) for r in telemetry_rows]
    tokens = sum(int(r["tokens_injected"]) for r in telemetry_rows)
    cache_hits = sum(1 for r in telemetry_rows if r["cache_hit"])
    hit_rate = (cache_hits / total_consults * 100.0) if total_consults > 0 else 0.0
    return {
        "total_consults": total_consults,
        "cache_hit_rate_pct": round(hit_rate, 2),
        "latency_p50_ms": round(_percentile(latencies, 0.50), 3),
        "latency_p90_ms": round(_percentile(latencies, 0.90), 3),
        "latency_p95_ms": round(_percentile(latencies, 0.95), 3),
        "latency_p99_ms": round(_percentile(latencies, 0.99), 3),
        "total_injected_tokens": tokens,
    }


def _query_veto_counts(cur: sqlite3.Cursor) -> tuple[int, int]:
    cur.execute("SELECT count(*) FROM active_vetoes WHERE is_shadow = 1 AND state != 'lifted';")
    shadow_vetoes = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM active_vetoes WHERE is_shadow = 0 AND state != 'lifted';")
    active_blocks = cur.fetchone()[0]
    return shadow_vetoes, active_blocks


def _query_index_totals(cur: sqlite3.Cursor) -> tuple[int, int]:
    cur.execute("SELECT count(*) FROM action_records;")
    total_records = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM action_signatures;")
    total_signatures = cur.fetchone()[0]
    return total_records, total_signatures


class TelemetryCollector:
    @classmethod
    def record_consult(  # noqa: PLR0913
        cls,
        query_labels: str,
        latency_ms: float,
        matched_count: int,
        tokens_injected: int,
        cache_hit: bool = False,
        index_db: ActionIndexDatabase | None = None,
    ) -> None:
        db = index_db or ActionIndexDatabase(DEFAULT_PROJECT_DB_PATH)
        conn = db.get_connection()
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
                        latency_ms,
                        matched_count,
                        tokens_injected,
                        1 if cache_hit else 0,
                    ),
                )
        except (sqlite3.Error, OSError) as e:
            logger.debug("Failed to record consult telemetry: %s", e)
        finally:
            conn.close()

    @classmethod
    def get_telemetry_stats(cls, index_db: ActionIndexDatabase | None = None) -> dict[str, Any]:
        db = index_db or ActionIndexDatabase(DEFAULT_PROJECT_DB_PATH)
        conn = db.get_connection()

        try:
            cur = conn.cursor()
            consult_metrics = _query_consult_telemetry(cur)
            shadow_vetoes, active_blocks = _query_veto_counts(cur)
            total_records, total_signatures = _query_index_totals(cur)

            return {
                **consult_metrics,
                "shadow_veto_warnings": shadow_vetoes,
                "active_veto_blocks": active_blocks,
                "total_action_records": total_records,
                "total_signatures": total_signatures,
            }
        finally:
            conn.close()


def format_stats_dashboard(stats: dict[str, Any]) -> str:
    lines = [
        "===========================================================",
        "               HOLON OPERATIONAL SELF-METRICS              ",
        "===========================================================",
        f"  Total Consult Invocations    : {stats.get('total_consults', 0)}",
        f"  Cache / Fast Hit Rate        : {stats.get('cache_hit_rate_pct', 0.0):.1f}%",
        f"  Latency p50                  : {stats.get('latency_p50_ms', 0.0):.2f} ms",
        f"  Latency p90                  : {stats.get('latency_p90_ms', 0.0):.2f} ms",
        f"  Latency p95                  : {stats.get('latency_p95_ms', 0.0):.2f} ms",
        f"  Latency p99                  : {stats.get('latency_p99_ms', 0.0):.2f} ms",
        f"  Total Injected Tokens        : {stats.get('total_injected_tokens', 0):,}",
        "-----------------------------------------------------------",
        f"  Shadow Veto Warnings Emitted : {stats.get('shadow_veto_warnings', 0)}",
        f"  Active Veto Execution Blocks : {stats.get('active_veto_blocks', 0)}",
        "-----------------------------------------------------------",
        f"  Indexed Action Records       : {stats.get('total_action_records', 0):,}",
        f"  Tracked Action Signatures    : {stats.get('total_signatures', 0):,}",
        "===========================================================",
    ]
    return "\n".join(lines)
