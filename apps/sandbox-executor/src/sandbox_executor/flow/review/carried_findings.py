from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.review import delta_review
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
_BLOCKED_ON_KEY = "blocked_on"
_CLEAN_TREE_REASON = "pre-iteration carried-finding resolver produced no code diff (nothing to commit)"


def _signature(row: FindingRow | FindingDisposition) -> tuple[str, str, int | None, str]:
    return finding_signature(row.severity, row.path, row.line, row.summary)


def serialize_open(findings: Iterable[FindingRow], action_ids: Mapping[str, str]) -> list[dict[str, Any]]:
    return [
        serialize_finding_for_resolver(
            row, introduced_by_action_id=action_ids.get(row.id) or action_ids.get(str(_signature(row)))
        )
        for row in findings
    ]


def _row_from(entry: Any) -> FindingRow | None:
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
    merged: dict[tuple[str, str, int | None, str], FindingRow] = {}
    for row in (*carried, *fresh):
        merged.setdefault(_signature(row), row)
    return list(merged.values())


def undisposed(carried: Sequence[FindingRow], dispositions: Iterable[FindingDisposition]) -> list[FindingRow]:
    decided = {_signature(d) for d in dispositions if d.disposition in _DECIDED}
    return [row for row in carried if _signature(row) not in decided]


def carried_blocker(carried: Sequence[FindingRow]) -> str:
    if not carried:
        return ""
    names = ", ".join(f"{row.id} ({row.path}{'' if row.line is None else f':{row.line}'})" for row in carried)
    return f"{len(carried)} finding(s) from an earlier iteration have no disposition: {names}"


@dataclass(frozen=True)
class CarriedPass:
    context: FlowContext
    target: ReviewTarget
    iteration: int
    run_resolver_fn: Callable[..., Any] | None
    recorder: DispositionRecorder


@dataclass(frozen=True)
class _CarriedPrePassDeps:
    probe_ci: Callable[[], Mapping[str, Any]] | None = None
    sync_target: Callable[[ReviewTarget], tuple[ReviewTarget | None, str | None]] | None = None
    record_rejected: Callable[[int, ResolverOutcome], None] | None = None


@dataclass(frozen=True)
class _CarriedPrePassResult:
    outcome: ResolverOutcome | None
    carried_rows: list[FindingRow]
    pending_dispositions: tuple[FindingDisposition, ...]
    head_sha: str
    head_changed: bool
    resolver_error: str | None
    blocked_on: dict[str, Any] | None


def _is_clean_tree_resolver_error(error: str | None) -> bool:
    return bool(error and "nothing to commit" in error.lower())


def _is_sole_ci_finding(carried: Sequence[FindingRow]) -> bool:
    return len(carried) == 1 and carried[0].id.startswith("FIND-CI-")


def _ci_status_is_green(status: Mapping[str, Any] | None) -> bool:
    if not isinstance(status, Mapping):
        return False
    total = status.get("total", 0)
    return (
        status.get("source") == "github_cli"
        and status.get("measured") is not False
        and isinstance(total, int)
        and not isinstance(total, bool)
        and total > 0
        and status.get("all_passed") is True
        and status.get("failed", 0) == 0
        and status.get("pending", 0) == 0
    )


def _active_blocked_on(context: FlowContext, head_sha: str) -> dict[str, Any] | None:
    record = context.review_state.get(_BLOCKED_ON_KEY)
    if not isinstance(record, Mapping):
        return None
    if record.get("head_sha") == head_sha:
        return dict(record)
    context.review_state.pop(_BLOCKED_ON_KEY, None)
    return None


def _clear_blocked_on(context: FlowContext) -> None:
    context.review_state.pop(_BLOCKED_ON_KEY, None)


def _set_blocked_on(context: FlowContext, head_sha: str, carried: Sequence[FindingRow], reason: str) -> dict[str, Any]:
    record = {
        "head_sha": head_sha,
        "finding_ids": [row.id for row in carried],
        "reason": reason,
    }
    context.review_state[_BLOCKED_ON_KEY] = record
    return dict(record)


