"""Stage 3 admission: refuse to dispatch an executor whose plan's touch set overlaps a flow in flight.

Before any execution branch, container or token is spent, the active plan row's ``touch_set`` is compared with every
flow :mod:`flow.in_flight` discovers: with its declared touch set (entry against entry, ``overlapping_entries``) and,
for an open pull request, with its real changed files (``files_in_set``). Metadata never conflicts. A plan without a
touch set, or a discovery that could not run, is refused too (fail closed). ``--allow-overlap``
(``FlowContext.allow_overlap``) admits the run anyway and appends one ``admission_override`` row to the plan branch's
``executions.jsonl``; refusal never closes, merges or edits another flow.

Outside a dry run the check holds the repository's admission lock (:mod:`flow.admission_lock`) from discovery until the
admission state (:mod:`flow.admission_state`) is written to this flow's checkpoint, so two overlapping flows on one host
are never admitted together, and a flow with no overlap still defers to an older ready waiter
(:mod:`flow.admission_order`). An overlap refusal records ``blocked``; an admission records ``admitted``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow import admission_state
from sandbox_executor.flow.admission_lock import AdmissionLockError, admission_lock
from sandbox_executor.flow.admission_order import older_ready_waiter
from sandbox_executor.flow.admission_state import (
    ADMISSION_ADMITTED,
    AdmissionRecord,
    BlockedBy,
    admitted_record,
    blocked_record,
)
from sandbox_executor.flow.checkpoint import save_checkpoint
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.git_refs import _append_ledger_row
from sandbox_executor.flow.github.merge import GhRunner
from sandbox_executor.flow.in_flight import (
    AdmissionUnavailable,
    BlockedWaiter,
    GitRunner,
    InFlightFlow,
    _root_intent,
    discover_in_flight_flows,
    local_blocked_waiters,
    read_plan_touch_set,
)
from sandbox_executor.touch_set import TouchSet, files_in_set, overlapping_entries

#: ``entry_type`` and ``kind`` of the executions.jsonl row an override appends (never a summary or an action).
ADMISSION_OVERRIDE_KIND = "admission_override"


@dataclass(frozen=True)
class AdmissionConflict:
    """One in-flight flow this plan overlaps, and the files or declared entries both may touch."""

    label: str
    files: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Payload and ledger shape."""
        return {"flow": self.label, "files": list(self.files)}


@dataclass(frozen=True)
class AdmissionDecision:
    """Whether Stage 3 may dispatch; ``overridden`` when ``--allow-overlap`` admitted a refused plan."""

    admitted: bool
    conflicts: tuple[AdmissionConflict, ...] = ()
    reason: str | None = None
    overridden: bool = False
    blocked_by: tuple[BlockedBy, ...] = ()  # the checkpoint's blocked_by entries of an overlap refusal

    def to_payload(self) -> dict[str, Any]:
        """Stage 3 payload fields: the conflicts and, for a refusal without one, its reason."""
        return {"conflicts": [conflict.to_dict() for conflict in self.conflicts], "admission_reason": self.reason}


def _conflict(own: TouchSet, flow: InFlightFlow) -> AdmissionConflict | None:
    files = list(files_in_set(own, list(flow.changed_files))) if flow.changed_files is not None else []
    if flow.declared is not None:
        pairs = overlapping_entries(own, flow.declared)
        files.extend(mine if mine == theirs else f"{mine} (its entry {theirs})" for mine, theirs in pairs)
    unique = tuple(dict.fromkeys(files))
    return AdmissionConflict(flow.label, unique) if unique else None


def _refused(
    context: FlowContext,
    reason: str | None = None,
    conflicts: tuple[AdmissionConflict, ...] = (),
    blocked_by: tuple[BlockedBy, ...] = (),
) -> AdmissionDecision:
    return AdmissionDecision(context.allow_overlap, conflicts, reason, context.allow_overlap, blocked_by)


