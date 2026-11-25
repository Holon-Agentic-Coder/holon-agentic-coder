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

CI_WAIT_POLL_CEILING_SECONDS = 15.0


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


def _is_pending(check: dict[str, Any]) -> bool:
    """True for a check whose bucket is ``pending`` or whose state is queued or running."""
    return str(check.get("bucket") or "").lower() == "pending" or str(check.get("state") or "").upper() in (
        "IN_PROGRESS",
        "QUEUED",
        "PENDING",
    )


def _named_checks(ci_status: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """``(name, check)`` for every named check of a CI probe, in probe order."""
    checks = [c for c in ci_status.get("checks") or [] if isinstance(c, dict)]
    return [(name, c) for c in checks if (name := str(c.get("name") or "").strip())]


def pending_check_names(ci_status: dict[str, Any]) -> tuple[str, ...]:
    """Names of the checks of a CI probe that are still pending."""
    return tuple(dict.fromkeys(name for name, check in _named_checks(ci_status) if _is_pending(check)))


def _check_conclusions(ci_status: dict[str, Any]) -> dict[str, str]:
    """Each named check's conclusion: its bucket (``pass``, ``fail``, ``skipping``, ``cancel``), else its state."""
    return {
        name: str(check.get("bucket") or "").lower() or str(check.get("state") or "").upper() or "unknown"
        for name, check in _named_checks(ci_status)
    }


def is_pending_only(ci_status: dict[str, Any]) -> bool:
    """True when checks are still running and none has failed: not a failure and never ``all_passed``."""
    return ci_status.get("pending", 0) > 0 and ci_status.get("failed", 0) == 0


def _log_transitions(context: FlowContext, before: dict[str, Any], after: dict[str, Any]) -> None:
    """One line per check that left the pending set; nothing when no check changed state."""
    pending_before, pending_after = pending_check_names(before), pending_check_names(after)
    remaining = int(after.get("pending", 0) or 0)
    finished = [name for name in pending_before if name not in pending_after]
    conclusions = _check_conclusions(after)
    for name in finished:
        conclusion = conclusions.get(name, "unknown")
        context.log(f"[Review Loop] CI check '{name}' finished: {conclusion} ({remaining} pending).")
    previous = int(before.get("pending", 0) or 0)
    if not finished and remaining != previous:
        context.log(f"[Review Loop] CI checks pending: {previous} -> {remaining}.")


def _log_summary(context: FlowContext, ci_status: dict[str, Any], result: CiWaitResult) -> None:
    """The one closing line: the final counts (pending ones too), or the pending checks once the budget is spent."""
    if result.exhausted:
        pending = int(ci_status.get("pending", 0) or 0)
        names = ", ".join(result.pending_checks) or "unnamed"
        context.log(
            f"[Review Loop] CI wait budget of {result.budget_seconds:g}s spent: "
            f"{pending} check(s) still pending ({names})."
        )
        return
    passed, failed = int(ci_status.get("passed", 0) or 0), int(ci_status.get("failed", 0) or 0)
    pending = int(ci_status.get("pending", 0) or 0)
    other = max(0, int(ci_status.get("total", 0) or 0) - passed - failed - pending)
    # Pending checks remain only when the wait stopped early on a failure (stop_on_failure).
    ending, still = ("stopped on a failure", f", {pending} still pending") if pending else ("settled", "")
    context.log(
        f"[Review Loop] CI wait {ending} after {result.waited_seconds:g}s: "
        f"{passed} passed, {failed} failed{still}, {other} other."
    )


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
    never a failure. The log carries one start line, one line per check that finishes, nothing for a poll where
    nothing changed, and one closing summary.
    """
    start = policy.clock()
    deadline = start + max(0.0, policy.budget_seconds)
    interval = policy.poll_interval if policy.poll_interval > 0 else CI_REGISTRATION_POLL_SECONDS
    if policy.ceiling > 0:
        interval = min(interval, policy.ceiling)
    exhausted = False
    tracking = ci_status.get("pending", 0) > 0
    if tracking:
        context.log(
            f"[Review Loop] Tracking {int(ci_status.get('total', 0) or 0)} GitHub CI check(s): "
            f"{ci_status['pending']} pending (budget {policy.budget_seconds:g}s)."
        )
    while ci_status.get("pending", 0) > 0 and not (stop_on_failure and ci_status.get("failed", 0) > 0):
        remaining = deadline - policy.clock()
        if remaining <= 0:
            exhausted = True
            break
        policy.sleep(min(interval, remaining))
        interval = next_poll_interval(interval, policy.ceiling)
        previous, ci_status = ci_status, probe()
        _log_transitions(context, previous, ci_status)
    waited = max(0.0, policy.clock() - start)
    result = CiWaitResult(exhausted, waited, policy.budget_seconds, pending_check_names(ci_status))
    if tracking:
        _log_summary(context, ci_status, result)
    return ci_status, result


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
