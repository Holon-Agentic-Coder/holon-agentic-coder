"""Stage 5's CI gate on the merged head: no calibration commit is pushed for a head CI has not passed.

The conflict gate may merge the target branch into the approved head, and the head may move after approval for other
reasons. Either way the Stage 4 approval no longer covers the head Stage 5 would calibrate, so the gate waits for every
check run on that exact SHA (:mod:`sandbox_executor.flow.github.ci_head_checks`). States: ``passed``, ``failed`` (a
check failed: Stage 4 restarts), ``pending`` (the CI wait budget ran out), ``unknown`` (nothing registered or the
checks could not be read), ``failed_closed`` (a cancelled run could not be re-triggered or was cancelled again) and
``skipped``. Only ``failed`` restarts Stage 4, routed by failure class
(:mod:`sandbox_executor.flow.review.merged_head_routing`); every other unsettled state fails Stage 5 closed. It never
merges, approves or pushes."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sandbox_executor.budgets import resolve_review_ci_wait_seconds
from sandbox_executor.flow.constants import CI_REGISTRATION_POLL_SECONDS
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.ci import resolve_ci_registration_timeout
from sandbox_executor.flow.github.ci_failure_detail import DetailLookup, parse_run_id
from sandbox_executor.flow.github.ci_head_checks import (
    FAILED,
    PASSED,
    HeadCheck,
    HeadChecksProbe,
    RunRerunner,
    classify_head_checks,
    gh_head_checks_probe,
    is_cancelled,
    is_failed,
    is_pending,
    suite_pending,
)
from sandbox_executor.flow.github.ci_head_reruns import CancelledReruns, gh_run_rerunner
from sandbox_executor.flow.github.ci_slowest_tests import SlowestLookup
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy, next_poll_interval
from sandbox_executor.flow.review.consensus_withdrawal import WithdrawalSeams
from sandbox_executor.knowledge import CredentialRedactor

MAX_REASON_CHARS = 300
RERUN_START_POLL_SECONDS = 5.0


@dataclass(frozen=True)
class MergedHeadSeams:
    """Optional collaborators of the gate; tests inject fakes, the flow uses the ``gh`` and ``git`` defaults."""

    proxy_env: Mapping[str, str] = field(default_factory=dict)
    policy: CiWaitPolicy | None = None
    probe: HeadChecksProbe | None = None
    rerun_dispatcher: RunRerunner | None = None
    registration_timeout: float | None = None
    head_reader: Callable[[], str | None] | None = None
    bookkeeping_move: Callable[[str, str], bool] | None = None
    withdrawal: WithdrawalSeams | None = None
    detail_lookup: DetailLookup | None = None  # The failed-step detail that classifies a red head (``gh`` default).
    slowest_lookup: SlowestLookup | None = None  # The slowest tests a repeated timing failure names (``gh`` default).


def _run_entry(check: Mapping[str, Any]) -> dict[str, Any]:
    conclusion = check.get("status") if is_pending(check) else check.get("conclusion")
    return {
        "run_id": check.get("run_id"),
        "check_run_id": check.get("check_run_id"),
        "name": check.get("name"),
        "conclusion": conclusion or "unknown",
    }


def gate_record(
    state: str, head_sha: str, checks: list[HeadCheck], reruns: list[str], reason: str = ""
) -> dict[str, Any]:
    """The gate's record: state, full head SHA, every run with its conclusion, the reruns and a bounded reason."""
    bounded = CredentialRedactor.redact_text(reason)[:MAX_REASON_CHARS]
    runs = [_run_entry(check) for check in checks]
    return {"state": state, "head_sha": head_sha, "runs": runs, "reruns": list(reruns), "reason": bounded}


def skipped_record(head_sha: str | None, reason: str) -> dict[str, Any]:
    """The record of a gate that did not wait, so the Stage 5 payload always carries the key."""
    return gate_record("skipped", str(head_sha or ""), [], [], reason)


def stage4_ci_record(context: FlowContext, head: str) -> dict[str, Any]:
    """The Stage 4 CI checks Stage 5 relies on when the approved code is unchanged."""
    review = context.stage_results.get("review")
    checks = ((review.payload.get("ci_checks") if review else None) or {}).get("checks") or []
    runs = [
        {
            "run_id": parse_run_id(c.get("link")),
            "check_run_id": None,
            "name": c.get("name"),
            "conclusion": c.get("bucket"),
        }
        for c in checks
        if isinstance(c, dict)
    ]
    reason = "no merge since approval: Stage 5 relies on the Stage 4 CI of the approved head"
    return {"state": "stage4", "head_sha": head, "runs": runs, "reruns": [], "reason": reason}


