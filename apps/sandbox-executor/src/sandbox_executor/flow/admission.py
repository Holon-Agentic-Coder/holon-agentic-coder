"""Stage 3 admission: refuse to dispatch an executor whose plan's touch set overlaps a flow in flight.

Before any execution branch, container or token is spent, the active plan row's ``touch_set`` is compared with every
flow :mod:`flow.in_flight` discovers: with its declared touch set (entry against entry, ``overlapping_entries``) and,
for an open pull request, with its real changed files (``files_in_set``). Metadata never conflicts. A plan without a
touch set, or a discovery that could not run, is refused too (fail closed). ``--allow-overlap``
(``FlowContext.allow_overlap``) admits the run anyway and appends one ``admission_override`` row to the plan branch's
``executions.jsonl``; refusal never closes, merges or edits another flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.git_refs import _append_ledger_row
from sandbox_executor.flow.github.merge import GhRunner
from sandbox_executor.flow.in_flight import (
    AdmissionUnavailable,
    GitRunner,
    InFlightFlow,
    discover_in_flight_flows,
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
    context: FlowContext, reason: str | None = None, conflicts: tuple[AdmissionConflict, ...] = ()
) -> AdmissionDecision:
    return AdmissionDecision(context.allow_overlap, conflicts, reason, overridden=context.allow_overlap)


def check_admission(
    context: FlowContext, run_gh: GhRunner | None = None, run_git: GitRunner | None = None
) -> AdmissionDecision:
    """Compare the active plan's touch set with every flow in flight and decide whether Stage 3 may dispatch."""
    plan_branch = str(context.plan_branch or "")
    own = read_plan_touch_set(context.target_worktree, plan_branch) if plan_branch else None
    if own is None:
        plan_id = plan_branch.removesuffix("/_").rsplit("/", 1)[-1] or "the active plan"
        reason = (
            f"the plan row of {plan_id} records no touch set or could not be read; re-plan so the plan declares a "
            "'## Touch Set' section"
        )
        return _refused(context, reason=reason)
    flows = discover_in_flight_flows(context.target_worktree, context.intent_branch or plan_branch, run_gh, run_git)
    if isinstance(flows, AdmissionUnavailable):
        return _refused(context, reason=f"the flows in flight could not be discovered: {flows.reason}")
    conflicts = tuple(conflict for conflict in (_conflict(own, flow) for flow in flows) if conflict is not None)
    return _refused(context, conflicts=conflicts) if conflicts else AdmissionDecision(admitted=True)


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
        error = f"Stage 3 refused: the --allow-overlap override could not be recorded: {write_error}"
        return _stage_failure(context, FlowStage.EXECUTE, start_time, error, dispatched=False, **decision.to_payload())
    context.log(f"Stage 3 admitted by --allow-overlap (recorded on '{context.plan_branch}'): {message}")
    return None
