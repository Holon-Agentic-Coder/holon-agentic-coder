"""Stage 5's report of CI on the final head: the head calibration pushed, which the reviewers did not vote on.

The check reports; Stage 5 acts on it (:mod:`sandbox_executor.flow.github.ci_reverify`). It waits with the existing CI
wait facility (:mod:`sandbox_executor.flow.github.ci_wait`) on the review CI wait budget, classifies the outcome and
returns a plain record. It never reruns CI and never touches the CI timing limit; merging stays with the human
maintainer.

States: ``success``, ``failed`` (any failed check, a timing gate included, because a job over its time limit is a
failed check), ``unknown`` (CI was not measured or was still pending when the budget ran out) and ``skipped``
(nothing was pushed).
"""

from __future__ import annotations

import functools
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sandbox_executor.budgets import resolve_review_ci_wait_seconds
from sandbox_executor.flow.constants import CI_REGISTRATION_POLL_SECONDS
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.ci import (
    _extract_failed_ci_check_names,
    _wait_for_ci_registration,
    probe_ci_checks,
    resolve_ci_registration_timeout,
)
from sandbox_executor.flow.github.ci_failure_detail import (
    MAX_STEP_LOOKUPS,
    STEP_LOOKUP_TIMEOUT_SECONDS,
    DetailLookup,
    bare_detail,
    describe_failure,
    gh_detail_lookup,
)
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy, wait_for_pending_ci
from sandbox_executor.knowledge import CredentialRedactor

MAX_REASON_CHARS = 300


@dataclass(frozen=True)
class FinalHeadSeams:
    """Optional collaborators of :func:`check_final_head_ci`; tests inject fakes, the flow uses the defaults."""

    proxy_env: Mapping[str, str] = field(default_factory=dict)
    policy: CiWaitPolicy | None = None
    probe: Callable[[], dict[str, Any]] | None = None
    detail_lookup: DetailLookup | None = None
    registration_timeout: float | None = None
    pushed_head: Callable[[], str | None] | None = None
    pr_head: Callable[[], str | None] | None = None


def _git_gh_head(repo_dir: str, branch: str, proxy_env: Mapping[str, str]) -> tuple[Callable[[], str | None], ...]:
    env = {**os.environ, **proxy_env, "DOCKER_CLI_HINTS": "false"}

    def _out(cmd: list[str]) -> str | None:
        try:
            proc = subprocess.run(
                cmd,
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=STEP_LOOKUP_TIMEOUT_SECONDS,
                env=env,
            )
        except Exception:
            return None
        return proc.stdout.strip() or None if proc.returncode == 0 else None

    return (
        lambda: _out(["git", "rev-parse", "HEAD"]),
        lambda: _out(["gh", "pr", "view", branch, "--json", "headRefOid", "--jq", ".headRefOid"]),
    )


def _wait_for_pr_head(
    branch: str, repo_dir: str, proxy_env: Mapping[str, str], seams: FinalHeadSeams, policy: Any
) -> None:
    """Wait until the PR head is the pushed SHA, so checks of the previous head are never taken for the final head.

    When either SHA cannot be read the head is unverifiable and the probe proceeds as before.
    """
    default_pushed, default_pr = _git_gh_head(repo_dir, branch, proxy_env)
    pushed = (seams.pushed_head or default_pushed)()
    if not pushed:
        return
    pr_head = seams.pr_head or default_pr
    deadline = policy.clock() + resolve_ci_registration_timeout(seams.registration_timeout)
    while True:
        current = pr_head()
        if current is None or current == pushed:
            return
        remaining = deadline - policy.clock()
        if remaining <= 0:
            raise RuntimeError("the PR head did not match the pushed head before the registration wait ran out")
        policy.sleep(max(0.0, min(CI_REGISTRATION_POLL_SECONDS, remaining)))


