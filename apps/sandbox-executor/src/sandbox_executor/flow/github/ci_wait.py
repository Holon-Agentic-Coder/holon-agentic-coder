"""The post-review wait for pending GitHub CI checks, on its own wall-clock budget with a bounded back-off.

Waiting for CI and judging code have different clocks: the wait never draws on the review iteration budget, it has
``HOLON_REVIEW_CI_WAIT_SECONDS`` (see :func:`sandbox_executor.budgets.resolve_review_ci_wait_seconds`). Clock and sleep
are injected so the wait is tested without sleeping.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sandbox_executor.flow.constants import CI_REGISTRATION_POLL_SECONDS
from sandbox_executor.flow.context import FlowContext

CI_WAIT_POLL_CEILING_SECONDS = 120.0


def next_poll_interval(current: float, ceiling: float = CI_WAIT_POLL_CEILING_SECONDS) -> float:
    """Double ``current``, never beyond ``ceiling``."""
    return min(current * 2, ceiling)


@dataclass(frozen=True)
class CiWaitPolicy:
    """The CI wait budget, its back-off (``poll_interval`` doubling up to ``ceiling``) and the injected time sources."""

    budget_seconds: float
    poll_interval: float = CI_REGISTRATION_POLL_SECONDS
    ceiling: float = CI_WAIT_POLL_CEILING_SECONDS
    clock: Callable[[], float] = field(default=time.monotonic)
    sleep: Callable[[float], None] = field(default=time.sleep)


@dataclass(frozen=True)
class CiWaitResult:
    """How one CI wait ended: ``exhausted`` when the budget ran out while checks were still pending."""

    exhausted: bool
    waited_seconds: float
    budget_seconds: float
    pending_checks: tuple[str, ...]

    def as_record(self) -> dict[str, Any]:
        """The ``ci_wait`` entry of the review loop result."""
        return {
            "state": "pending" if self.exhausted else "settled",
            "budget_seconds": self.budget_seconds,
            "waited_seconds": round(self.waited_seconds, 3),
            "pending_checks": list(self.pending_checks),
        }


def pending_check_names(ci_status: dict[str, Any]) -> tuple[str, ...]:
    """Names of the checks of a CI probe that are still pending."""
    names: list[str] = []
    for check in ci_status.get("checks") or []:
        if not isinstance(check, dict):
            continue
        pending = str(check.get("bucket") or "").lower() == "pending" or str(check.get("state") or "").upper() in (
            "IN_PROGRESS",
            "QUEUED",
            "PENDING",
        )
        name = str(check.get("name") or "").strip()
        if pending and name and name not in names:
            names.append(name)
    return tuple(names)


def is_pending_only(ci_status: dict[str, Any]) -> bool:
    """True when checks are still running and none has failed: not a failure and never ``all_passed``."""
    return ci_status.get("pending", 0) > 0 and ci_status.get("failed", 0) == 0


def wait_for_pending_ci(
    context: FlowContext,
    probe: Callable[[], dict[str, Any]],
    ci_status: dict[str, Any],
    policy: CiWaitPolicy,
    stop_on_failure: bool = True,
) -> tuple[dict[str, Any], CiWaitResult]:
    """Re-probe CI while checks are pending, for at most ``policy.budget_seconds`` of wall-clock time.

    Polls back off from ``poll_interval``, doubling up to the ceiling, and the wait ends as soon as nothing is pending
    or, with ``stop_on_failure``, any check has failed (the outcome is then already a CI failure, so waiting on the
    rest buys nothing). Checks still pending when the budget is spent are returned as they are: never ``all_passed``,
    never a failure.
    """
    start = policy.clock()
    deadline = start + max(0.0, policy.budget_seconds)
    interval = policy.poll_interval if policy.poll_interval > 0 else CI_REGISTRATION_POLL_SECONDS
    exhausted = False
    while ci_status.get("pending", 0) > 0 and not (stop_on_failure and ci_status.get("failed", 0) > 0):
        remaining = deadline - policy.clock()
        if remaining <= 0:
            exhausted = True
            context.log(f"[Review Loop] {ci_status['pending']} GitHub CI check(s) still pending after the CI wait.")
            break
        context.log(f"[Review Loop] Waiting for {ci_status['pending']} pending GitHub CI check(s)...")
        policy.sleep(min(interval, remaining))
        interval = next_poll_interval(interval, policy.ceiling)
        ci_status = probe()
    waited = max(0.0, policy.clock() - start)
    return ci_status, CiWaitResult(exhausted, waited, policy.budget_seconds, pending_check_names(ci_status))


def settle_pending_ci(
    context: FlowContext,
    probe: Callable[[], dict[str, Any]],
    ci_status: dict[str, Any],
    policy: CiWaitPolicy,
    spent: bool,
) -> tuple[dict[str, Any], CiWaitResult | None]:
    """Wait out pending GitHub checks unless the budget was already spent; the result is None when no wait ran."""
    if spent or ci_status.get("source") != "github_cli" or ci_status.get("pending", 0) <= 0:
        return ci_status, None
    return wait_for_pending_ci(context, probe, ci_status, policy)
