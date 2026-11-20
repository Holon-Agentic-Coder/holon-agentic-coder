"""Ledger querying, filtering, table formatting, and ID uniqueness validation."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from typing import Any

from sandbox_executor.ledger.rows import _REPEAT_CHECKED_ID_KEYS
from sandbox_executor.plan_paths import bare_branch, plan_branch_of


def _match_category_and_labels(rec: dict[str, Any], category: str | None, target_labels: list[str]) -> bool:
    if category and rec.get("category", "").lower() != category.lower():
        return False
    if target_labels:
        rec_labels = rec.get("labels")
        if isinstance(rec_labels, str):
            clean_rec_labels = [lbl.strip().lower() for lbl in rec_labels.split(",") if lbl.strip()]
        elif isinstance(rec_labels, list):
            clean_rec_labels = [str(lbl).strip().lower() for lbl in rec_labels if str(lbl).strip()]
        else:
            return False
        if not any(tl in clean_rec_labels for tl in target_labels):
            return False
    return True


def _match_outcome_and_origin(
    rec: dict[str, Any],
    outcome: str | None,
    origin: str | None,
    review_severity: str | None,
) -> bool:
    if outcome:
        rec_outcome = str(rec.get("outcome") or rec.get("status") or "").lower()
        req_outcome = outcome.lower()
        if req_outcome in ("failure", "failed"):
            if rec_outcome not in ("failure", "failed"):
                return False
        elif rec_outcome != req_outcome:
            return False
    if origin and rec.get("action_origin", "").lower() != origin.lower():
        return False
    return not (review_severity and rec.get("review_severity", "").lower() != review_severity.lower())


def _match_query_text(rec: dict[str, Any], query: str | None, target: str | None) -> bool:
    if target:
        rec_target = str(rec.get("target") or rec.get("plan_branch") or "")
        if target.lower() not in rec_target.lower():
            return False
    if query:
        q = query.lower()
        searchable = [
            str(rec.get("action") or ""),
            str(rec.get("summary") or ""),
            str(rec.get("target") or ""),
            str(rec.get("error_context") or ""),
            str(rec.get("mistake_category") or ""),
            str(rec.get("execution_id") or ""),
            str(rec.get("action_id") or ""),
            str(rec.get("event_type") or ""),
            str(rec.get("plan_branch") or ""),
        ]
        if not any(q in item.lower() for item in searchable):
            return False
    return True


def _parse_target_labels(labels: list[str] | str | None) -> list[str]:
    if not labels:
        return []
    if isinstance(labels, str):
        return [lbl.strip().lower() for lbl in labels.split(",") if lbl.strip()]
    if isinstance(labels, list):
        return [str(lbl).strip().lower() for lbl in labels if str(lbl).strip()]
    return []


def query_ledger(
    ledger_path: str,
    *,
    entry_type: str | None = None,
    category: str | None = None,
    labels: list[str] | str | None = None,
    **filters: Any,
) -> list[dict[str, Any]]:
    """Stream and filter execution ledger records."""
    if not os.path.exists(ledger_path):
        return []

    target_labels = _parse_target_labels(labels)
    target = filters.get("target")
    outcome = filters.get("outcome")
    origin = filters.get("origin")
    review_severity = filters.get("review_severity")
    query = filters.get("query")
    limit = filters.get("limit")

    results: list[dict[str, Any]] = []

    with open(ledger_path, encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue

            try:
                rec = json.loads(line_str)
            except (json.JSONDecodeError, ValueError):
                continue

            rec_entry_type = rec.get("entry_type", "summary")
            if entry_type and entry_type.lower() != "all" and rec_entry_type.lower() != entry_type.lower():
                continue

            if not _match_category_and_labels(rec, category, target_labels):
                continue

            if not _match_outcome_and_origin(rec, outcome, origin, review_severity):
                continue

            if not _match_query_text(rec, query, target):
                continue

            results.append(rec)
            if limit is not None and len(results) >= limit:
                break

    return results


def format_query_results_table(records: list[dict[str, Any]]) -> str:
    """Format matching records into a clean human-readable table."""
    if not records:
        return "No matching ledger records found."

    headers = ["Timestamp", "Type", "Category", "Action/Summary", "Target", "Outcome", "Origin"]

    rows: list[list[str]] = []
    for r in records:
        ts = str(r.get("created_at") or "")[:19]
        etype = str(r.get("entry_type", "summary"))
        cat = str(r.get("category") or "-")
        act = str(r.get("action") or r.get("summary") or "-")
        tgt = str(r.get("target") or r.get("plan_branch") or "-")
        outcome = str(r.get("outcome") or r.get("status") or "-")
        origin = str(r.get("action_origin") or "-")

        if len(act) > 40:
            act = act[:37] + "..."
        if len(tgt) > 30:
            tgt = tgt[:27] + "..."

        rows.append([ts, etype, cat, act, tgt, outcome, origin])

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    def format_row(cells: list[str]) -> str:
        padded = [cell.ljust(col_widths[i]) for i, cell in enumerate(cells)]
        return "| " + " | ".join(padded) + " |"

    separator = "|-" + "-|-".join(["-" * w for w in col_widths]) + "-|"

    output_lines = [format_row(headers), separator]
    for row in rows:
        output_lines.append(format_row(row))

    return "\n".join(output_lines)


def _extract_parent_ref(record: dict[str, Any], parent_key: str) -> str | None:
    parent_raw = record.get(parent_key)
    if not isinstance(parent_raw, str) or not parent_raw.strip():
        fallback_key = {"plan_branch": "plan_id", "intent_branch": "intent_id"}.get(parent_key, "intent_branch")
        parent_raw = record.get(fallback_key)
    if not isinstance(parent_raw, str) or not parent_raw.strip():
        return None
    if parent_key == "plan_branch":
        return plan_branch_of(parent_raw) or bare_branch(parent_raw) or None
    return bare_branch(parent_raw) or None


def _ledger_revision(record: dict[str, Any]) -> int:
    try:
        return int(record.get("ledger_revision") or 1)
    except (TypeError, ValueError):
        return 1


def _describe_id_owner(parent_ref: str, record: dict[str, Any]) -> str:
    agent = record.get("agent") or record.get("agent_name") or "unknown-agent"
    created_at = record.get("created_at") or record.get("timestamp") or "unknown-time"
    return f"'{parent_ref}' (agent {agent}, created_at {created_at})"


def validate_ledger_unique_ids(
    records: Iterable[dict[str, Any]],
    id_key: str = "plan_id",
    parent_key: str = "intent_branch",
) -> None:
    """Validate that no plan is recorded twice for one intent at one ledger_revision."""
    if id_key not in _REPEAT_CHECKED_ID_KEYS:
        return
    seen: dict[tuple[str, str, int], dict[str, Any]] = {}
    for record in records:
        id_val, parent_ref = record.get(id_key), _extract_parent_ref(record, parent_key)
        if not isinstance(id_val, str) or not id_val.strip() or not parent_ref:
            continue
        key = (parent_ref, id_val, _ledger_revision(record))
        if key in seen:
            raise ValueError(
                f"Duplicate {id_key} '{id_val}' recorded twice for the same intent at ledger revision {key[2]}: "
                f"{_describe_id_owner(parent_ref, seen[key])} vs {_describe_id_owner(parent_ref, record)}"
            )
        seen[key] = record


def validate_ledger_file_unique_ids(
    ledger_path: str,
    id_key: str = "plan_id",
    parent_key: str = "intent_branch",
) -> None:
    """Validate uniqueness of identifiers in a jsonl ledger file if the file exists."""
    if not os.path.exists(ledger_path):
        return
    records: list[dict[str, Any]] = []
    with open(ledger_path, encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                records.append(json.loads(line_str))
            except json.JSONDecodeError:
                continue
    validate_ledger_unique_ids(records, id_key=id_key, parent_key=parent_key)


def validate_stage_ledger(repo_dir: str | None, stage: Any) -> None:
    """Validate stage ledger uniqueness for a completed stage (e.g., plan or execute)."""
    base_dir = repo_dir or "."
    stage_val = getattr(stage, "value", stage)
    stage_name = str(stage_val).lower()
    if stage_name == "plan":
        ledger_path = os.path.join(base_dir, "holon-knowledge", "ledger", "plans.jsonl")
        validate_ledger_file_unique_ids(ledger_path, id_key="plan_id", parent_key="intent_branch")
    elif stage_name == "execute":
        ledger_path = os.path.join(base_dir, "holon-knowledge", "ledger", "executions.jsonl")
        validate_ledger_file_unique_ids(ledger_path, id_key="execution_id", parent_key="plan_branch")
