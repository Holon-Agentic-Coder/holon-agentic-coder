"""Review accounting: how many full and scoped reviews, pipeline runs and reruns a flow spent, and why it churned.

The record lives in ``context.review_state["accounting"]`` (checkpointed with the flow context) and only this module
writes it: one integer per ``COUNTER_KEYS`` entry, ``non_defect_churn`` (one integer per ``CHURN_REASONS`` entry: a
target merge the approval was carried across, a bookkeeping-only move the approval still covers, CI noise that was
rerun), ``seen_events`` (the events already counted, so a resumed pass or a re-inspected run is never counted twice) and
``generation`` (bumped by every Stage 4 restart, so a pass after a restart is a new event even at the same head and
label). The module imports nothing from the flow package, so a reader outside it (the calibration report, the
``holon calibrate`` CLI) never loads the flow.

A missing or malformed record (a legacy checkpoint or payload, or a run with no flow context) reads as None and renders
"not recorded", never as zeros: an unknown count must not read as "no churn". A record first created after review passes
already ran (a flow resumed from a checkpoint that predates the record) is marked ``partial`` and rendered as such.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

COUNTER_KEYS = ("full_reviews", "scoped_reviews", "pipeline_runs", "pipeline_reruns", "approval_carry_overs")
CHURN_REASONS = ("target_merge", "bookkeeping", "ci_noise")
NON_DEFECT_CHURN = "non_defect_churn"
NOT_RECORDED = "not recorded"
PARTIAL = "partial"
PARTIAL_NOTE = "partial: counting began after a resume"
SEEN_EVENTS_LIMIT = 200
GENERATION = "generation"


def int_or_zero(value: Any) -> int:
    """``value`` when it is an integer (not a bool), else 0."""
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


def accounting_of(source: Any) -> dict[str, Any] | None:
    """A copy of the counters and ``partial`` flag a context or a mapping carries; None when absent or malformed."""
    record = _raw_record(source)
    if not isinstance(record, Mapping) or not isinstance(record.get(NON_DEFECT_CHURN), Mapping):
        return None
    counters = _valid_counts(record, COUNTER_KEYS)
    churn = _valid_counts(record[NON_DEFECT_CHURN], CHURN_REASONS)
    if counters is None or churn is None:
        return None
    return {**counters, NON_DEFECT_CHURN: churn, PARTIAL: record.get(PARTIAL) is True}


def render_accounting_lines(accounting: Mapping[str, Any] | None) -> list[str]:
    """Flow summary lines for ``accounting`` (an :func:`accounting_of` result); "not recorded" for None."""
    if accounting is None:
        return [f"  Review churn   : {NOT_RECORDED}"]
    churn = accounting[NON_DEFECT_CHURN]
    partial = [f"  Review churn   : {PARTIAL_NOTE}"] if accounting.get(PARTIAL) else []
    return [
        *partial,
        f"  Reviews        : {accounting['full_reviews']} full, {accounting['scoped_reviews']} scoped; "
        f"carry-overs: {accounting['approval_carry_overs']}",
        f"  Pipelines      : {accounting['pipeline_runs']} run(s), {accounting['pipeline_reruns']} rerun(s)",
        f"  Non-defect churn: target merge {churn['target_merge']}, bookkeeping {churn['bookkeeping']}, "
        f"CI noise {churn['ci_noise']}",
    ]


def _accounting_state(context: Any) -> dict[str, Any]:
    """The mutable accounting record, created (every counter at zero) when absent or malformed.

    A record created once review passes already ran missed their counts, so it is marked ``partial``.
    """
    state = context.review_state.get("accounting")
    if not isinstance(state, dict):
        state = context.review_state["accounting"] = {}
        if int_or_zero(getattr(context, "review_iterations_done", 0)) > 0:
            state[PARTIAL] = True
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


def count_accounting(context: Any, key: str, reason: str | None = None, event: str | None = None) -> bool:
    """Count one ``key`` (or one ``non_defect_churn`` of ``reason``); False when ``event`` was already counted."""
    _validated_key(key, reason)
    state = _accounting_state(context)
    seen = state["seen_events"]
    if event is not None and event in seen:
        return False
    counters = state[NON_DEFECT_CHURN] if key == NON_DEFECT_CHURN else state
    name = reason if key == NON_DEFECT_CHURN else key
    counters[name] = int_or_zero(counters.get(name)) + 1
    if event is not None:
        seen.append(event)
        del seen[:-SEEN_EVENTS_LIMIT]
    return True


def count_carry_over(context: Any, approved_head: str, merged_head: str) -> None:
    """Count one approval carried across a target merge, once however many stages record it."""
    event = f"carry:{approved_head}:{merged_head}"
    if count_accounting(context, "approval_carry_overs", event=event):
        count_accounting(context, NON_DEFECT_CHURN, "target_merge")


def review_generation(context: Any) -> int:
    """How many times Stage 4 restarted its iteration count; part of every review-pass event key."""
    return int_or_zero(_accounting_state(context).get(GENERATION))


def restart_review_passes(context: Any) -> None:
    """Restart Stage 4's iteration count and clear its carried findings, starting a new review-pass generation.

    The record is touched before the counter resets, so a legacy checkpoint is still marked ``partial``.
    """
    state = _accounting_state(context)
    state[GENERATION] = int_or_zero(state.get(GENERATION)) + 1
    context.review_iterations_done, context.carried_review_findings = 0, []