def _blocked_by(flow: InFlightFlow, conflict: AdmissionConflict) -> BlockedBy:
    return BlockedBy(flow.kind, conflict.files, flow.number, flow.head, flow.plan_branch, flow.worktree)


def _own_key(record: AdmissionRecord | None, plan_branch: str) -> tuple[str, str]:
    """This flow's place in the admission order: its first refusal time, or now when it was never refused."""
    since = record.blocked_since if record is not None and record.blocked else None
    return (since or admission_state.utc_now(), plan_branch)


def _waiter_conflicts(
    context: FlowContext, own: TouchSet, flows: list[InFlightFlow], waiters: list[BlockedWaiter]
) -> list[tuple[InFlightFlow, AdmissionConflict]]:
    """The older ready waiter this flow defers to, as a ``local_flow`` conflict; empty when there is none."""
    record = AdmissionRecord.from_dict(context.admission)
    own_admitted = record is not None and record.state == ADMISSION_ADMITTED
    waiter = older_ready_waiter(_own_key(record, str(context.plan_branch)), own, flows, waiters, own_admitted)
    if waiter is None:
        return []
    since = waiter.record.blocked_since
    label = f"an older waiting flow on branch {waiter.plan_branch} (worktree {waiter.worktree}, blocked since {since})"
    flow = InFlightFlow(label, waiter.plan_branch, waiter.declared, worktree=waiter.worktree)
    conflict = _conflict(own, flow)
    return [(flow, conflict)] if conflict is not None else []


def _no_touch_set_reason(plan_branch: str) -> str:
    plan_id = plan_branch.removesuffix("/_").rsplit("/", 1)[-1] or "the active plan"
    return (
        f"the plan row of {plan_id} records no touch set or could not be read; re-plan so the plan declares a "
        "'## Touch Set' section"
    )


def _decide(context: FlowContext, run_gh: GhRunner | None, run_git: GitRunner | None) -> AdmissionDecision:
    plan_branch = str(context.plan_branch or "")
    own = read_plan_touch_set(context.target_worktree, plan_branch) if plan_branch else None
    if own is None:
        return _refused(context, reason=_no_touch_set_reason(plan_branch))
    own_branch = context.intent_branch or plan_branch
    flows = discover_in_flight_flows(context.target_worktree, own_branch, run_gh, run_git)
    if isinstance(flows, AdmissionUnavailable):
        return _refused(context, reason=f"the flows in flight could not be discovered: {flows.reason}")
    waiters = local_blocked_waiters(context.target_worktree, _root_intent(own_branch), run_git)
    if isinstance(waiters, AdmissionUnavailable):
        return _refused(context, reason=f"the flows in flight could not be discovered: {waiters.reason}")
    pairs = [(flow, conflict) for flow in flows if (conflict := _conflict(own, flow)) is not None]
    pairs = pairs or _waiter_conflicts(context, own, flows, waiters)
    if not pairs:
        return AdmissionDecision(admitted=True)
    conflicts = tuple(conflict for _flow, conflict in pairs)
    return _refused(context, conflicts=conflicts, blocked_by=tuple(_blocked_by(*pair) for pair in pairs))


def record_admission(context: FlowContext, decision: AdmissionDecision) -> AdmissionDecision:
    """Write the decision's admission state into this flow's checkpoint (the path other worktrees' discovery reads).

    An admission records ``admitted`` (with the override and its conflicts under ``--allow-overlap``), an overlap
    refusal ``blocked``; any other refusal records nothing. A write failure refuses fail-closed.
    """
    previous = context.admission
    if decision.admitted:
        overridden = [conflict.to_dict() for conflict in decision.conflicts] if decision.overridden else None
        record = admitted_record(overridden)
    elif decision.blocked_by:
        record = blocked_record(AdmissionRecord.from_dict(previous), decision.blocked_by, admission_state.utc_now())
    else:
        return decision
    context.admission = record.to_dict()
    try:
        save_checkpoint(context)
    except OSError as exc:
        context.admission = previous
        return _refused(context, reason=f"the admission state could not be recorded: {exc}")
    return decision


