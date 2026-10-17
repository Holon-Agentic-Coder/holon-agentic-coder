"""Knowledge Base (KB) and Wisdom Base (WB) persistence and promotion engine (Bean 0052 Goal 5)."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.knowledge.index import DEFAULT_PROJECT_DB_PATH, ActionIndexDatabase
from sandbox_executor.knowledge.promotion import evaluate_action_promotion

logger = logging.getLogger(__name__)

DEFAULT_KB_DIR = "holon-knowledge/kb"
DEFAULT_WB_DIR = "holon-knowledge/wisdom"


def _update_directory_index(subfolder_path: str, entry_meta: dict[str, Any]) -> None:
    """Update index.json in a KB or WB subfolder with the latest entry metadata."""
    index_file = os.path.join(subfolder_path, "index.json")
    index_data: dict[str, Any] = {
        "index_version": "1.0",
        "updated_ts": datetime.now(UTC).isoformat(),
        "entries": [],
    }

    if os.path.exists(index_file):
        try:
            with open(index_file, encoding="utf-8") as f:
                index_data = json.load(f)
        except Exception:
            pass

    entries: list[dict[str, Any]] = index_data.get("entries", [])
    entry_id = entry_meta.get("kb_id") or entry_meta.get("wb_id")

    # Replace existing or append
    updated = False
    for i, e in enumerate(entries):
        e_id = e.get("kb_id") or e.get("wb_id")
        if e_id == entry_id:
            entries[i] = entry_meta
            updated = True
            break
    if not updated:
        entries.append(entry_meta)

    index_data["entries"] = entries
    index_data["updated_ts"] = datetime.now(UTC).isoformat()

    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index_data, f, indent=2)


def persist_kb_fact(fact_data: dict[str, Any], kb_dir: str = DEFAULT_KB_DIR) -> str:
    """Validate and persist descriptive fact or pattern to holon-knowledge/kb/."""
    data = dict(fact_data)
    kb_type = str(data.get("kb_type") or "pattern").lower()
    kb_id = str(data.get("kb_id") or f"KB-{kb_type}-{data.get('context_fingerprint', 'item')[:8]}")
    now_iso = datetime.now(UTC).isoformat()

    subfolder = "patterns"
    if "failure" in kb_type:
        subfolder = "failure_modes"
    elif "tactic" in kb_type:
        subfolder = "tactics"

    target_dir = os.path.join(kb_dir, subfolder)
    os.makedirs(target_dir, exist_ok=True)

    envelope = {
        "kb_id": kb_id,
        "kb_type": kb_type,
        "version": str(data.get("version") or "1.0"),
        "created_ts": str(data.get("created_ts") or now_iso),
        "updated_ts": now_iso,
        "created_by": str(data.get("created_by") or "holon-promotion-engine"),
        "status": str(data.get("status") or "active"),
        "evidence": data.get("evidence", {}),
        "tags": data.get("tags", []),
        "human_approved": bool(data.get("human_approved", False)),
        "payload": data.get("payload", {}),
    }

    out_file = os.path.join(target_dir, f"{kb_id}.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(envelope, f, indent=2)

    _update_directory_index(
        target_dir,
        {
            "kb_id": kb_id,
            "file": f"{kb_id}.json",
            "kb_type": kb_type,
            "status": envelope["status"],
            "created_ts": envelope["created_ts"],
            "tags": envelope["tags"],
        },
    )

    return out_file


def persist_wb_rule(rule_data: dict[str, Any], wb_dir: str = DEFAULT_WB_DIR) -> str:
    """Validate and persist prescriptive rule or invariant to holon-knowledge/wisdom/."""
    data = dict(rule_data)
    wb_type = str(data.get("wb_type") or "heuristic").lower()
    wb_id = str(data.get("wb_id") or f"WB-{wb_type}-{data.get('context_fingerprint', 'rule')[:8]}")
    now_iso = datetime.now(UTC).isoformat()

    subfolder = "heuristics"
    if "invariant" in wb_type:
        subfolder = "invariants"

    target_dir = os.path.join(wb_dir, subfolder)
    os.makedirs(target_dir, exist_ok=True)

    envelope = {
        "wb_id": wb_id,
        "wb_type": wb_type,
        "version": str(data.get("version") or "1.0"),
        "created_ts": str(data.get("created_ts") or now_iso),
        "scope": "global",
        "status": str(data.get("status") or "active"),
        "ascension_evidence": data.get("ascension_evidence", {}),
        "human_approved": bool(data.get("human_approved", False)),
        "payload": data.get("payload", {}),
    }

    out_file = os.path.join(target_dir, f"{wb_id}.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(envelope, f, indent=2)

    _update_directory_index(
        target_dir,
        {
            "wb_id": wb_id,
            "file": f"{wb_id}.json",
            "wb_type": wb_type,
            "status": envelope["status"],
            "created_ts": envelope["created_ts"],
        },
    )

    return out_file


def promote_pending_candidates(
    index_db: ActionIndexDatabase | None = None,
    kb_dir: str = DEFAULT_KB_DIR,
    wb_dir: str = DEFAULT_WB_DIR,
    min_trials: int = 2,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Scan action signatures and promote qualified patterns to KB and WB."""
    db = index_db or ActionIndexDatabase(DEFAULT_PROJECT_DB_PATH)
    conn = db.get_connection()

    promoted_items: list[dict[str, Any]] = []

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
            sig_hash = sig["signature_hash"]
            eval_res = evaluate_action_promotion(sig_hash, index_db=db)

            # Look up a sample action record for context
            cur.execute(
                """
                SELECT action, target, category, error_context, record_id
                FROM action_records
                WHERE context_fingerprint = ?
                ORDER BY created_at DESC LIMIT 5;
                """,
                (sig["context_fingerprint"],),
            )
            records = cur.fetchall()
            sample_act = records[0]["action"] if records else "unknown action"
            sample_tgt = records[0]["target"] if records else "unknown target"
            source_ids = [r["record_id"] for r in records]

            # 1. Promote failure modes
            if eval_res.failure_ladder_state in ("repeated", "proven-fail"):
                item = {
                    "kb_id": f"KB-failure-{eval_res.context_fingerprint[:8]}",
                    "kb_type": "failure_mode",
                    "context_fingerprint": eval_res.context_fingerprint,
                    "evidence": {
                        "ledger_refs": source_ids,
                        "success_count": eval_res.success_count,
                        "failure_count": eval_res.failure_count,
                        "ladder_state": eval_res.failure_ladder_state,
                    },
                    "tags": [records[0]["category"] if records else "failure", "veto"],
                    "payload": {
                        "name": f"Failure mode in {sample_tgt}",
                        "description": (
                            f"Reproduced failure across {eval_res.failure_count} executions with identical context."
                        ),
                        "action": sample_act,
                        "symptoms": [records[0]["error_context"]] if records and records[0]["error_context"] else [],
                    },
                }
                if not dry_run:
                    path = persist_kb_fact(item, kb_dir=kb_dir)
                    item["persisted_path"] = path
                promoted_items.append(item)

            # 2. Promote success patterns
            elif eval_res.success_ladder_state in ("likely-success", "proven-success"):
                item = {
                    "kb_id": f"KB-pattern-{eval_res.context_fingerprint[:8]}",
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
                        "description": eval_res.advisory_text or f"Succeeded in {eval_res.success_count} executions.",
                        "action": sample_act,
                    },
                }
                if not dry_run:
                    path = persist_kb_fact(item, kb_dir=kb_dir)
                    item["persisted_path"] = path
                promoted_items.append(item)

                # Proven-success also ascends to Wisdom Base heuristic
                if eval_res.success_ladder_state == "proven-success":
                    wb_item = {
                        "wb_id": f"WB-heuristic-{eval_res.context_fingerprint[:8]}",
                        "wb_type": "heuristic",
                        "context_fingerprint": eval_res.context_fingerprint,
                        "ascension_evidence": {
                            "project_count": 1,
                            "total_executions": eval_res.total_trials,
                            "wilson_lower": eval_res.wilson_lower,
                            "provenance": [{"kb_id": item["kb_id"]}],
                        },
                        "payload": {
                            "name": f"Universal heuristic: {sample_tgt}",
                            "advisory": eval_res.advisory_text,
                        },
                    }
                    if not dry_run:
                        wb_path = persist_wb_rule(wb_item, wb_dir=wb_dir)
                        wb_item["persisted_path"] = wb_path
                    promoted_items.append(wb_item)

        return promoted_items
    finally:
        conn.close()


def list_vetoes(index_db: ActionIndexDatabase | None = None) -> list[dict[str, Any]]:
    """List active, shadow, and lifted veto records in the index."""
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
    """Manually lift an active or shadowed veto for a given context fingerprint."""
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
