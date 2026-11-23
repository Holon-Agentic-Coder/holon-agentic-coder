"""Stage 5's report of CI on the final head: the head calibration pushed, which the reviewers did not vote on.

The check reports; Stage 5 acts on it (:mod:`sandbox_executor.flow.github.ci_reverify`). It waits with the existing CI
wait facility (:mod:`sandbox_executor.flow.github.ci_wait`) on the review CI wait budget, classifies the outcome and
returns a plain record. It reruns a failed job at most once, and only when every failed check is a timing gate and the
commits from the approved head to the final head touch nothing but bookkeeping paths (plans, executions and the
knowledge base); every other failure, and a second timing failure, yields exactly the record it yields today. It never
touches the CI timing limit; merging stays with the human maintainer.

States: ``success``, ``failed`` (any failed check, a timing gate included, because a job over its time limit is a
failed check), ``unknown`` (CI was not measured or was still pending when the budget ran out) and ``skipped``
(nothing was pushed). A record that went through the automatic rerun carries an additive ``timing_rerun`` note for
auditability.
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
from sandbox_executor.flow.github.adapter import GitHubAdapter
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
    parse_run_id,
)
from sandbox_executor.flow.github.ci_reverify import approved_head_of
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy, wait_for_pending_ci
from sandbox_executor.knowledge import CredentialRedactor

MAX_REASON_CHARS = 300

# A head whose new commits name only these path roots changed no code; only such a head earns the one automatic rerun.
BOOKKEEPING_PATH_PREFIXES = ("plans/", "executions/", "holon-knowledge/")


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
    rerun_dispatcher: Callable[[str], bool] | None = None
    changed_paths: Callable[[str, str], list[str] | None] | None = None
    approved_head: Callable[[], str | None] | None = None
    github: GitHubAdapter | None = None  # PR head, CI probes and reruns (not ``detail_lookup``); default over checkout.


@dataclass(frozen=True)
class _FinalHeadCheck:
    """One invocation's fixed arguments, bundled so the rerun helpers stay within the argument limit."""

    context: FlowContext
    repo_dir: str
    branch: str
    seams: FinalHeadSeams
    github: GitHubAdapter

    def default_probe(self) -> Callable[[], dict[str, Any]]:
        """The CI probe of the branch through the check's adapter."""
        proxy_env = dict(self.seams.proxy_env)
        return functools.partial(probe_ci_checks, self.repo_dir, self.branch, proxy_env=proxy_env, github=self.github)


def _git_gh_head(check: _FinalHeadCheck) -> tuple[Callable[[], str | None], ...]:
    env = {**os.environ, **check.seams.proxy_env, "DOCKER_CLI_HINTS": "false"}
    repo_dir = check.repo_dir

    def _local_head() -> str | None:
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "HEAD"],
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

    return _local_head, lambda: check.github.pr_head_sha(check.branch, STEP_LOOKUP_TIMEOUT_SECONDS)


def _wait_for_pr_head(check: _FinalHeadCheck, policy: Any) -> None:
    """Wait until the PR head is the pushed SHA, so checks of the previous head are never taken for the final head.

    When either SHA cannot be read the head is unverifiable and the probe proceeds as before.
    """
    seams = check.seams
    default_pushed, default_pr = _git_gh_head(check)
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


def _settled_status(check: _FinalHeadCheck) -> tuple[dict[str, Any], dict[str, Any] | None]:
    seams, context = check.seams, check.context
    default_probe = check.default_probe()
    probe = seams.probe or default_probe
    policy = seams.policy or CiWaitPolicy(resolve_review_ci_wait_seconds(os.environ))
    _wait_for_pr_head(check, policy)
    ci_status = probe()
    if ci_status.get("source") != "github_cli":
        # A push returns before GitHub registers the new runs, so give them time to appear.
        timeout = resolve_ci_registration_timeout(seams.registration_timeout)
        _wait_for_ci_registration(
            context,
            check.repo_dir,
            check.branch,
            dict(seams.proxy_env),
            timeout,
            CI_REGISTRATION_POLL_SECONDS,
            policy.clock,
            policy.sleep,
            probe=default_probe,
        )
        ci_status = probe()
    if ci_status.get("source") != "github_cli" or ci_status.get("pending", 0) <= 0:
        return ci_status, None
    # A failed check must not end the wait while others are pending: they may fail too, or hide a real test failure.
    ci_status, waited = wait_for_pending_ci(context, probe, ci_status, policy, stop_on_failure=False)
    return ci_status, waited.as_record()


def _classify_settled(check: _FinalHeadCheck) -> tuple[dict[str, Any], dict[str, Any]]:
    """Wait for a settled CI probe and classify it, returning the record and the probe it was built from."""
    ci_status, wait_record = _settled_status(check)
    lookup = check.seams.detail_lookup or gh_detail_lookup(check.repo_dir, check.seams.proxy_env)
    return classify_final_head(ci_status, wait_record, lookup), ci_status


def _git_changed_paths(repo_dir: str, proxy_env: Mapping[str, str]) -> Callable[[str, str], list[str] | None]:
    """A lister of the paths changed from ``approved`` to ``head``; None on any error (fail-closed)."""
    env = {**os.environ, **proxy_env, "DOCKER_CLI_HINTS": "false"}

    def changed(approved: str, head: str) -> list[str] | None:
        try:
            proc = subprocess.run(
                ["git", "diff", "--name-only", f"{approved}..{head}", "--"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=STEP_LOOKUP_TIMEOUT_SECONDS,
                env=env,
            )
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]

    return changed