def check_admission(
    context: FlowContext, run_gh: GhRunner | None = None, run_git: GitRunner | None = None
) -> AdmissionDecision:
    """Compare the active plan's touch set with every flow in flight and decide whether Stage 3 may dispatch.

    Outside a dry run the decision is made and recorded under the repository's admission lock; a dry run neither
    takes the lock nor writes anything.
    """
    if context.dry_run:
        return _decide(context, run_gh, run_git)
    try:
        with admission_lock(context.target_worktree):
            return record_admission(context, _decide(context, run_gh, run_git))
    except AdmissionLockError as exc:
        return _refused(context, reason=f"the admission lock could not be taken: {exc}")


def format_refusal(decision: AdmissionDecision) -> str:
    """One message naming every overlapping flow (pull request and branch, or local branch) and its files."""
    if decision.conflicts:
        overlaps = "; ".join(f"{conflict.label} on {', '.join(conflict.files)}" for conflict in decision.conflicts)
        return (
            f"Stage 3 refused: the touch set overlaps {overlaps}; finish or merge that flow first, "
            "or rerun with --allow-overlap."
        )
    return f"Stage 3 refused: {decision.reason}; or rerun with --allow-overlap to admit it anyway."


def _record_admission_override(context: FlowContext, decision: AdmissionDecision) -> str | None:
    """Append one ``admission_override`` row to the plan branch's executions.jsonl; the error text on failure."""
    plan_branch = str(context.plan_branch)
    plan_id = plan_branch.removesuffix("/_").rsplit("/", 1)[-1]
    row = {
        "entry_type": ADMISSION_OVERRIDE_KIND,
        "kind": ADMISSION_OVERRIDE_KIND,
        "plan_id": plan_id,
        "plan_branch": plan_branch,
        "intent_id": context.intent_branch or context.intent_data.get("branch"),
        "conflicts": [conflict.to_dict() for conflict in decision.conflicts],
        "reason": decision.reason,
        "recorded_by": "flow",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
    }
    message = f"execute: record the --allow-overlap admission override of {plan_id}"
    return _append_ledger_row(context, plan_branch, "executions.jsonl", row, message)


def _restore_admission(context: FlowContext, previous: dict[str, Any] | None) -> None:
    """Undo an ``admitted`` override record whose ledger row could not be written (the run is refused)."""
    context.admission = previous
    try:
        save_checkpoint(context)
    except OSError as exc:
        context.log(f"Stage 3 admission: could not restore the checkpoint's admission state: {exc}")


def admission_stage_failure(context: FlowContext, start_time: str) -> StageResult | None:
    """Run admission for Stage 3: ``None`` to dispatch, or the failed result that refuses it before any dispatch.

    A dry run only logs the decision. An override is recorded before dispatch; if that row cannot be written the run
    is refused, so no overlapping execution goes unrecorded.
    """
    previous = context.admission
    decision = check_admission(context)
    if decision.admitted and not decision.overridden:
        context.log("Stage 3 admission: the touch set overlaps no flow in flight.")
        return None
    message = format_refusal(decision)
    if context.dry_run:
        context.log(f"Stage 3 (dry run) admission: {message}")
        return None
    if not decision.overridden:
        return _stage_failure(
            context,
            FlowStage.EXECUTE,
            start_time,
            message,
            admission_refused=True,
            dispatched=False,
            **decision.to_payload(),
        )
    write_error = _record_admission_override(context, decision)
    if write_error is not None:
        _restore_admission(context, previous)
        error = f"Stage 3 refused: the --allow-overlap override could not be recorded: {write_error}"
        return _stage_failure(context, FlowStage.EXECUTE, start_time, error, dispatched=False, **decision.to_payload())
    context.log(f"Stage 3 admitted by --allow-overlap (recorded on '{context.plan_branch}'): {message}")
    return None
