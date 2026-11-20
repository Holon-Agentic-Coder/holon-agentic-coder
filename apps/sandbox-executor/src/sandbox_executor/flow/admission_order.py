"""Stage 3 admission order: the oldest overlapping waiter whose blockers have all cleared is admitted first.

A flow that finds no overlap still defers to a blocked waiter (:class:`flow.in_flight.BlockedWaiter`) whose declared
touch set overlaps its own, whose ``(blocked_since, plan_branch)`` key is strictly smaller than its own (a flow never
refused uses "now", so every waiter is older) and whose ``blocked_by`` entries are all clear: a pull request no longer
open, a local flow neither in flight nor waiting. An entry naming this flow's own lineage is never clear (the waiter
waits on us). Every deferral points at a strictly older key, a total order, so the waits-for graph stays acyclic.
"""

from __future__ import annotations

from sandbox_executor.flow.admission_state import BLOCKED_BY_PULL_REQUEST, BlockedBy
from sandbox_executor.flow.in_flight import BlockedWaiter, InFlightFlow, _root_intent
from sandbox_executor.touch_set import TouchSet, overlapping_entries


def waiter_key(waiter: BlockedWaiter) -> tuple[str, str]:
    """The order of a waiter: first refusal time, then plan branch (a total order on one host's clock)."""
    return (waiter.record.blocked_since or "", waiter.plan_branch)


def blocker_is_clear(entry: BlockedBy, flows: list[InFlightFlow], waiters: list[BlockedWaiter], own_root: str) -> bool:
    """Whether a waiter's ``blocked_by`` entry no longer holds it back."""
    if _root_intent(entry.branch or entry.plan_branch or "") == own_root:
        return False
    if entry.kind == BLOCKED_BY_PULL_REQUEST:
        return entry.number not in {flow.number for flow in flows if flow.number is not None}
    busy = {flow.plan_branch for flow in flows} | {waiter.plan_branch for waiter in waiters}
    return entry.plan_branch not in busy


def older_ready_waiter(
    own_key: tuple[str, str], own: TouchSet, flows: list[InFlightFlow], waiters: list[BlockedWaiter]
) -> BlockedWaiter | None:
    """The oldest overlapping waiter older than ``own_key`` (``(blocked_since or now, plan_branch)``) that is ready."""
    own_root = _root_intent(own_key[1])
    in_flight = {flow.plan_branch for flow in flows}
    ready = [
        waiter
        for waiter in waiters
        if waiter.plan_branch not in in_flight
        and waiter_key(waiter) < own_key
        and waiter.declared is not None
        and overlapping_entries(own, waiter.declared)
        and all(blocker_is_clear(entry, flows, waiters, own_root) for entry in waiter.record.blocked_by)
    ]
    return min(ready, key=waiter_key, default=None)
