from __future__ import annotations

from datetime import UTC, datetime

from sandbox_executor.flow import admission_state
from sandbox_executor.flow.admission_decision import AdmissionDecision, decide_admission
from sandbox_executor.flow.admission_lock import AdmissionLockError, admission_lock
from sandbox_executor.flow.admission_record import ADMISSION_OVERRIDE_KIND, override_already_recorded
from sandbox_executor.flow.admission_state import AdmissionRecord, admitted_record, blocked_record
from sandbox_executor.flow.checkpoint import save_admission
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.git_refs import _append_ledger_row
from sandbox_executor.flow.github.merge import GhRunner
from sandbox_executor.flow.in_flight import GitRunner


def record_admission(context: FlowContext, decision: AdmissionDecision) -> AdmissionDecision:
    previous = AdmissionRecord.from_dict(context.admission)
    if decision.admitted:
        overridden = [conflict.to_dict() for conflict in decision.conflicts] if decision.overridden else None
        record = admitted_record(overridden, previous)
    elif decision.blocked_by:
        record = blocked_record(previous, decision.blocked_by, admission_state.utc_now())
    else:
        return decision
    if (error := save_admission(context, record.to_dict())) is None:
        return decision
    # why: an admission other worktrees' discovery cannot read would let an overlapping flow through
    return AdmissionDecision(False, reason=f"the admission state could not be recorded: {error}")


def check_admission(
    context: FlowContext, run_gh: GhRunner | None = None, run_git: GitRunner | None = None
) -> AdmissionDecision:
    if context.dry_run:
        return decide_admission(context, run_gh, run_git)
    try:
        with admission_lock(context.target_worktree, run_git):
            return record_admission(context, decide_admission(context, run_gh, run_git))
    # why: even under --allow-overlap an admission must be recorded under the lock, or discovery could miss it
    except AdmissionLockError as exc:
        return AdmissionDecision(False, reason=f"the admission lock could not be taken: {exc}")


# why: only the refusal message truncates; the payload and blocked_by keep every overlapping file
MAX_REFUSAL_FILES = 20


def _file_list(files: tuple[str, ...]) -> str:
    shown = ", ".join(files[:MAX_REFUSAL_FILES])
    return f"{shown} and {len(files) - MAX_REFUSAL_FILES} more" if len(files) > MAX_REFUSAL_FILES else shown


def format_refusal(decision: AdmissionDecision) -> str:
    if decision.conflicts:
        overlaps = "; ".join(f"{conflict.label} on {_file_list(conflict.files)}" for conflict in decision.conflicts)
        return (
            f"Stage 3 refused: the touch set overlaps {overlaps}; finish or merge that flow first, "
            "or rerun with --allow-overlap."
        )
    return f"Stage 3 refused: {decision.reason}; or rerun with --allow-overlap to admit it anyway."


def _record_admission_override(context: FlowContext, decision: AdmissionDecision) -> str | None:
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
    if override_already_recorded(context.target_worktree, str(context.plan_branch)):
        context.log(
            f"Stage 3 admitted by --allow-overlap (override already recorded on '{context.plan_branch}'): {message}"
        )
        return None
    write_error = _record_admission_override(context, decision)
    if write_error is not None:
        # why: check_admission already recorded this refused run as admitted
        if save_admission(context, previous) is not None:
            context.log("Stage 3 admission: could not restore the checkpoint's previous admission state.")
        error = f"Stage 3 refused: the --allow-overlap override could not be recorded: {write_error}"
        return _stage_failure(context, FlowStage.EXECUTE, start_time, error, dispatched=False, **decision.to_payload())
    context.log(f"Stage 3 admitted by --allow-overlap (recorded on '{context.plan_branch}'): {message}")
    return None
