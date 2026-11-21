"""The Stage 3 admission decision: compare the active plan's touch set with every flow in flight.

The plan row's ``touch_set`` is compared with every flow :mod:`flow.in_flight` discovers, on concrete files
(:mod:`touch_set_overlap`) against the files of the base branch (:mod:`flow.base_files`): an open pull request by its
changed files only (``changed_files_overlap``), a local flow by its declared touch set (``declared_overlap``). A new
file overlaps only an identical literal path, and folder or glob entries match only base files. Metadata never
conflicts; the base is listed only when some flow or waiter is there to compare, and a listing that fails refuses
(fail closed). A plan without a touch set, or a discovery that could not run, is refused too (fail closed). When
nothing overlaps, the flow still defers to an older ready waiter (:mod:`flow.admission_order`). An overlap refusal
carries the ``blocked_by`` entries :mod:`flow.admission` records; ``--allow-overlap`` admits any refusal as
``overridden``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sandbox_executor.flow.admission_order import older_waiter_flows
from sandbox_executor.flow.admission_state import BlockedBy
from sandbox_executor.flow.base_files import base_branch_name, list_base_files
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.merge import GhRunner
from sandbox_executor.flow.in_flight import (
    AdmissionUnavailable,
    BlockedWaiter,
    GitRunner,
    InFlightFlow,
    discover_in_flight_flows,
    local_blocked_waiters,
    other_checkpoints,
    read_plan_touch_set,
    root_intent,
)
from sandbox_executor.touch_set import TouchSet
from sandbox_executor.touch_set_overlap import TouchSetOnBase, changed_files_overlap, declared_overlap


@dataclass(frozen=True)
class AdmissionConflict:
    """One in-flight flow this plan overlaps, and the concrete files both may touch."""

    label: str
    files: tuple[str, ...]
    paths: tuple[str, ...] = field(default=(), compare=False)  # the same files, for ``blocked_by``

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


def _conflict(own: TouchSet, flow: InFlightFlow, base_files: frozenset[str]) -> AdmissionConflict | None:
    if flow.changed_files is not None:  # a pull request: its diff, never its planned touch set
        files = changed_files_overlap(own, flow.changed_files, base_files)
    else:
        files = declared_overlap(own, flow.declared, base_files) if flow.declared is not None else []
    return AdmissionConflict(flow.label, tuple(files), tuple(files)) if files else None


def refusal(
    context: FlowContext,
    reason: str | None = None,
    conflicts: tuple[AdmissionConflict, ...] = (),
    blocked_by: tuple[BlockedBy, ...] = (),
) -> AdmissionDecision:
    """A refusal (admitted as ``overridden`` under ``--allow-overlap``) with its reason or overlap conflicts."""
    return AdmissionDecision(context.allow_overlap, conflicts, reason, context.allow_overlap, blocked_by)


def _conflict_pairs(
    own: TouchSet, flows: list[InFlightFlow], base_files: frozenset[str]
) -> list[tuple[InFlightFlow, AdmissionConflict]]:
    return [(flow, conflict) for flow in flows if (conflict := _conflict(own, flow, base_files)) is not None]


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
    checkpoints = other_checkpoints(context.target_worktree, root_intent(own_branch), run_git)  # read once
    if isinstance(checkpoints, AdmissionUnavailable):
        return refusal(context, reason=f"the flows in flight could not be discovered: {checkpoints.reason}")
    flows = discover_in_flight_flows(context.target_worktree, own_branch, run_gh, checkpoints=checkpoints)
    if isinstance(flows, AdmissionUnavailable):
        return refusal(context, reason=f"the flows in flight could not be discovered: {flows.reason}")
    waiters = local_blocked_waiters(context.target_worktree, checkpoints)
    return _overlap_decision(context, own, flows, waiters, run_git)


def _overlap_decision(
    context: FlowContext,
    own: TouchSet,
    flows: list[InFlightFlow],
    waiters: list[BlockedWaiter],
    run_git: GitRunner | None,
) -> AdmissionDecision:
    """Admit unless ``own`` overlaps a flow in flight or an older ready waiter, on the files of the base branch."""
    if not flows and not waiters:  # nothing to overlap: the base branch is never listed
        return AdmissionDecision(admitted=True)
    base_files = list_base_files(context.target_worktree, base_branch_name(context), run_git)
    if isinstance(base_files, AdmissionUnavailable):
        return refusal(context, reason=f"the files of the base branch could not be listed: {base_files.reason}")
    pairs = _conflict_pairs(own, flows, base_files)
    if not pairs:
        plan_branch = str(context.plan_branch or "")
        older = older_waiter_flows(context.admission, plan_branch, TouchSetOnBase(own, base_files), flows, waiters)
        pairs = _conflict_pairs(own, older, base_files)
    if not pairs:
        return AdmissionDecision(admitted=True)
    conflicts = tuple(conflict for _flow, conflict in pairs)
    blocked_by = tuple(BlockedBy(f.kind, c.paths, f.number, f.head, f.plan_branch, f.worktree) for f, c in pairs)
    return refusal(context, conflicts=conflicts, blocked_by=blocked_by)