def _failed_checks_by_name(ci_status: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
    checks = [c for c in ci_status.get("checks") or [] if isinstance(c, dict)]
    return [next((c for c in checks if c.get("name") == n), {"name": n}) for n in names]


def _record(state: str, reason: str = "", **extra: Any) -> dict[str, Any]:
    return {"state": state, "failed_checks": [], "failed_details": [], "ci_wait": None, "reason": reason, **extra}


def not_pushed_record() -> dict[str, Any]:
    """The record of a stage that pushed nothing, so the key is always present."""
    return _record("skipped", "nothing pushed")


def _failed_details(ci_status: dict[str, Any], names: list[str], detail_lookup: DetailLookup) -> list[dict[str, Any]]:
    checks = _failed_checks_by_name(ci_status, names)
    looked_up = [detail_lookup(c) if i < MAX_STEP_LOOKUPS else None for i, c in enumerate(checks)]
    return [d or bare_detail(c["name"]) for c, d in zip(checks, looked_up, strict=True)]


def classify_final_head(
    ci_status: dict[str, Any], wait_record: dict[str, Any] | None, detail_lookup: DetailLookup
) -> dict[str, Any]:
    """Turn a settled CI probe into the final-head record; any failed check, a timing gate included, is ``failed``."""
    if ci_status.get("source") != "github_cli":
        return _record("unknown", "CI checks were not measured on GitHub.", ci_wait=wait_record)
    names = _extract_failed_ci_check_names(ci_status)
    if names:
        details = _failed_details(ci_status, names, detail_lookup)
        return _record("failed", failed_checks=names, failed_details=details, ci_wait=wait_record)
    if ci_status.get("pending", 0) > 0:
        pending = ", ".join((wait_record or {}).get("pending_checks") or [])
        reason = f"CI checks were still pending when the wait budget ran out: {pending}".rstrip(": ")
        return _record("unknown", reason, ci_wait=wait_record)
    if ci_status.get("all_passed") is True:
        return _record("success", ci_wait=wait_record)
    return _record("unknown", "CI reported no checks.", ci_wait=wait_record)


def _settled_status(
    context: FlowContext, repo_dir: str, branch: str, seams: FinalHeadSeams
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    proxy_env = dict(seams.proxy_env)
    probe = seams.probe or functools.partial(probe_ci_checks, repo_dir, branch, proxy_env=proxy_env)
    policy = seams.policy or CiWaitPolicy(resolve_review_ci_wait_seconds(os.environ))
    _wait_for_pr_head(branch, repo_dir, proxy_env, seams, policy)
    ci_status = probe()
    if ci_status.get("source") != "github_cli":
        # A push returns before GitHub registers the new runs, so give them time to appear.
        timeout = resolve_ci_registration_timeout(seams.registration_timeout)
        _wait_for_ci_registration(
            context, repo_dir, branch, proxy_env, timeout, CI_REGISTRATION_POLL_SECONDS, policy.clock, policy.sleep
        )
        ci_status = probe()
    if ci_status.get("source") != "github_cli" or ci_status.get("pending", 0) <= 0:
        return ci_status, None
    # A failed check must not end the wait while others are pending: they may fail too, or hide a real test failure.
    ci_status, waited = wait_for_pending_ci(context, probe, ci_status, policy, stop_on_failure=False)
    return ci_status, waited.as_record()


def check_final_head_ci(
    context: FlowContext, repo_dir: str, branch: str, seams: FinalHeadSeams | None = None
) -> dict[str, Any]:
    """Wait for CI on the pushed final head and report the outcome; never raises and never changes the stage status."""
    if context.dry_run or context.skip_push:
        return _record("skipped", "dry run" if context.dry_run else "push skipped")
    seams = seams or FinalHeadSeams()
    try:
        ci_status, wait_record = _settled_status(context, repo_dir, branch, seams)
        lookup = seams.detail_lookup or gh_detail_lookup(repo_dir, seams.proxy_env)
        return classify_final_head(ci_status, wait_record, lookup)
    except Exception as exc:
        return _record("unknown", CredentialRedactor.redact_text(str(exc))[:MAX_REASON_CHARS])


def format_final_head_ci(record: Mapping[str, Any] | None) -> str:
    """One summary line for the stage log and the flow summary."""
    state = (record or {}).get("state", "skipped")
    reason = (record or {}).get("reason") or ""
    if state == "success":
        return "final head CI: success"
    if state == "failed":
        details = record.get("failed_details") or [bare_detail(n) for n in record["failed_checks"]]  # type: ignore[index]
        return f"final head CI: failed: {', '.join(describe_failure(d) for d in details)}"
    return f"final head CI: {state}" + (f" ({reason})" if reason else "")