def _bookkeeping_only(paths: list[str] | None) -> bool:
    """Whether a diff names only plan, execution and knowledge-base files; an unreadable or empty diff is not one."""
    return bool(paths) and all(path.startswith(BOOKKEEPING_PATH_PREFIXES) for path in paths)


def _timing_gate_only(details: list[dict[str, Any]]) -> bool:
    """Whether every failed check is tagged as a timing-gate failure; no details means nothing is known."""
    return bool(details) and all(detail.get("timing_gate") for detail in details)


def _failed_run_ids(ci_status: dict[str, Any], names: list[str]) -> list[str] | None:
    """The Actions run ids behind the failed checks, or None when any failed check has no parsable run link."""
    run_ids: list[str] = []
    for check in _failed_checks_by_name(ci_status, names):
        run_id = parse_run_id(check.get("link"))
        if run_id is None:
            return None
        if run_id not in run_ids:
            run_ids.append(run_id)
    return run_ids


def _resolve_final_head(check: _FinalHeadCheck) -> str | None:
    """The head CI was measured on: the PR head, or the pushed head when the PR head is unreadable."""
    default_pushed, default_pr = _git_gh_head(check)
    return (check.seams.pr_head or default_pr)() or (check.seams.pushed_head or default_pushed)()


def _eligible_run_ids(check: _FinalHeadCheck, record: dict[str, Any], ci_status: dict[str, Any]) -> list[str] | None:
    """Run ids eligible for the one automatic rerun, or None; every undecidable input is ineligible (fail-closed)."""
    if not _timing_gate_only(record["failed_details"]):
        return None
    approved = (check.seams.approved_head or (lambda: approved_head_of(check.context)))()
    if not approved:
        return None
    # Diff against the head the checks ran on, not the workspace HEAD, which may have moved since the push.
    final = _resolve_final_head(check)
    if not final:
        return None
    paths = (check.seams.changed_paths or _git_changed_paths(check.repo_dir, check.seams.proxy_env))(approved, final)
    if not _bookkeeping_only(paths):
        return None
    return _failed_run_ids(ci_status, record["failed_checks"])


def _wait_for_rerun_start(probe: Callable[[], dict[str, Any]], names: list[str], policy: Any, timeout: float) -> None:
    """Poll until none of ``names`` is still reported failed (the reruns registered) or ``timeout`` elapses."""
    deadline = policy.clock() + timeout
    while set(_extract_failed_ci_check_names(probe())) & set(names):
        remaining = deadline - policy.clock()
        if remaining <= 0:
            return
        policy.sleep(max(0.0, min(CI_REGISTRATION_POLL_SECONDS, remaining)))


def _dispatch_rerun(check: _FinalHeadCheck, run_id: str) -> bool:
    """Dispatch one rerun through the check's adapter; any exception is a failed dispatch (fail-closed)."""
    try:
        return check.github.rerun_failed(run_id, timeout=STEP_LOOKUP_TIMEOUT_SECONDS)
    except Exception:
        return False


def _retry_timing_noise(check: _FinalHeadCheck, record: dict[str, Any], ci_status: dict[str, Any]) -> dict[str, Any]:
    """Rerun a timing-gate-only failure on a bookkeeping-only head once and re-classify; anything else is unchanged.

    Called exactly once per :func:`check_final_head_ci` invocation and never loops, so a head gets at most one rerun;
    a second failure returns a ``failed`` record exactly as today, annotated with the ``timing_rerun`` note.
    """
    run_ids = _eligible_run_ids(check, record, ci_status)
    if not run_ids:
        return record
    check.context.log(f"Final-head CI: rerunning timing-gate failure on a bookkeeping-only head: {', '.join(run_ids)}")
    seams = check.seams
    # A failed dispatch (False, including gh missing, a spawn error or any exception) is fail-closed.
    rerun = seams.rerun_dispatcher or functools.partial(_dispatch_rerun, check)
    if not all(rerun(run_id) for run_id in run_ids):
        return {**record, "timing_rerun": {"attempted": False, "reason": "the rerun could not be dispatched"}}
    probe = seams.probe or check.default_probe()
    policy = seams.policy or CiWaitPolicy(resolve_review_ci_wait_seconds(os.environ))
    timeout = resolve_ci_registration_timeout(seams.registration_timeout)
    _wait_for_rerun_start(probe, record["failed_checks"], policy, timeout)
    retried, _ = _classify_settled(check)
    return {**retried, "timing_rerun": {"attempted": True, "run_ids": run_ids, "outcome": retried["state"]}}


def check_final_head_ci(
    context: FlowContext, repo_dir: str, branch: str, seams: FinalHeadSeams | None = None
) -> dict[str, Any]:
    """Wait for CI on the pushed final head and report the outcome; never raises and never changes the stage status."""
    if context.dry_run or context.skip_push:
        return _record("skipped", "dry run" if context.dry_run else "push skipped")
    seams = seams or FinalHeadSeams()
    github = seams.github or GitHubAdapter(repo_dir, dict(seams.proxy_env))
    check = _FinalHeadCheck(context, repo_dir, branch, seams, github)
    try:
        record, ci_status = _classify_settled(check)
        if record["state"] != "failed":
            return record
        return _retry_timing_noise(check, record, ci_status)
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
