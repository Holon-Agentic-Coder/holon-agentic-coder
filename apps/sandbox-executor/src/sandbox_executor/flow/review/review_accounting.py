"""Review accounting: how many full and scoped reviews, pipeline runs and reruns a flow spent, and why it churned.

The record lives in ``context.review_state["accounting"]`` (checkpointed with the context) and only this module writes
it: one integer per ``COUNTER_KEYS`` entry, ``non_defect_churn`` (one integer per ``CHURN_REASONS`` entry: a target
merge the approval was carried across, a bookkeeping-only move the approval still covers, CI noise that was rerun) and
``seen_events`` (the events already counted, so a resumed pass or a re-inspected run is never counted twice).

A missing or malformed record (a legacy checkpoint or payload, or a run with no flow context) reads as None and renders
"not recorded", never as zeros: an unknown count must not read as "no churn".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

COUNTER_KEYS = ("full_reviews", "scoped_reviews", "pipeline_runs", "pipeline_reruns", "approval_carry_overs")
CHURN_REASONS = ("target_merge", "bookkeeping", "ci_noise")
NON_DEFECT_CHURN = "non_defect_churn"
SEEN_EVENTS_LIMIT = 200
NOT_RECORDED = "not recorded"


def _accounting_state(context: Any) -> dict[str, Any]:
    """The mutable accounting record, created (every counter at zero) when absent or malformed."""
    state = context.review_state.get("accounting")
    if not isinstance(state, dict):
        state = context.review_state["accounting"] = {}
    for key in COUNTER_KEYS:
        state.setdefault(key, 0)
    churn = state.get(NON_DEFECT_CHURN)
    if not isinstance(churn, dict):
        churn = state[NON_DEFECT_CHURN] = {}
    for reason in CHURN_REASONS:
        churn.setdefault(reason, 0)
    if not isinstance(state.get("seen_events"), list):
        state["seen_events"] = []
    return state


def _validated_key(key: str, reason: str | None) -> None:
    """Raise ``ValueError`` for an unknown counter, or a churn count without a known reason."""
    if key == NON_DEFECT_CHURN:
        if reason not in CHURN_REASONS:
            raise ValueError(f"unknown non-defect churn reason: {reason!r}")
    elif key not in COUNTER_KEYS or reason is not None:
        raise ValueError(f"unknown review accounting counter: {key!r} (reason {reason!r})")


def _count(context: Any, key: str, reason: str | None = None, event: str | None = None) -> bool:
    """Count one ``key`` (or one ``non_defect_churn`` of ``reason``); False when ``event`` was already counted."""
    _validated_key(key, reason)
    state = _accounting_state(context)
    seen = state["seen_events"]
    if event is not None and event in seen:
        return False
    counters = state[NON_DEFECT_CHURN] if key == NON_DEFECT_CHURN else state
    name = reason if key == NON_DEFECT_CHURN else key
    counters[name] = _int(counters.get(name)) + 1
    if event is not None:
        seen.append(event)
        del seen[:-SEEN_EVENTS_LIMIT]
    return True


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _valid_counts(record: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, int] | None:
    """The non-negative integer ``keys`` of ``record``, or None when any is missing or not an integer."""
    values = [record.get(key) for key in keys]
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values):
        return None
    return {key: int(record[key]) for key in keys}


def _raw_record(source: Any) -> Any:
    """The accounting a context, a Stage 4 payload or a loop result carries, unvalidated."""
    if isinstance(source, Mapping):
        return source.get("review_accounting", source.get("accounting"))
    review_state = getattr(source, "review_state", None)
    return review_state.get("accounting") if isinstance(review_state, Mapping) else None


def _accounting_of(source: Any) -> dict[str, Any] | None:
    """A copy of the counters (no ``seen_events``) a context or a mapping carries; None when absent or malformed."""
    record = _raw_record(source)
    if not isinstance(record, Mapping) or not isinstance(record.get(NON_DEFECT_CHURN), Mapping):
        return None
    counters = _valid_counts(record, COUNTER_KEYS)
    churn = _valid_counts(record[NON_DEFECT_CHURN], CHURN_REASONS)
    if counters is None or churn is None:
        return None
    return {**counters, NON_DEFECT_CHURN: churn}


def _render_accounting_lines(accounting: Mapping[str, Any] | None) -> list[str]:
    """Flow summary lines for ``accounting`` (an :func:`_accounting_of` result); "not recorded" for None."""
    if accounting is None:
        return [f"  Review churn   : {NOT_RECORDED}"]
    churn = accounting[NON_DEFECT_CHURN]
    return [
        f"  Reviews        : {accounting['full_reviews']} full, {accounting['scoped_reviews']} scoped; "
        f"carry-overs: {accounting['approval_carry_overs']}",
        f"  Pipelines      : {accounting['pipeline_runs']} run(s), {accounting['pipeline_reruns']} rerun(s)",
        f"  Non-defect churn: target merge {churn['target_merge']}, bookkeeping {churn['bookkeeping']}, "
        f"CI noise {churn['ci_noise']}",
    ]


def _count_carry_over(context: Any, approved_head: str, merged_head: str) -> None:
    """Count one approval carried across a target merge, once however many stages record it."""
    event = f"carry:{approved_head}:{merged_head}"
    if _count(context, "approval_carry_overs", event=event):
        _count(context, NON_DEFECT_CHURN, "target_merge")
