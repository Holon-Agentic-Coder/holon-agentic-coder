"""The Stage 3 admission decision: compare the active plan's touch set with every flow in flight.

The plan row's ``touch_set`` is compared with every flow :mod:`flow.in_flight` discovers: with its declared touch set
(entry against entry, ``overlapping_entries``) and, for an open pull request, with its real changed files
(``files_in_set``). Metadata never conflicts. A plan without a touch set, or a discovery that could not run, is refused
too (fail closed). When nothing overlaps, the flow still defers to an older ready waiter (:mod:`flow.admission_order`).
An overlap refusal carries the ``blocked_by`` entries :mod:`flow.admission` records; ``--allow-overlap`` admits any
refusal as ``overridden``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.admission_order import older_waiter_flows
from sandbox_executor.flow.admission_state import BlockedBy
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.merge import GhRunner
from sandbox_executor.flow.in_flight import (
    AdmissionUnavailable,
    GitRunner,
    InFlightFlow,
    _root_intent,
    discover_in_flight_flows,
    local_blocked_waiters,
    read_plan_touch_set,
)
from sandbox_executor.touch_set import TouchSet, files_in_set, overlapping_entries


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


def refusal(
    context: FlowContext,
    reason: str | None = None,
    conflicts: tuple[AdmissionConflict, ...] = (),
    blocked_by: tuple[BlockedBy, ...] = (),
) -> AdmissionDecision:
    """A refusal (admitted as ``overridden`` under ``--allow-overlap``) with its reason or overlap conflicts."""
    return AdmissionDecision(context.allow_overlap, conflicts, reason, context.allow_overlap, blocked_by)


def _blocked_by(flow: InFlightFlow, conflict: AdmissionConflict) -> BlockedBy:
    return BlockedBy(flow.kind, conflict.files, flow.number, flow.head, flow.plan_branch, flow.worktree)


def _conflict_pairs(own: TouchSet, flows: list[InFlightFlow]) -> list[tuple[InFlightFlow, AdmissionConflict]]:
    return [(flow, conflict) for flow in flows if (conflict := _conflict(own, flow)) is not None]


def _no_touch_set_reason(plan_branch: str) -> str:
    plan_id = plan_branch.removesuffix("/_").rsplit("/", 1)[-1] or "the active plan"
    return (
        f"the plan row of {plan_id} records no touch set or could not be read; re-plan so the plan declares a "
        "'## Touch Set' section"
    )


def decide_admission(context: FlowContext, run_gh: GhRunner | None, run_git: GitRunner | None) -> AdmissionDecision:
    """Whether Stage 3 may dispatch: the overlapping flows (or the older waiter deferred to), or why it cannot tell."""
    plan_branch = str(context.plan_branch or "")
    own = read_plan_touch_set(context.target_worktree, plan_branch) if plan_branch else None
    if own is None:
        return refusal(context, reason=_no_touch_set_reason(plan_branch))
    own_branch = context.intent_branch or plan_branch
    flows = discover_in_flight_flows(context.target_worktree, own_branch, run_gh, run_git)
    if isinstance(flows, AdmissionUnavailable):
        return refusal(context, reason=f"the flows in flight could not be discovered: {flows.reason}")
    waiters = local_blocked_waiters(context.target_worktree, _root_intent(own_branch), run_git)
    if isinstance(waiters, AdmissionUnavailable):
        return refusal(context, reason=f"the flows in flight could not be discovered: {waiters.reason}")
    pairs = _conflict_pairs(own, flows)
    pairs = pairs or _conflict_pairs(own, older_waiter_flows(context.admission, plan_branch, own, flows, waiters))
    if not pairs:
        return AdmissionDecision(admitted=True)
    conflicts = tuple(conflict for _flow, conflict in pairs)
    return refusal(context, conflicts=conflicts, blocked_by=tuple(_blocked_by(*pair) for pair in pairs))
