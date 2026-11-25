from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sandbox_executor.flow.constants import CI_REGISTRATION_POLL_SECONDS
from sandbox_executor.flow.context import FlowContext

CI_WAIT_POLL_CEILING_SECONDS = 15.0
_PENDING_BUCKET = "pending"
_PENDING_CHECK_STATES = ("IN_PROGRESS", "QUEUED", "PENDING")


def next_poll_interval(current: float, ceiling: float = CI_WAIT_POLL_CEILING_SECONDS) -> float:
    return min(current * 2, ceiling)


@dataclass(frozen=True)
class CiWaitPolicy:
    budget_seconds: float
    poll_interval: float = CI_REGISTRATION_POLL_SECONDS
    ceiling: float = CI_WAIT_POLL_CEILING_SECONDS
    clock: Callable[[], float] = field(default=time.monotonic)
    sleep: Callable[[float], None] = field(default=time.sleep)


@dataclass(frozen=True)
class CiWaitResult:
    exhausted: bool
    waited_seconds: float
    budget_seconds: float
    pending_checks: tuple[str, ...]

    def as_record(self) -> dict[str, Any]:
        return {
            "state": "pending" if self.exhausted else "settled",
            "budget_seconds": self.budget_seconds,
            "waited_seconds": round(self.waited_seconds, 3),
            "pending_checks": list(self.pending_checks),
        }


def _is_pending(check: dict[str, Any]) -> bool:
    return (
        str(check.get("bucket") or "").lower() == _PENDING_BUCKET
        or str(check.get("state") or "").upper() in _PENDING_CHECK_STATES
    )


def _named_checks(ci_status: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    checks = [c for c in ci_status.get("checks") or [] if isinstance(c, dict)]
    return [(name, c) for c in checks if (name := str(c.get("name") or "").strip())]


def pending_check_names(ci_status: dict[str, Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(name for name, check in _named_checks(ci_status) if _is_pending(check)))


def _check_conclusions(ci_status: dict[str, Any]) -> dict[str, str]:
    return {
        name: str(check.get("bucket") or "").lower() or str(check.get("state") or "").upper() or "unknown"
        for name, check in _named_checks(ci_status)
    }


def is_pending_only(ci_status: dict[str, Any]) -> bool:
    return ci_status.get("pending", 0) > 0 and ci_status.get("failed", 0) == 0


def _initial_poll_interval(policy: CiWaitPolicy) -> float:
    interval = policy.poll_interval if policy.poll_interval > 0 else CI_REGISTRATION_POLL_SECONDS
    return min(interval, policy.ceiling) if policy.ceiling > 0 else interval


def _should_continue_waiting(ci_status: dict[str, Any], stop_on_failure: bool) -> bool:
    return ci_status.get("pending", 0) > 0 and not (stop_on_failure and ci_status.get("failed", 0) > 0)


def _log_transitions(context: FlowContext, before: dict[str, Any], after: dict[str, Any]) -> None:
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
    stopped_early_on_failure = pending > 0
    ending, still = (
        ("stopped on a failure", f", {pending} still pending") if stopped_early_on_failure else ("settled", "")
    )
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
    start = policy.clock()
    deadline = start + max(0.0, policy.budget_seconds)
    interval = _initial_poll_interval(policy)
    exhausted = False
    tracking = ci_status.get("pending", 0) > 0
    if tracking:
        context.log(
            f"[Review Loop] Tracking {int(ci_status.get('total', 0) or 0)} GitHub CI check(s): "
            f"{ci_status['pending']} pending (budget {policy.budget_seconds:g}s)."
        )
    while _should_continue_waiting(ci_status, stop_on_failure):
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
    if spent or ci_status.get("source") != "github_cli" or ci_status.get("pending", 0) <= 0:
        return ci_status, None
    return wait_for_pending_ci(context, probe, ci_status, policy)
