from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from sandbox_executor.ledger.query_filters import FILTER_PREDICATES, FilterPredicate, match_entry_type, parse_labels
from sandbox_executor.ledger.rows import _REPEAT_CHECKED_ID_KEYS
from sandbox_executor.plan_paths import bare_branch, plan_branch_of

_TABLE_HEADERS: tuple[str, ...] = ("Timestamp", "Type", "Category", "Action/Summary", "Target", "Outcome", "Origin")
_TIMESTAMP_PREFIX_LEN = 19
_MAX_ACTION_CELL_WIDTH = 40
_MAX_TARGET_CELL_WIDTH = 30
_TRUNCATION_SUFFIX = "..."

_STAGE_LEDGER_SPECS: dict[str, tuple[str, str, str]] = {
    "plan": ("plans.jsonl", "plan_id", "intent_branch"),
    "execute": ("executions.jsonl", "execution_id", "plan_branch"),
}


@dataclass(frozen=True, kw_only=True)
class LedgerQuery:
    entry_type: str | None = None
    category: str | None = None
    labels: list[str] | str | None = None
    target: str | None = None
    outcome: str | None = None
    origin: str | None = None
    review_severity: str | None = None
    query: str | None = None
    limit: int | None = None

    def predicates(self) -> list[tuple[FilterPredicate, Any]]:
        active: list[tuple[FilterPredicate, Any]] = []
        for field_name, predicate in FILTER_PREDICATES:
            wanted = parse_labels(self.labels) if field_name == "labels" else getattr(self, field_name)
            if wanted or predicate is match_entry_type:
                active.append((predicate, wanted))
        return active


def query_ledger(ledger_path: str, **filters: Any) -> list[dict[str, Any]]:
    spec = LedgerQuery(**filters)
    if not os.path.exists(ledger_path):
        return []

    active = spec.predicates()
    results: list[dict[str, Any]] = []
    for rec in _iter_ledger_records(ledger_path):
        if not all(predicate(rec, wanted) for predicate, wanted in active):
            continue
        results.append(rec)
        if spec.limit is not None and len(results) >= spec.limit:
            break

    return results


def _iter_ledger_records(ledger_path: str) -> Iterator[Any]:
    with open(ledger_path, encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                rec = json.loads(line_str)
            except (json.JSONDecodeError, ValueError):
                continue
            yield rec


def _truncate_cell(text: str, max_width: int) -> str:
    if len(text) <= max_width:
        return text
    return text[: max_width - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX


def format_query_results_table(records: list[dict[str, Any]]) -> str:
    if not records:
        return "No matching ledger records found."

    rows: list[list[str]] = []
    for record in records:
        timestamp = str(record.get("created_at") or "")[:_TIMESTAMP_PREFIX_LEN]
        entry_type = str(record.get("entry_type", "summary"))
        category = str(record.get("category") or "-")
        action = _truncate_cell(str(record.get("action") or record.get("summary") or "-"), _MAX_ACTION_CELL_WIDTH)
        target = _truncate_cell(str(record.get("target") or record.get("plan_branch") or "-"), _MAX_TARGET_CELL_WIDTH)
        outcome = str(record.get("outcome") or record.get("status") or "-")
        origin = str(record.get("action_origin") or "-")
        rows.append([timestamp, entry_type, category, action, target, outcome, origin])

    col_widths = [len(header) for header in _TABLE_HEADERS]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    def format_row(cells: Sequence[str]) -> str:
        padded = [cell.ljust(col_widths[i]) for i, cell in enumerate(cells)]
        return "| " + " | ".join(padded) + " |"

    separator = "|-" + "-|-".join("-" * width for width in col_widths) + "-|"
    output_lines = [format_row(_TABLE_HEADERS), separator]
    output_lines.extend(format_row(row) for row in rows)
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
    base_dir = repo_dir or "."
    stage_val = getattr(stage, "value", stage)
    spec = _STAGE_LEDGER_SPECS.get(str(stage_val).lower())
    if spec is None:
        return
    filename, id_key, parent_key = spec
    ledger_path = os.path.join(base_dir, "holon-knowledge", "ledger", filename)
    validate_ledger_file_unique_ids(ledger_path, id_key=id_key, parent_key=parent_key)