def _clear_stale_ci_finding(
    run: CarriedPass, carried: Sequence[FindingRow], deps: _CarriedPrePassDeps
) -> tuple[bool, str, bool]:
    head_sha = run.target.head_sha
    if deps.probe_ci is None or not _is_sole_ci_finding(carried):
        return False, head_sha, False
    if _ci_status_is_green(deps.probe_ci()):
        return True, head_sha, False
    if deps.sync_target is None:
        return False, head_sha, False
    synced, sync_err = deps.sync_target(run.target)
    if sync_err is not None or synced is None or not synced.head_sha or synced.head_sha == head_sha:
        return False, head_sha, False
    return _ci_status_is_green(deps.probe_ci()), synced.head_sha, True


def _handle_clean_tree_outcome(
    carried: Sequence[FindingRow], run: CarriedPass, outcome: ResolverOutcome, deps: _CarriedPrePassDeps
) -> _CarriedPrePassResult:
    cleared, updated_head_sha, head_changed = _clear_stale_ci_finding(run, carried, deps)
    if cleared:
        _clear_blocked_on(run.context)
        run.context.log(
            f"[Review Loop] Cleared stale CI carried finding {carried[0].id} after clean-tree resolver pass "
            f"(CI is green on {updated_head_sha[:8] or 'current head'})."
        )
        return _CarriedPrePassResult(outcome, [], (), updated_head_sha, head_changed, None, None)
    record = _set_blocked_on(run.context, updated_head_sha, carried, _CLEAN_TREE_REASON)
    ids = ", ".join(record["finding_ids"])
    run.context.log(f"[Review Loop] Halting once on blocked_on ({ids}): {_CLEAN_TREE_REASON}.")
    return _CarriedPrePassResult(outcome, list(carried), (), updated_head_sha, head_changed, None, record)


def resolve_carried_first(
    carried: Sequence[FindingRow], action_ids: Mapping[str, str], run: CarriedPass
) -> tuple[ResolverOutcome, list[FindingRow], tuple[FindingDisposition, ...]]:
    run.context.log(
        f"[Review Loop] Resolving {len(carried)} carried finding(s) from an earlier iteration before reviewing."
    )
    resolver_input = ResolverInput(findings=list(carried), action_ids=dict(action_ids))
    outcome = _invoke_resolver_pass(run.context, run.target, run.iteration, resolver_input, run.run_resolver_fn)
    if outcome.error:
        if not _is_clean_tree_resolver_error(outcome.error):
            run.context.log(
                f"[Review Loop] Carried-finding resolver pass failed, no review iteration starts: {outcome.error}"
            )
        return outcome, list(carried), ()
    decided = [
        d for d in outcome.dispositions if d.disposition == REJECTED or (outcome.pushed and d.disposition in _DECIDED)
    ]
    # why: the loop records an unpushed fix later, as it does for its own, so it does not block approval
    pending = () if outcome.pushed else tuple(d for d in outcome.dispositions if d.disposition == "applied")
    run.recorder.record(run.iteration, decided)
    return outcome, undisposed(carried, run.recorder.all_dispositions), pending


def _run_pre_iteration_carried_pass(
    carried: Sequence[FindingRow],
    action_ids: Mapping[str, str],
    run: CarriedPass,
    deps: _CarriedPrePassDeps,
) -> _CarriedPrePassResult:
    existing = _active_blocked_on(run.context, run.target.head_sha)
    if existing is not None:
        run.context.log(
            f"[Review Loop] Already halted on blocked_on for head {run.target.head_sha[:8]}: {existing.get('reason')}."
        )
        return _CarriedPrePassResult(None, list(carried), (), run.target.head_sha, False, None, existing)
    delta_review.record_addressed_findings(run.context, carried)
    outcome, remaining, pending = resolve_carried_first(carried, action_ids, run)
    if outcome.error is None:
        _clear_blocked_on(run.context)
        head_changed = bool(outcome.pushed and outcome.head_sha)
        updated_sha = outcome.head_sha if head_changed and outcome.head_sha else run.target.head_sha
        return _CarriedPrePassResult(outcome, remaining, pending, updated_sha, head_changed, None, None)
    if _is_clean_tree_resolver_error(outcome.error):
        return _handle_clean_tree_outcome(carried, run, outcome, deps)
    if deps.record_rejected is not None:
        deps.record_rejected(run.iteration, outcome)
    return _CarriedPrePassResult(outcome, remaining, (), run.target.head_sha, False, outcome.error, None)
