"""One predicate per ``holon ledger query`` filter; ``LedgerQuery`` composes the active ones.

Each predicate takes ``(record, wanted)`` and returns whether the record passes that filter. ``FILTER_PREDICATES``
lists them in the order the filters are evaluated, which is part of the contract: a record failing an earlier filter
never reaches a later one, so which error a malformed record raises (e.g. a null ``review_severity``) is unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

FilterPredicate = Callable[[dict[str, Any], Any], bool]

_FAILURE_OUTCOMES = ("failure", "failed")


def parse_labels(value: Any) -> list[str]:
    """Normalise a comma-separated string or a list of labels to stripped, lower-case, non-empty labels."""
    if isinstance(value, str):
        return [lbl.strip().lower() for lbl in value.split(",") if lbl.strip()]
    if isinstance(value, list):
        return [str(lbl).strip().lower() for lbl in value if str(lbl).strip()]
    return []


def match_entry_type(rec: dict[str, Any], wanted: str | None) -> bool:
    """Match ``entry_type`` (default ``summary``) case-insensitively; an empty or ``all`` filter matches everything.

    The record is always read, even with no filter, so a non-object ledger line raises as it always has.
    """
    rec_entry_type = rec.get("entry_type", "summary")
    return not wanted or wanted.lower() == "all" or rec_entry_type.lower() == wanted.lower()


def match_category(rec: dict[str, Any], wanted: str) -> bool:
    """Match ``category`` case-insensitively."""
    return rec.get("category", "").lower() == wanted.lower()


def match_labels(rec: dict[str, Any], wanted: list[str]) -> bool:
    """Match when any of the pre-parsed ``wanted`` labels is among the record's labels."""
    rec_labels = parse_labels(rec.get("labels"))
    return any(label in rec_labels for label in wanted)


def match_outcome(rec: dict[str, Any], wanted: str) -> bool:
    """Match ``outcome`` (or ``status``); ``failure`` and ``failed`` are synonyms."""
    rec_outcome = str(rec.get("outcome") or rec.get("status") or "").lower()
    req_outcome = wanted.lower()
    if req_outcome in _FAILURE_OUTCOMES:
        return rec_outcome in _FAILURE_OUTCOMES
    return rec_outcome == req_outcome


def match_origin(rec: dict[str, Any], wanted: str) -> bool:
    """Match ``action_origin`` case-insensitively."""
    return rec.get("action_origin", "").lower() == wanted.lower()


def match_review_severity(rec: dict[str, Any], wanted: str) -> bool:
    """Match ``review_severity`` case-insensitively."""
    return rec.get("review_severity", "").lower() == wanted.lower()


def match_target(rec: dict[str, Any], wanted: str) -> bool:
    """Match a case-insensitive substring of ``target`` (or ``plan_branch``)."""
    rec_target = str(rec.get("target") or rec.get("plan_branch") or "")
    return wanted.lower() in rec_target.lower()


_SEARCHABLE_FIELDS = (
    "action",
    "summary",
    "target",
    "error_context",
    "mistake_category",
    "execution_id",
    "action_id",
    "event_type",
    "plan_branch",
)


def match_text(rec: dict[str, Any], wanted: str) -> bool:
    """Match a case-insensitive substring of any searchable field."""
    q = wanted.lower()
    return any(q in str(rec.get(key) or "").lower() for key in _SEARCHABLE_FIELDS)


# (LedgerQuery field, predicate) in evaluation order.
FILTER_PREDICATES: tuple[tuple[str, FilterPredicate], ...] = (
    ("entry_type", match_entry_type),
    ("category", match_category),
    ("labels", match_labels),
    ("outcome", match_outcome),
    ("origin", match_origin),
    ("review_severity", match_review_severity),
    ("target", match_target),
    ("query", match_text),
)