def _settle(checks: list[HeadCheck], reruns: CancelledReruns) -> tuple[str, str] | None:
    """The final state and reason once the checks settle, or None to keep waiting (a cancelled run re-triggered)."""
    state = classify_head_checks(checks)
    if state in (FAILED, PASSED):
        return state, ""
    error = reruns.retrigger(checks)
    return ("failed_closed", error) if error else None


def _poll_head(context: FlowContext, repo_dir: str, head_sha: str, seams: MergedHeadSeams) -> dict[str, Any]:
    policy = seams.policy or CiWaitPolicy(resolve_review_ci_wait_seconds(os.environ))
    probe = seams.probe or gh_head_checks_probe(repo_dir, seams.proxy_env)
    reruns = CancelledReruns(seams.rerun_dispatcher or gh_run_rerunner(repo_dir, seams.proxy_env), context.log)
    registration = policy.clock() + resolve_ci_registration_timeout(seams.registration_timeout)
    deadline = registration + max(0.0, policy.budget_seconds)
    interval = policy.poll_interval if policy.poll_interval > 0 else CI_REGISTRATION_POLL_SECONDS
    if policy.ceiling > 0:
        interval = min(interval, policy.ceiling)
    while True:
        probed = probe(head_sha)
        checks = probed or []
        settled = _settle(checks, reruns) if checks else None
        if settled is not None:
            return gate_record(settled[0], head_sha, checks, reruns.run_ids, settled[1])
        now = policy.clock()
        if probed is None and now >= deadline:
            return gate_record("unknown", head_sha, [], reruns.run_ids, "the checks on the head could not be read")
        if probed is not None and not checks and now >= registration:
            return gate_record("unknown", head_sha, [], reruns.run_ids, "no check run on the head could be read")
        if probed is not None and now >= deadline:
            names = ", ".join(
                str(c.get("name")) for c in checks if is_pending(c) or is_cancelled(c) or suite_pending(c)
            )
            reason = f"checks still unsettled when the CI wait budget ran out: {names}"
            return gate_record("pending", head_sha, checks, reruns.run_ids, reason)
        until = registration if probed is not None and not checks else deadline  # A failed probe keeps polling.
        policy.sleep(max(0.0, min(interval, until - now)))
        interval = next_poll_interval(interval, policy.ceiling)


def gate_merged_head_ci(
    context: FlowContext, repo_dir: str, head_sha: str, seams: MergedHeadSeams | None = None
) -> dict[str, Any]:
    """Wait for every check run on exactly ``head_sha`` and report it; never raises (an error is ``unknown``)."""
    try:
        return _poll_head(context, repo_dir, head_sha, seams or MergedHeadSeams())
    except Exception as exc:
        return gate_record("unknown", head_sha, [], [], str(exc))


def format_merged_head_ci(record: Mapping[str, Any]) -> str:
    """One log line naming the full head SHA, every run id and conclusion, the reruns and the reason."""
    runs = ", ".join(
        f"{run.get('name')} (run {run.get('run_id') or 'n/a'}, check run {run.get('check_run_id')}): "
        f"{run.get('conclusion')}"
        for run in record.get("runs") or []
    )
    line = f"Merged-head CI on {record.get('head_sha') or 'unknown head'}: {record.get('state')}"
    line += f"; runs: {runs}" if runs else "; no runs"
    if record.get("reruns"):
        line += f"; re-triggered: {', '.join(record['reruns'])}"
    return line + (f" ({record['reason']})" if record.get("reason") else "")


def _wait_for_rerun_start(context: FlowContext, head: str, names: set[str], seams: MergedHeadSeams) -> None:
    """Poll until no rerun check is still reported failed on ``head``, at most the registration timeout."""
    probe = seams.probe or gh_head_checks_probe(context.target_worktree, seams.proxy_env)
    policy = seams.policy or CiWaitPolicy(resolve_review_ci_wait_seconds(os.environ))
    deadline = policy.clock() + resolve_ci_registration_timeout(seams.registration_timeout)
    while any(is_failed(c) and c.get("name") in names for c in probe(head) or []):
        remaining = deadline - policy.clock()
        if remaining <= 0:
            return
        policy.sleep(max(0.0, min(RERUN_START_POLL_SECONDS, remaining)))
