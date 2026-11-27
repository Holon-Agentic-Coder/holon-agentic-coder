"""Stage 4 findings carried across a halted review pass: kept open, resolved first on resume, and blocking approval.

A finding leaves the review loop only through a disposition a resolver that ran supplied: ``applied``, or ``rejected``
with a stated reason. A pass that halts (a failed resolver turn, a budget stop) returns its open findings; the engine
keeps them in the checkpoint (``FlowContext.carried_review_findings``) and the next ``run_review_loop`` sends them to
the resolver before any new reviewer iteration. Until each has a disposition, consensus approval is refused.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.review.resolver import (
    DispositionRecorder,
    ResolverInput,
    ResolverOutcome,
    _invoke_resolver_pass,
)
from sandbox_executor.resolver_evidence import REJECTED
from sandbox_executor.review_report import (
    FindingDisposition,
    FindingRow,
    finding_signature,
    serialize_finding_for_resolver,
)
from sandbox_executor.review_target import ReviewTarget

logger = logging.getLogger(__name__)

_DECIDED = frozenset({"applied", REJECTED})


def _signature(row: FindingRow | FindingDisposition) -> tuple[str, str, int | None, str]:
    """The cross-iteration signature of a finding row or disposition."""
    return finding_signature(row.severity, row.path, row.line, row.summary)


def serialize_open(findings: Iterable[FindingRow], action_ids: Mapping[str, str]) -> list[dict[str, Any]]:
    """Serialise open findings for the checkpoint, each with the ACT-FIND row that introduced it when known."""
    return [
        serialize_finding_for_resolver(
            row, introduced_by_action_id=action_ids.get(row.id) or action_ids.get(str(_signature(row)))
        )
        for row in findings
    ]


def _row_from(entry: Any) -> FindingRow | None:
    """One carried finding row from its checkpoint entry, or None when the entry is malformed."""
    if not isinstance(entry, Mapping) or not str(entry.get("id") or "").strip() or not entry.get("summary"):
        return None
    line = entry.get("line")
    reviewers = entry.get("reviewers") or ()
    return FindingRow(
        id=str(entry["id"]).strip(),
        severity=str(entry.get("severity") or "nit"),
        path=str(entry.get("path") or ""),
        line=line if isinstance(line, int) and not isinstance(line, bool) and line >= 1 else None,
        reviewers=tuple(i for i in reviewers if isinstance(i, int)) if isinstance(reviewers, list) else (),
        summary=str(entry["summary"]),
        recommendation=str(entry.get("recommendation") or ""),
    )


def deserialize_carried(raw: Any) -> tuple[list[FindingRow], dict[str, str]]:
    """Return the carried ``(rows, action_ids)``; a malformed entry is skipped and logged, never treated as approved."""
    rows: list[FindingRow] = []
    action_ids: dict[str, str] = {}
    for entry in raw if isinstance(raw, list) else []:
        row = _row_from(entry)
        if row is None:
            logger.warning("Skipping a malformed carried review finding: %r", entry)
            continue
        rows.append(row)
        intro = str(entry.get("introduced_by_action_id") or "").strip()
        if intro:
            action_ids[row.id] = intro
            action_ids.setdefault(str(_signature(row)), intro)
    return rows, action_ids


def merge_findings(fresh: Sequence[FindingRow], carried: Sequence[FindingRow]) -> list[FindingRow]:
    """Carried findings first, then fresh ones, de-duplicated by signature so the resolver sees each finding once."""
    merged: dict[tuple[str, str, int | None, str], FindingRow] = {}
    for row in (*carried, *fresh):
        merged.setdefault(_signature(row), row)
    return list(merged.values())


def undisposed(carried: Sequence[FindingRow], dispositions: Iterable[FindingDisposition]) -> list[FindingRow]:
    """The carried rows without an ``applied`` or ``rejected`` disposition among ``dispositions``."""
    decided = {_signature(d) for d in dispositions if d.disposition in _DECIDED}
    return [row for row in carried if _signature(row) not in decided]


def carried_blocker(carried: Sequence[FindingRow]) -> str:
    """The consensus blocker naming carried findings that still have no disposition; empty when none remain."""
    if not carried:
        return ""
    names = ", ".join(f"{row.id} ({row.path}{'' if row.line is None else f':{row.line}'})" for row in carried)
    return f"{len(carried)} finding(s) from an earlier iteration have no disposition: {names}"


@dataclass(frozen=True)
class CarriedPass:
    """What a resolver pass on carried findings runs against and records through."""

    context: FlowContext
    target: ReviewTarget
    iteration: int
    run_resolver_fn: Callable[..., Any] | None
    recorder: DispositionRecorder


def resolve_carried_first(
    carried: Sequence[FindingRow], action_ids: Mapping[str, str], run: CarriedPass
) -> tuple[ResolverOutcome, list[FindingRow], tuple[FindingDisposition, ...]]:
    """Run one resolver pass on the carried findings before any reviewer; return ``(outcome, open_rows, pending)``.

    A failed pass writes nothing and leaves every row open. Otherwise its rejections, and its applied fixes once the
    head moved, are recorded as ACT-RES rows; every other row stays open. An applied fix without a push is also returned
    as ``pending`` for the loop to record later, as it does for its own unpushed fixes; with no commit it blocks
    approval.
    """
    run.context.log(
        f"[Review Loop] Resolving {len(carried)} carried finding(s) from an earlier iteration before reviewing."
    )
    resolver_input = ResolverInput(findings=list(carried), action_ids=dict(action_ids))
    outcome = _invoke_resolver_pass(run.context, run.target, run.iteration, resolver_input, run.run_resolver_fn)
    if outcome.error:
        run.context.log(
            f"[Review Loop] Carried-finding resolver pass failed, no review iteration starts: {outcome.error}"
        )
        return outcome, list(carried), ()
    decided = [
        d for d in outcome.dispositions if d.disposition == REJECTED or (outcome.pushed and d.disposition in _DECIDED)
    ]
    pending = () if outcome.pushed else tuple(d for d in outcome.dispositions if d.disposition == "applied")
    run.recorder.record(run.iteration, decided)
    return outcome, undisposed(carried, run.recorder.all_dispositions), pending
