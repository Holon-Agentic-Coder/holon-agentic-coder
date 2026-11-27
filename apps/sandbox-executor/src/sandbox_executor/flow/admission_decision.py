from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from sandbox_executor.flow.admission_lock import AdmissionLockError, _live_runner_worktrees
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
from sandbox_executor.flow.in_flight_liveness import ProcessCmdlines, fetch_prune_origin
from sandbox_executor.touch_set import TouchSet
from sandbox_executor.touch_set_overlap import TouchSetOnBase, changed_files_overlap, declared_overlap


@dataclass(frozen=True)
class AdmissionConflict:
    label: str
    files: tuple[str, ...]
    paths: tuple[str, ...] = field(default=(), compare=False)
    ref_name: str | None = field(default=None, compare=False)
    ref_source: str | None = field(default=None, compare=False)
    pr_number: int | None = field(default=None, compare=False)
    pr_state: str | None = field(default=None, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {"flow": self.label, "files": list(self.files)}


@dataclass(frozen=True)
class AdmissionDecision:
    admitted: bool
    conflicts: tuple[AdmissionConflict, ...] = ()
    reason: str | None = None
    overridden: bool = False
    blocked_by: tuple[BlockedBy, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {"conflicts": [conflict.to_dict() for conflict in self.conflicts], "admission_reason": self.reason}


def format_blocker_provenance(conflict: AdmissionConflict) -> str:
    ref = conflict.ref_name or "unknown"
    source = conflict.ref_source or "local"
    if conflict.pr_number is not None:
        state = conflict.pr_state or "UNKNOWN"
        return f"ref={ref} source={source} pr=#{conflict.pr_number} pr_state={state}"
    return f"ref={ref} source={source} pr=none"


def _conflict(own: TouchSet, flow: InFlightFlow, base_files: frozenset[str]) -> AdmissionConflict | None:
    # why: an open pull request is judged by its diff, never by its planned touch set
    if flow.changed_files is not None:
        files = changed_files_overlap(own, flow.changed_files, base_files)
    else:
        files = declared_overlap(own, flow.declared, base_files) if flow.declared is not None else []
    if not files:
        return None
    ref_name = flow.ref_name or flow.head or flow.plan_branch
    ref_source = flow.ref_source or "local"
    return AdmissionConflict(
        flow.label,
        tuple(files),
        tuple(files),
        ref_name=ref_name,
        ref_source=ref_source,
        pr_number=flow.number,
        pr_state=flow.pr_state,
    )


def refusal(
    context: FlowContext,
    reason: str | None = None,
    conflicts: tuple[AdmissionConflict, ...] = (),
    blocked_by: tuple[BlockedBy, ...] = (),
) -> AdmissionDecision:
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


def _filter_live_waiters(
    repo_dir: str,
    waiters: list[BlockedWaiter],
    run_git: GitRunner | None,
) -> list[BlockedWaiter] | AdmissionUnavailable:
    if not waiters:
        return waiters
    try:
        live_worktrees = _live_runner_worktrees(repo_dir, run_git)
    except AdmissionLockError as exc:
        return AdmissionUnavailable(str(exc))
    return [waiter for waiter in waiters if os.path.realpath(waiter.worktree) in live_worktrees]


def decide_admission(
    context: FlowContext,
    run_gh: GhRunner | None,
    run_git: GitRunner | None,
    cmdlines: ProcessCmdlines | None = None,
) -> AdmissionDecision:
    fetch_error = fetch_prune_origin(context.target_worktree, run_git)
    if fetch_error is not None:
        return refusal(context, reason=f"origin refs could not be refreshed before admission: {fetch_error.reason}")
    plan_branch = str(context.plan_branch or "")
    own = read_plan_touch_set(context.target_worktree, plan_branch) if plan_branch else None
    if own is None:
        return refusal(context, reason=_no_touch_set_reason(plan_branch))
    own_branch = context.intent_branch or plan_branch
    checkpoints = other_checkpoints(context.target_worktree, root_intent(own_branch), run_git)
    if isinstance(checkpoints, AdmissionUnavailable):
        return refusal(context, reason=f"the flows in flight could not be discovered: {checkpoints.reason}")
    flows = discover_in_flight_flows(context.target_worktree, own_branch, run_gh, run_git, cmdlines)
    if isinstance(flows, AdmissionUnavailable):
        return refusal(context, reason=f"the flows in flight could not be discovered: {flows.reason}")
    waiters = local_blocked_waiters(context.target_worktree, checkpoints)
    live_waiters = _filter_live_waiters(context.target_worktree, waiters, run_git)
    if isinstance(live_waiters, AdmissionUnavailable):
        return refusal(context, reason=f"the flows in flight could not be discovered: {live_waiters.reason}")
    return _overlap_decision(context, own, flows, live_waiters, run_git)


def _overlap_decision(
    context: FlowContext,
    own: TouchSet,
    flows: list[InFlightFlow],
    waiters: list[BlockedWaiter],
    run_git: GitRunner | None,
) -> AdmissionDecision:
    # why: a failed base listing refuses, so it is never attempted when nothing can overlap
    if not flows and not waiters:
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
