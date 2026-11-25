from __future__ import annotations

from collections.abc import Callable
from typing import Any

FilterPredicate = Callable[[dict[str, Any], Any], bool]

_DEFAULT_ENTRY_TYPE = "summary"
_ALL_ENTRY_TYPES = "all"
_FAILURE_OUTCOMES = ("failure", "failed")


def parse_labels(value: Any) -> list[str]:
    if isinstance(value, str):
        return [lbl.strip().lower() for lbl in value.split(",") if lbl.strip()]
    if isinstance(value, list):
        return [str(lbl).strip().lower() for lbl in value if str(lbl).strip()]
    return []


def match_entry_type(rec: dict[str, Any], wanted: str | None) -> bool:
    rec_entry_type = rec.get("entry_type", _DEFAULT_ENTRY_TYPE)
    return not wanted or wanted.lower() == _ALL_ENTRY_TYPES or rec_entry_type.lower() == wanted.lower()


def match_category(rec: dict[str, Any], wanted: str) -> bool:
    return rec.get("category", "").lower() == wanted.lower()


def match_labels(rec: dict[str, Any], wanted: list[str]) -> bool:
    rec_labels = parse_labels(rec.get("labels"))
    return any(label in rec_labels for label in wanted)


def match_outcome(rec: dict[str, Any], wanted: str) -> bool:
    rec_outcome = str(rec.get("outcome") or rec.get("status") or "").lower()
    req_outcome = wanted.lower()
    if req_outcome in _FAILURE_OUTCOMES:
        return rec_outcome in _FAILURE_OUTCOMES
    return rec_outcome == req_outcome


def match_origin(rec: dict[str, Any], wanted: str) -> bool:
    return rec.get("action_origin", "").lower() == wanted.lower()


def match_review_severity(rec: dict[str, Any], wanted: str) -> bool:
    return rec.get("review_severity", "").lower() == wanted.lower()


def match_target(rec: dict[str, Any], wanted: str) -> bool:
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
    q = wanted.lower()
    return any(q in str(rec.get(key) or "").lower() for key in _SEARCHABLE_FIELDS)


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
