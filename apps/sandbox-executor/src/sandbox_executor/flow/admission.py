"""Stage 3 admission: refuse to dispatch an executor whose plan's touch set overlaps a flow in flight.

Before any execution branch, container or token is spent, :mod:`flow.admission_decision` compares the active plan's
touch set with every flow in flight (a plan without a touch set, or a discovery that could not run, is refused too:
fail closed). Outside a dry run the check holds the repository's admission lock (:mod:`flow.admission_lock`) from
discovery until the admission state (:mod:`flow.admission_state`) is written to this flow's checkpoint, so two
overlapping flows on one host are never admitted together: an overlap refusal records ``blocked``, an admission
``admitted``. ``--allow-overlap`` (``FlowContext.allow_overlap``) admits the run anyway and appends one
``admission_override`` row to the plan branch's ``executions.jsonl``; refusal never closes, merges or edits another
flow.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sandbox_executor.flow import admission_state
from sandbox_executor.flow.admission_decision import AdmissionDecision, decide_admission, refusal
from sandbox_executor.flow.admission_lock import AdmissionLockError, admission_lock
from sandbox_executor.flow.admission_state import AdmissionRecord, admitted_record, blocked_record
from sandbox_executor.flow.checkpoint import save_admission
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.git_refs import _append_ledger_row
from sandbox_executor.flow.github.merge import GhRunner
from sandbox_executor.flow.in_flight import GitRunner

#: ``entry_type`` and ``kind`` of the executions.jsonl row an override appends (never a summary or an action).
ADMISSION_OVERRIDE_KIND = "admission_override"


def record_admission(context: FlowContext, decision: AdmissionDecision) -> AdmissionDecision:
    """Write the decision's admission state into this flow's checkpoint (the path other worktrees' discovery reads).

    An admission records ``admitted`` (with the override and its conflicts under ``--allow-overlap``), an overlap
    refusal ``blocked``; any other refusal records nothing. A write failure refuses fail-closed.
    """
    if decision.admitted:
        overridden = [conflict.to_dict() for conflict in decision.conflicts] if decision.overridden else None
        record = admitted_record(overridden)
    elif decision.blocked_by:
        previous = AdmissionRecord.from_dict(context.admission)
        record = blocked_record(previous, decision.blocked_by, admission_state.utc_now())
    else:
        return decision
    error = save_admission(context, record.to_dict())
    return decision if error is None else refusal(context, reason=f"the admission state could not be recorded: {error}")


def check_admission(
    context: FlowContext, run_gh: GhRunner | None = None, run_git: GitRunner | None = None
) -> AdmissionDecision:
    """Compare the active plan's touch set with every flow in flight and decide whether Stage 3 may dispatch.

    Outside a dry run the decision is made and recorded under the repository's admission lock; a dry run neither
    takes the lock nor writes anything.
    """
    if context.dry_run:
        return decide_admission(context, run_gh, run_git)
    try:
        with admission_lock(context.target_worktree):
            return record_admission(context, decide_admission(context, run_gh, run_git))
    except AdmissionLockError as exc:
        return refusal(context, reason=f"the admission lock could not be taken: {exc}")


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
        if save_admission(context, previous) is not None:  # undo the admitted record: the run is refused
            context.log("Stage 3 admission: could not restore the checkpoint's previous admission state.")
        error = f"Stage 3 refused: the --allow-overlap override could not be recorded: {write_error}"
        return _stage_failure(context, FlowStage.EXECUTE, start_time, error, dispatched=False, **decision.to_payload())
    context.log(f"Stage 3 admitted by --allow-overlap (recorded on '{context.plan_branch}'): {message}")
    return None
