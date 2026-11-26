from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sandbox_executor.flow.constants import CI_REGISTRATION_POLL_SECONDS
from sandbox_executor.flow.github.adapter import GitHubAdapter
from sandbox_executor.flow.github.ci import _extract_failed_ci_check_names
from sandbox_executor.flow.github.ci_failure_detail import STEP_LOOKUP_TIMEOUT_SECONDS

MAX_TIMING_RERUNS = 3

RERUN_NOT_DISPATCHED = "the rerun could not be dispatched"

Settled = tuple[dict[str, Any], dict[str, Any]]


def _wait_for_rerun_start(probe: Callable[[], dict[str, Any]], names: list[str], policy: Any, timeout: float) -> None:
    deadline = policy.clock() + timeout
    while set(_extract_failed_ci_check_names(probe())) & set(names):
        remaining = deadline - policy.clock()
        if remaining <= 0:
            return
        policy.sleep(max(0.0, min(CI_REGISTRATION_POLL_SECONDS, remaining)))


def _dispatch_rerun(github: GitHubAdapter, run_id: str) -> bool:
    try:
        return github.rerun_failed(run_id, timeout=STEP_LOOKUP_TIMEOUT_SECONDS)
    except Exception:
        return False


def _retry_timing_noise(
    log: Callable[[str], None],
    record: dict[str, Any],
    ci_status: dict[str, Any],
    eligible: Callable[[dict[str, Any], dict[str, Any]], list[str] | None],
    rerun_once: Callable[[dict[str, Any], list[str]], Settled | None],
) -> dict[str, Any]:
    first_run_ids: list[str] = []
    outcomes: list[str] = []
    reason = ""
    while len(outcomes) < MAX_TIMING_RERUNS:
        run_ids = eligible(record, ci_status)
        if not run_ids:
            break
        attempt = f"attempt {len(outcomes) + 1} of {MAX_TIMING_RERUNS}"
        log(f"Final-head CI: rerunning timing-gate failure on unchanged code ({attempt}): {', '.join(run_ids)}")
        retried = rerun_once(record, run_ids)
        if retried is None:
            reason = RERUN_NOT_DISPATCHED
            break
        record, ci_status = retried
        first_run_ids = first_run_ids or run_ids
        outcomes.append(record["state"])
    if not outcomes:
        return {**record, "timing_rerun": {"attempted": False, "reason": reason}} if reason else record
    summary = {"attempted": True, "run_ids": first_run_ids, "outcome": outcomes[-1]}
    summary.update(attempts=len(outcomes), outcomes=outcomes, **({"reason": reason} if reason else {}))
    return {**record, "timing_rerun": summary}
