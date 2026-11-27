from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sandbox_executor.flow.github.ci import _ci_green, _extract_failed_ci_check_names
from sandbox_executor.flow.github.ci_wait import is_pending_only, pending_check_names
from sandbox_executor.flow.review.ci_failure_routing import CLASS_LABELS

NO_BLOCKER_RECORDED = "Autonomous review consensus failed: no specific blocker was recorded."


def gate_ci_flags(ci_status: dict[str, Any], tests_failed: bool) -> tuple[bool | None, bool]:
    pending_only = is_pending_only(ci_status) and not tests_failed
    finding = (not ci_status.get("all_passed", False) and not pending_only) or tests_failed
    return (None if pending_only else _ci_green(ci_status, tests_failed)), finding


def _routed(routing: Mapping[str, Any] | None) -> str:
    label, reruns = CLASS_LABELS.get(str((routing or {}).get("classification"))), (routing or {}).get("reruns")
    if not label or not isinstance(reruns, int) or reruns < 1:
        return ""
    return f"CI noise ({label}) still red after {reruns} rerun{'s' if reruns > 1 else ''}: "


def describe_ci_outcome(
    ci_status: dict[str, Any],
    ci_wait: dict[str, Any] | None,
    tests_failed: bool,
    *,
    routing: Mapping[str, Any] | None = None,
) -> str:
    failed = _extract_failed_ci_check_names(ci_status, tests_failed)
    if not failed and ci_status.get("failed", 0) > 0:
        failed = [f"{ci_status['failed']} unnamed check(s)"]
    if failed:
        if tests_failed and failed == ["local_tests"]:
            return "Autonomous review consensus failed: test suite reported failures."
        return f"Autonomous review consensus failed: CI failed: {_routed(routing)}{', '.join(failed)}."
    if not (ci_wait and ci_wait.get("state") == "pending") and is_pending_only(ci_status):
        # why: pending that no wait explains stays pending, never "no blocker"
        names = ", ".join(pending_check_names(ci_status)) or "checks"
        return f"Autonomous review stopped: CI still pending and not waited for: {names} still running; no test failed."
    if ci_wait and ci_wait.get("state") == "pending":
        names = ", ".join(ci_wait.get("pending_checks") or []) or "checks"
        return (
            f"Autonomous review stopped: CI still pending after the wait budget: {names} still running after "
            f"{ci_wait.get('budget_seconds', 0):g} s (budget used {ci_wait.get('waited_seconds', 0):g} s); "
            "no test failed."
        )
    return NO_BLOCKER_RECORDED
