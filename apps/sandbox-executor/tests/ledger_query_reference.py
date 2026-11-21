"""Frozen copy of ``ledger/query.py::query_ledger`` at commit ``6ab13d19``, kept only as a parity oracle.

``tests/test_ledger_query_parity.py`` runs this copy and the live ``query_ledger`` over the repository's real,
append-only ledgers and asserts identical results, so the check stays valid as the ledgers grow. Do not edit this
module to follow later changes to ``query_ledger`` and never import it from ``src``: its whole value is that it is the
behaviour before the predicate refactor. Only the public names differ from the original
(``reference_query_ledger``, ``ReferenceLedgerQuery``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


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


@dataclass(frozen=True, kw_only=True)
class ReferenceLedgerQuery:
    """The filters ``query_ledger`` applies; constructing one with an unknown or misspelt filter raises TypeError.

    Attributes:
        entry_type: Filter by entry type ('action', 'summary', or 'all'/None).
        category: Filter by category (e.g. 'code', 'test', 'doc').
        labels: Filter by labels (matches if any label exists in record).
        target: Substring match on target or plan_branch.
        outcome: Match on outcome or status.
        origin: Match on action_origin.
        review_severity: Match on review_severity.
        query: Case-insensitive search across summary/action/target/error fields.
        limit: Maximum number of matching records to return.
    """

    entry_type: str | None = None
    category: str | None = None
    labels: list[str] | str | None = None
    target: str | None = None
    outcome: str | None = None
    origin: str | None = None
    review_severity: str | None = None
    query: str | None = None
    limit: int | None = None


def reference_query_ledger(ledger_path: str, **filters: Any) -> list[dict[str, Any]]:
    """Stream and filter execution ledger records.

    Args:
        ledger_path: Path to the execution ledger file (e.g. executions.jsonl).
        **filters: Keyword filters, the fields of ``LedgerQuery``.

    Returns:
        List of matching record dictionaries.

    Raises:
        TypeError: When a filter is not a ``LedgerQuery`` field, so a misspelt filter never silently matches all.
    """
    spec = ReferenceLedgerQuery(**filters)
    if not os.path.exists(ledger_path):
        return []

    target_labels = _parse_target_labels(spec.labels)
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
            if (
                spec.entry_type
                and spec.entry_type.lower() != "all"
                and rec_entry_type.lower() != spec.entry_type.lower()
            ):
                continue

            if not _match_category_and_labels(rec, spec.category, target_labels):
                continue

            if not _match_outcome_and_origin(rec, spec.outcome, spec.origin, spec.review_severity):
                continue

            if not _match_query_text(rec, spec.query, spec.target):
                continue

            results.append(rec)
            if spec.limit is not None and len(results) >= spec.limit:
                break

    return results
