"""Stage 3 admission order: the oldest overlapping waiter whose blockers have all cleared is admitted first.

A flow that finds no overlap still defers to a blocked waiter (:class:`flow.in_flight.BlockedWaiter`) whose declared
touch set overlaps its own, whose ``(blocked_since, plan_branch)`` key is strictly smaller than its own (a flow never
refused uses "now", so every waiter is older) and whose ``blocked_by`` entries are all clear: a pull request no longer
open, a local flow neither in flight nor waiting. An entry naming this flow itself is clear unless this flow is
admitted (it is not running while it re-checks); one naming a pull request or another flow of this lineage is never
clear, since discovery does not list them. Every deferral points at a strictly older key, a total order, so the
waits-for graph stays acyclic.
"""

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
from sandbox_executor.touch_set import TouchSet, overlapping_entries


def waiter_key(waiter: BlockedWaiter) -> tuple[str, str]:
    """The order of a waiter: first refusal time, then plan branch (a total order on one host's clock)."""
    return (waiter.record.blocked_since or "", waiter.plan_branch)


def blocker_is_clear(entry: BlockedBy, open_numbers: set[int], busy: set[str], own_plan_branch: str) -> bool:
    """Whether a waiter's ``blocked_by`` entry no longer holds it back; ``busy``: plan branches in flight or waiting."""
    own_root = root_intent(own_plan_branch)
    if entry.kind == BLOCKED_BY_PULL_REQUEST:
        return root_intent(entry.branch or "") != own_root and entry.number not in open_numbers
    plan_branch = entry.plan_branch or ""
    if plan_branch != own_plan_branch and root_intent(plan_branch) == own_root:
        return False
    return plan_branch not in busy


def older_ready_waiter(
    own_key: tuple[str, str],
    own: TouchSet,
    flows: list[InFlightFlow],
    waiters: list[BlockedWaiter],
    own_admitted: bool = False,
) -> BlockedWaiter | None:
    """The oldest overlapping waiter older than ``own_key`` (``(blocked_since or now, plan_branch)``) that is ready."""
    own_plan_branch = own_key[1]
    in_flight = {flow.plan_branch for flow in flows}
    busy = in_flight | {waiter.plan_branch for waiter in waiters} | ({own_plan_branch} if own_admitted else set())
    open_numbers = {flow.number for flow in flows if flow.number is not None}
    ready = [
        waiter
        for waiter in waiters
        if waiter.plan_branch not in in_flight
        and waiter_key(waiter) < own_key
        and waiter.declared is not None
        and overlapping_entries(own, waiter.declared)
        and all(blocker_is_clear(entry, open_numbers, busy, own_plan_branch) for entry in waiter.record.blocked_by)
    ]
    return min(ready, key=waiter_key, default=None)


def older_waiter_flows(
    admission: dict[str, Any] | None,
    plan_branch: str,
    own: TouchSet,
    flows: list[InFlightFlow],
    waiters: list[BlockedWaiter],
) -> list[InFlightFlow]:
    """The older ready waiter this flow (its ``admission`` object and plan branch) defers to, as a local flow."""
    record = AdmissionRecord.from_dict(admission)
    since = record.blocked_since if record is not None and record.blocked else None
    own_admitted = record is not None and record.state == ADMISSION_ADMITTED
    waiter = older_ready_waiter((since or admission_state.utc_now(), plan_branch), own, flows, waiters, own_admitted)
    if waiter is None:
        return []
    since = waiter.record.blocked_since
    label = f"an older waiting flow on branch {waiter.plan_branch} (worktree {waiter.worktree}, blocked since {since})"
    return [InFlightFlow(label, waiter.plan_branch, waiter.declared, worktree=waiter.worktree)]
