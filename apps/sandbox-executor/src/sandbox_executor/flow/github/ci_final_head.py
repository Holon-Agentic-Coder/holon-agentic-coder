"""Stage 5's report of CI on the final head: the head calibration pushed, which the reviewers did not vote on.

The check only reports. It waits with the existing CI wait facility (:mod:`sandbox_executor.flow.github.ci_wait`) on
the review CI wait budget, classifies the outcome and returns a plain record. It never fails calibration, never
reruns CI and never touches the CI timing limit; merging stays with the human maintainer.

States: ``success``, ``failed``, ``timing_gate`` (every failed job failed only in its timing gate step), ``unknown``
(CI was not measured or was still pending when the budget ran out) and ``skipped`` (nothing was pushed).
"""

from __future__ import annotations

import functools
import json
import os
import re
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
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy, settle_pending_ci
from sandbox_executor.knowledge import CredentialRedactor

TIMING_GATE_STEP_PREFIX = "CI Timing Gate"
STEP_LOOKUP_TIMEOUT_SECONDS = 30.0
MAX_STEP_LOOKUPS = 5
MAX_REASON_CHARS = 300
_RUN_ID_PATTERN = re.compile(r"/actions/runs/(\d+)")

StepLookup = Callable[[dict[str, Any]], "list[str] | None"]


@dataclass(frozen=True)
class FinalHeadSeams:
    """Optional collaborators of :func:`check_final_head_ci`; tests inject fakes, the flow uses the defaults."""

    proxy_env: Mapping[str, str] = field(default_factory=dict)
    policy: CiWaitPolicy | None = None
    probe: Callable[[], dict[str, Any]] | None = None
    step_lookup: StepLookup | None = None
    registration_timeout: float | None = None


def parse_run_id(link: str | None) -> str | None:
    """The Actions run id in a check link, or None when the link is not an Actions run URL."""
    match = _RUN_ID_PATTERN.search(str(link or ""))
    return match.group(1) if match else None


def gh_step_lookup(repo_dir: str, proxy_env: Mapping[str, str], run: Callable[..., Any] = subprocess.run) -> StepLookup:
    """A lookup of the failed step names of a failed check through ``gh run view``; every error yields None."""
    env = {**os.environ, **proxy_env, "DOCKER_CLI_HINTS": "false"}

    def lookup(check: dict[str, Any]) -> list[str] | None:
        run_id = parse_run_id(check.get("link"))
        if run_id is None:
            return None
        try:
            proc = run(
                ["gh", "run", "view", run_id, "--json", "jobs"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=STEP_LOOKUP_TIMEOUT_SECONDS,
                env=env,
            )
            if proc.returncode != 0:
                return None
            for job in json.loads(proc.stdout).get("jobs") or []:
                if job.get("name") == check.get("name"):
                    steps = job.get("steps") or []
                    return [str(s.get("name")) for s in steps if str(s.get("conclusion")).lower() == "failure"]
        except Exception:
            return None
        return None

    return lookup


def _failed_checks_by_name(ci_status: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
    checks = [c for c in ci_status.get("checks") or [] if isinstance(c, dict)]
    return [next((c for c in checks if c.get("name") == n), {"name": n}) for n in names]


def _timing_gate_only(failed_steps: list[str] | None) -> bool:
    return bool(failed_steps) and all(s.startswith(TIMING_GATE_STEP_PREFIX) for s in failed_steps or [])


def _record(state: str, reason: str = "", **extra: Any) -> dict[str, Any]:
    return {"state": state, "failed_checks": [], "timing_gate_checks": [], "ci_wait": None, "reason": reason, **extra}


def not_pushed_record() -> dict[str, Any]:
    """The record of a stage that pushed nothing, so the key is always present."""
    return _record("skipped", "nothing pushed")


def classify_final_head(
    ci_status: dict[str, Any], wait_record: dict[str, Any] | None, step_lookup: StepLookup
) -> dict[str, Any]:
    """Turn a settled CI probe into the final-head record; unknown steps always count as a plain failure."""
    if ci_status.get("source") != "github_cli":
        return _record("unknown", "CI checks were not measured on GitHub.", ci_wait=wait_record)
    names = _extract_failed_ci_check_names(ci_status)
    if names:
        failed = _failed_checks_by_name(ci_status, names)
        steps = [step_lookup(c) for c in failed[:MAX_STEP_LOOKUPS]]
        if len(failed) <= MAX_STEP_LOOKUPS and all(_timing_gate_only(s) for s in steps):
            return _record("timing_gate", timing_gate_checks=names, ci_wait=wait_record)
        return _record("failed", failed_checks=names, ci_wait=wait_record)
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
    ci_status = probe()
    if ci_status.get("source") != "github_cli":
        # A push returns before GitHub registers the new runs, so give them time to appear.
        timeout = resolve_ci_registration_timeout(seams.registration_timeout)
        _wait_for_ci_registration(
            context, repo_dir, branch, proxy_env, timeout, CI_REGISTRATION_POLL_SECONDS, policy.clock, policy.sleep
        )
        ci_status = probe()
    ci_status, waited = settle_pending_ci(context, probe, ci_status, policy, False)
    return ci_status, waited.as_record() if waited else None


def check_final_head_ci(
    context: FlowContext, repo_dir: str, branch: str, seams: FinalHeadSeams | None = None
) -> dict[str, Any]:
    """Wait for CI on the pushed final head and report the outcome; never raises and never changes the stage status."""
    if context.dry_run or context.skip_push:
        return _record("skipped", "dry run" if context.dry_run else "push skipped")
    seams = seams or FinalHeadSeams()
    try:
        ci_status, wait_record = _settled_status(context, repo_dir, branch, seams)
        lookup = seams.step_lookup or gh_step_lookup(repo_dir, seams.proxy_env)
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
        return f"final head CI: failed: {', '.join(record['failed_checks'])}"  # type: ignore[index]
    if state == "timing_gate":
        return f"final head CI: timing gate only: {', '.join(record['timing_gate_checks'])}"  # type: ignore[index]
    return f"final head CI: {state}" + (f" ({reason})" if reason else "")
