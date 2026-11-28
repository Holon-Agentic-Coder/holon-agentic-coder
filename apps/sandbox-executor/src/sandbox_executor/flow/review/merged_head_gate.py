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
from sandbox_executor.flow.review.main_baseline_guard import BaselineGuardSeams
from sandbox_executor.knowledge import CredentialRedactor

MAX_REASON_CHARS = 300
RERUN_START_POLL_SECONDS = 5.0


@dataclass(frozen=True)
class MergedHeadSeams:
    proxy_env: Mapping[str, str] = field(default_factory=dict)
    policy: CiWaitPolicy | None = None
    probe: HeadChecksProbe | None = None
    rerun_dispatcher: RunRerunner | None = None
    registration_timeout: float | None = None
    head_reader: Callable[[], str | None] | None = None
    bookkeeping_move: Callable[[str, str], bool] | None = None
    withdrawal: WithdrawalSeams | None = None
    detail_lookup: DetailLookup | None = None
    slowest_lookup: SlowestLookup | None = None
    baseline_guard: BaselineGuardSeams | None = None


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
    bounded = CredentialRedactor.redact_text(reason)[:MAX_REASON_CHARS]
    runs = [_run_entry(check) for check in checks]
    return {"state": state, "head_sha": head_sha, "runs": runs, "reruns": list(reruns), "reason": bounded}


# why: a gate that did not wait still records itself, so the Stage 5 payload always carries the key.
def skipped_record(head_sha: str | None, reason: str) -> dict[str, Any]:
    return gate_record("skipped", str(head_sha or ""), [], [], reason)


def stage4_ci_record(context: FlowContext, head: str) -> dict[str, Any]:
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
        # why: a failed probe keeps polling until the CI wait deadline, not the registration timeout.
        until = registration if probed is not None and not checks else deadline
        policy.sleep(max(0.0, min(interval, until - now)))
        interval = next_poll_interval(interval, policy.ceiling)


# why: Stage 4's approval no longer covers a merged or moved head; an error is ``unknown`` and fails Stage 5 closed.
def gate_merged_head_ci(
    context: FlowContext, repo_dir: str, head_sha: str, seams: MergedHeadSeams | None = None
) -> dict[str, Any]:
    try:
        return _poll_head(context, repo_dir, head_sha, seams or MergedHeadSeams())
    except Exception as exc:
        return gate_record("unknown", head_sha, [], [], str(exc))


def format_merged_head_ci(record: Mapping[str, Any]) -> str:
    runs = ", ".join(
        f"{run.get('name')} (run {run.get('run_id') or 'n/a'}, check run {run.get('check_run_id')}): "
        f"{run.get('conclusion')}"
        for run in record.get("runs") or []
    )
    line = f"Merged-head CI on {record.get('head_sha') or 'unknown head'}: {record.get('state')}"
    line += f"; runs: {runs}" if runs else "; no runs"
    if record.get("reruns"):
        line += f"; re-triggered: {', '.join(record['reruns'])}"
    if record.get("blocked_on"):
        line += f"; blocked_on: {record['blocked_on']}"
    return line + (f" ({record['reason']})" if record.get("reason") else "")


def _wait_for_rerun_start(context: FlowContext, head: str, names: set[str], seams: MergedHeadSeams) -> None:
    probe = seams.probe or gh_head_checks_probe(context.target_worktree, seams.proxy_env)
    policy = seams.policy or CiWaitPolicy(resolve_review_ci_wait_seconds(os.environ))
    deadline = policy.clock() + resolve_ci_registration_timeout(seams.registration_timeout)
    while any(is_failed(c) and c.get("name") in names for c in probe(head) or []):
        remaining = deadline - policy.clock()
        if remaining <= 0:
            return
        policy.sleep(max(0.0, min(RERUN_START_POLL_SECONDS, remaining)))
