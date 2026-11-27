from __future__ import annotations

import glob
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.knowledge.index import DEFAULT_PROJECT_DB_PATH, ActionIndexDatabase
from sandbox_executor.knowledge.promotion import evaluate_action_promotion
from sandbox_executor.knowledge.promotion_rules import is_review_chatter_context
from sandbox_executor.knowledge.stable_envelope import (
    build_kb_envelope,
    build_wb_envelope,
    stable_advisory_text,
    write_envelope_once,
)
from sandbox_executor.ledger.merged import read_merged_rows

logger = logging.getLogger(__name__)

DEFAULT_KB_DIR = "holon-knowledge/kb"
DEFAULT_WB_DIR = "holon-knowledge/wisdom"
TEST_FAILURE_ACTION = "test_failure"
MIN_SIGNATURE_RUNS = 2
EXECUTIONS_LEDGER_NAME = "executions.jsonl"


_INDEX_FIELDS: dict[str, tuple[str, ...]] = {
    "kb_id": ("kb_id", "kb_type", "status", "created_ts", "tags"),
    "wb_id": ("wb_id", "wb_type", "status", "created_ts"),
}


def _index_entry(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as f:
            envelope = json.load(f)
    except (OSError, ValueError) as exc:
        logger.warning("Skipping unreadable knowledge entry %s: %s", path, exc)
        return None
    id_key = next((k for k in _INDEX_FIELDS if isinstance(envelope, dict) and k in envelope), None)
    if id_key is None:
        logger.warning("Skipping knowledge entry without kb_id or wb_id: %s", path)
        return None
    entry: dict[str, Any] = {field: envelope.get(field) for field in _INDEX_FIELDS[id_key]}
    entry["file"] = os.path.basename(path)
    if id_key == "kb_id" and entry["tags"] is None:
        entry["tags"] = []
    return entry


def load_index(subfolder: str) -> list[dict[str, Any]]:
    if not os.path.isdir(subfolder):
        return []
    paths = sorted(glob.glob(os.path.join(subfolder, "KB-*.json")) + glob.glob(os.path.join(subfolder, "WB-*.json")))
    entries = [entry for entry in map(_index_entry, paths) if entry is not None]
    return sorted(entries, key=lambda e: (str(e.get("created_ts") or ""), str(e.get("kb_id") or e.get("wb_id"))))


def persist_kb_fact(fact_data: dict[str, Any], kb_dir: str = DEFAULT_KB_DIR) -> str:
    data = dict(fact_data)
    kb_type = str(data.get("kb_type") or "pattern").lower()
    kb_id = str(data.get("kb_id") or f"KB-{kb_type}-{data.get('context_fingerprint', 'item')[:8]}")

    subfolder = "patterns"
    if "failure" in kb_type:
        subfolder = "failure_modes"
    elif "tactic" in kb_type:
        subfolder = "tactics"

    target_dir = os.path.join(kb_dir, subfolder)
    out_file = os.path.join(target_dir, f"{kb_id}.json")
    if os.path.exists(out_file):
        return out_file
    return write_envelope_once(out_file, build_kb_envelope(data, kb_id, kb_type))


def persist_wb_rule(rule_data: dict[str, Any], wb_dir: str = DEFAULT_WB_DIR) -> str:
    data = dict(rule_data)
    wb_type = str(data.get("wb_type") or "heuristic").lower()
    wb_id = str(data.get("wb_id") or f"WB-{wb_type}-{data.get('context_fingerprint', 'rule')[:8]}")

    subfolder = "heuristics"
    if "invariant" in wb_type:
        subfolder = "invariants"

    target_dir = os.path.join(wb_dir, subfolder)
    out_file = os.path.join(target_dir, f"{wb_id}.json")
    if os.path.exists(out_file):
        return out_file
    return write_envelope_once(out_file, build_wb_envelope(data, wb_id, wb_type))


def _already_promoted(conn: Any, sig: Any, kb_dir: str, wb_dir: str) -> bool:
    context_fingerprint = sig["context_fingerprint"]
    fp8 = context_fingerprint[:8]
    if sig["success_count"] == 0 and os.path.exists(os.path.join(kb_dir, "failure_modes", f"KB-failure-{fp8}.json")):
        veto = conn.execute(
            "SELECT state, reproduction_count FROM active_vetoes WHERE context_fingerprint = ?;",
            (context_fingerprint,),
        ).fetchone()
        if veto is None:
            return False
        if veto["state"] == "lifted":
            return True
        return veto["state"] == "proven-fail" and veto["reproduction_count"] >= sig["failure_count"]
    pattern_path = os.path.join(kb_dir, "patterns", f"KB-pattern-{fp8}.json")
    heuristic_path = os.path.join(wb_dir, "heuristics", f"WB-heuristic-{fp8}.json")
    return os.path.exists(pattern_path) and os.path.exists(heuristic_path)


def _fetch_sample_context_records(cur: Any, context_fingerprint: str) -> list[Any]:
    cur.execute(
        """
        SELECT action, target, category, error_context, record_id
        FROM action_records
        WHERE context_fingerprint = ?
        ORDER BY created_at ASC, record_id ASC LIMIT 5;
        """,
        (context_fingerprint,),
    )
    return cur.fetchall()


def _promote_failure_candidate(
    eval_res: Any,
    records: list[Any],
    kb_dir: str,
    dry_run: bool,
) -> dict[str, Any] | None:
    if eval_res.failure_ladder_state not in ("repeated", "proven-fail"):
        return None
    kb_id = f"KB-failure-{eval_res.context_fingerprint[:8]}"
    if os.path.exists(os.path.join(kb_dir, "failure_modes", f"{kb_id}.json")):
        return None

    sample_act = records[0]["action"] if records else "unknown action"
    sample_tgt = records[0]["target"] if records else "unknown target"
    item = {
        "kb_id": kb_id,
        "kb_type": "failure_mode",
        "context_fingerprint": eval_res.context_fingerprint,
        "evidence": {
            "ledger_refs": [r["record_id"] for r in records],
            "success_count": eval_res.success_count,
            "failure_count": eval_res.failure_count,
            "ladder_state": eval_res.failure_ladder_state,
        },
        "tags": [records[0]["category"] if records else "failure", "veto"],
        "payload": {
            "name": f"Failure mode in {sample_tgt}",
            "description": stable_advisory_text("failure", sample_act),
            "action": sample_act,
            "symptoms": [records[0]["error_context"]] if records and records[0]["error_context"] else [],
        },
    }
    if not dry_run:
        item["persisted_path"] = persist_kb_fact(item, kb_dir=kb_dir)
    return item


def _build_wisdom_heuristic_item(
    eval_res: Any, kb_id: str, wb_id: str, sample_tgt: str, sample_act: str
) -> dict[str, Any]:
    return {
        "wb_id": wb_id,
        "wb_type": "heuristic",
        "context_fingerprint": eval_res.context_fingerprint,
        "ascension_evidence": {
            "project_count": 1,
            "total_executions": eval_res.total_trials,
            "wilson_lower": eval_res.wilson_lower,
            "provenance": [{"kb_id": kb_id}],
        },
        "payload": {
            "name": f"Universal heuristic: {sample_tgt}",
            "advisory": stable_advisory_text("heuristic", sample_act),
        },
    }


def _promote_success_candidate(
    eval_res: Any,
    records: list[Any],
    kb_dir: str,
    wb_dir: str,
    dry_run: bool,
) -> list[dict[str, Any]]:
    if eval_res.success_ladder_state not in ("likely-success", "proven-success"):
        return []

    items: list[dict[str, Any]] = []
    sample_act = records[0]["action"] if records else "unknown action"
    sample_tgt = records[0]["target"] if records else "unknown target"
    source_ids = [r["record_id"] for r in records]
    kb_id = f"KB-pattern-{eval_res.context_fingerprint[:8]}"
    item = {
        "kb_id": kb_id,
        "kb_type": "pattern",
        "context_fingerprint": eval_res.context_fingerprint,
        "evidence": {
            "ledger_refs": source_ids,
            "success_count": eval_res.success_count,
            "failure_count": eval_res.failure_count,
            "wilson_lower": eval_res.wilson_lower,
            "posterior_mean": eval_res.posterior_mean,
            "ladder_state": eval_res.success_ladder_state,
        },
        "tags": [records[0]["category"] if records else "pattern", "proven"],
        "payload": {
            "name": f"Proven approach for {sample_tgt}",
            "description": stable_advisory_text("pattern", sample_act),
            "action": sample_act,
        },
    }
    if not os.path.exists(os.path.join(kb_dir, "patterns", f"{kb_id}.json")):
        if not dry_run:
            item["persisted_path"] = persist_kb_fact(item, kb_dir=kb_dir)
        items.append(item)

    wb_id = f"WB-heuristic-{eval_res.context_fingerprint[:8]}"
    heuristic_exists = os.path.exists(os.path.join(wb_dir, "heuristics", f"{wb_id}.json"))
    should_ascend_to_wisdom_heuristic = eval_res.success_ladder_state == "proven-success" and not heuristic_exists
    if should_ascend_to_wisdom_heuristic:
        wb_item = _build_wisdom_heuristic_item(eval_res, item["kb_id"], wb_id, sample_tgt, sample_act)
        if not dry_run:
            wb_item["persisted_path"] = persist_wb_rule(wb_item, wb_dir=wb_dir)
        items.append(wb_item)

    return items


def _test_failure_rows_by_signature(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("action") == TEST_FAILURE_ACTION and row.get("failure_signature") and row.get("run_id"):
            grouped.setdefault(str(row["failure_signature"]), []).append(row)
    return grouped


def _distinct_runs(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row["run_id"]) for row in rows}


def _signature_classification(rows: list[dict[str, Any]]) -> str:
    return next((str(row["classification"]) for row in reversed(rows) if row.get("classification")), "unclassified")


def _signature_failure_item(signature: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    classification = _signature_classification(rows)
    symptom = rows[0].get("error_context")
    # why: build_kb_envelope keeps no evidence keys for KB facts, so the ids and classification live in the payload.
    return {
        "kb_id": f"KB-failure-{signature}",
        "kb_type": "failure_mode",
        "context_fingerprint": signature,
        "tags": ["test", "failure_signature", classification],
        "payload": {
            "name": f"Repeated test failure {signature}",
            "description": stable_advisory_text("failure", TEST_FAILURE_ACTION),
            "action": TEST_FAILURE_ACTION,
            "failure_signature": signature,
            "failing_test_ids": sorted({str(i) for row in rows for i in row.get("failing_test_ids") or []}),
            "classification": classification,
            "stages": sorted({str(row.get("stage")) for row in rows if row.get("stage")}),
            "symptoms": [symptom] if symptom else [],
        },
    }


def promote_repeated_failure_signatures(
    ledger_path: str, kb_dir: str = DEFAULT_KB_DIR, dry_run: bool = False
) -> list[dict[str, Any]]:
    promoted: list[dict[str, Any]] = []
    for signature, rows in _test_failure_rows_by_signature(read_merged_rows(ledger_path)).items():
        already_promoted = os.path.exists(os.path.join(kb_dir, "failure_modes", f"KB-failure-{signature}.json"))
        if already_promoted or len(_distinct_runs(rows)) < MIN_SIGNATURE_RUNS:
            continue
        item = _signature_failure_item(signature, rows)
        if not dry_run:
            item["persisted_path"] = persist_kb_fact(item, kb_dir=kb_dir)
        promoted.append(item)
    return promoted


def _executions_ledger_of(kb_dir: str) -> str:
    return os.path.join(os.path.dirname(os.path.normpath(kb_dir)), "ledger", EXECUTIONS_LEDGER_NAME)


def promote_pending_candidates(
    index_db: ActionIndexDatabase | None = None,
    kb_dir: str = DEFAULT_KB_DIR,
    wb_dir: str = DEFAULT_WB_DIR,
    min_trials: int = 2,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    db = index_db or ActionIndexDatabase(DEFAULT_PROJECT_DB_PATH)
    conn = db.get_connection()

    promoted_items: list[dict[str, Any]] = []
    seen_promoted_ids: set[str] = set()

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT signature_hash, context_fingerprint, action_origin,
                   success_count, failure_count, last_seen_ts
            FROM action_signatures
            WHERE (success_count + failure_count) >= ?;
            """,
            (min_trials,),
        )
        signatures = cur.fetchall()

        for sig in signatures:
            if _already_promoted(conn, sig, kb_dir, wb_dir):
                continue
            eval_res = evaluate_action_promotion(sig["signature_hash"], index_db=db)
            records = _fetch_sample_context_records(cur, sig["context_fingerprint"])

            chatter = is_review_chatter_context(cur, sig, eval_res.failure_ladder_state)
            failure_item = None if chatter else _promote_failure_candidate(eval_res, records, kb_dir, dry_run)
            if failure_item and failure_item["kb_id"] in seen_promoted_ids:
                failure_item = None
            if failure_item:
                seen_promoted_ids.add(failure_item["kb_id"])
                promoted_items.append(failure_item)
            else:
                for item in _promote_success_candidate(eval_res, records, kb_dir, wb_dir, dry_run):
                    item_id = str(item.get("kb_id") or item.get("wb_id"))
                    if item_id not in seen_promoted_ids:
                        seen_promoted_ids.add(item_id)
                        promoted_items.append(item)

        signature_items = promote_repeated_failure_signatures(_executions_ledger_of(kb_dir), kb_dir, dry_run)
        promoted_items.extend(item for item in signature_items if item["kb_id"] not in seen_promoted_ids)
        return promoted_items
    finally:
        conn.close()


def list_vetoes(index_db: ActionIndexDatabase | None = None) -> list[dict[str, Any]]:
    db = index_db or ActionIndexDatabase(DEFAULT_PROJECT_DB_PATH)
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT context_fingerprint, signature_hash, reproduction_count,
                   state, decay_weight, is_shadow, created_at, updated_at
            FROM active_vetoes
            ORDER BY updated_at DESC;
            """
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def lift_veto(
    index_db: ActionIndexDatabase | None = None,
    context_fingerprint: str = "",
    reason: str = "Operator override",
) -> bool:
    db = index_db or ActionIndexDatabase(DEFAULT_PROJECT_DB_PATH)
    conn = db.get_connection()
    try:
        now_iso = datetime.now(UTC).isoformat()
        with conn:
            cur = conn.execute(
                """
                UPDATE active_vetoes
                SET state = 'lifted', updated_at = ?
                WHERE context_fingerprint = ?;
                """,
                (now_iso, context_fingerprint),
            )
            return cur.rowcount > 0
    finally:
        conn.close()
