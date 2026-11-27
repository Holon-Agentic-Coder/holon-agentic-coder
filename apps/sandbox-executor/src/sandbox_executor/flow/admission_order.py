from __future__ import annotations

from typing import Any

from sandbox_executor.flow import admission_state
from sandbox_executor.flow.admission_state import (
    ADMISSION_ADMITTED,
    BLOCKED_BY_PULL_REQUEST,
    AdmissionRecord,
    BlockedBy,
)
from sandbox_executor.flow.in_flight import BlockedWaiter, InFlightFlow, root_intent
from sandbox_executor.touch_set_overlap import TouchSetOnBase


def waiter_key(waiter: BlockedWaiter) -> tuple[str, str]:
    # why: a total order on one host's clock keeps the waits-for graph acyclic
    return (waiter.record.blocked_since or "", waiter.plan_branch)


def blocker_is_clear(entry: BlockedBy, open_numbers: set[int], busy: set[str], own_plan_branch: str) -> bool:
    own_root = root_intent(own_plan_branch)
    if entry.kind == BLOCKED_BY_PULL_REQUEST:
        return root_intent(entry.branch or "") != own_root and entry.number not in open_numbers
    plan_branch = entry.plan_branch or ""
    # why: discovery never lists this lineage's own flows, so they can never be seen to clear
    if plan_branch != own_plan_branch and root_intent(plan_branch) == own_root:
        return False
    return plan_branch not in busy


def older_ready_waiter(
    own_key: tuple[str, str],
    own: TouchSetOnBase,
    flows: list[InFlightFlow],
    waiters: list[BlockedWaiter],
) -> BlockedWaiter | None:
    own_plan_branch = own_key[1]
    in_flight = {flow.plan_branch for flow in flows}
    busy = in_flight | {waiter.plan_branch for waiter in waiters}
    open_numbers = {flow.number for flow in flows if flow.number is not None}
    ready = [
        waiter
        for waiter in waiters
        if waiter.plan_branch not in in_flight
        and waiter.record.blocked_since
        and waiter_key(waiter) < own_key
        and waiter.declared is not None
        and own.overlap(waiter.declared)
        and all(blocker_is_clear(entry, open_numbers, busy, own_plan_branch) for entry in waiter.record.blocked_by)
    ]
    return min(ready, key=waiter_key, default=None)


def older_waiter_flows(
    admission: dict[str, Any] | None,
    plan_branch: str,
    own: TouchSetOnBase,
    flows: list[InFlightFlow],
    waiters: list[BlockedWaiter],
) -> list[InFlightFlow]:
    record = AdmissionRecord.from_dict(admission)
    # why: blocker_is_clear treats an entry naming this flow as clear only because an admitted flow never defers
    if record is not None and record.state == ADMISSION_ADMITTED:
        return []
    since = record.blocked_since if record is not None and record.blocked else None
    waiter = older_ready_waiter((since or admission_state.utc_now(), plan_branch), own, flows, waiters)
    if waiter is None:
        return []
    since = waiter.record.blocked_since
    label = f"an older waiting flow on branch {waiter.plan_branch} (worktree {waiter.worktree}, blocked since {since})"
    return [InFlightFlow(label, waiter.plan_branch, waiter.declared, worktree=waiter.worktree)]
