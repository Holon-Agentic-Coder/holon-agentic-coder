from __future__ import annotations

import functools
import json
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
from sandbox_executor.flow.github.ci_head_checks import (
    CANCELLED_CONCLUSIONS,
    CHECK_RUNS_JQ,
    PASSING_CONCLUSIONS,
    HeadCheck,
    is_pending,
    latest_checks_for_head,
    parse_check_runs,
)
from sandbox_executor.flow.github.ci_policy import summarise_ci_checks
from sandbox_executor.flow.github.ci_reverify import approved_head_of
from sandbox_executor.flow.github.ci_timing_rerun import _dispatch_rerun, _retry_timing_noise, _wait_for_rerun_start
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy, wait_for_pending_ci
from sandbox_executor.knowledge import CredentialRedactor

MAX_REASON_CHARS = 300

BOOKKEEPING_PATH_PREFIXES = ("plans/", "executions/", "holon-knowledge/")

PR_HEAD_LAGGED_REASON = "pull request head lagged the pushed head"
NO_CHECKS_REASON = "no checks registered for the pushed head"
LOOKUP_FAILED_REASON = "check-runs lookup failed for the pushed head"
_UNREGISTERED = {"source": "unregistered", "total": 0, "pending": 0, "checks": []}

Probe = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class FinalHeadSeams:
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
    github: GitHubAdapter | None = None


@dataclass(frozen=True)
class _FinalHeadCheck:
    context: FlowContext
    repo_dir: str
    branch: str
    seams: FinalHeadSeams
    github: GitHubAdapter

    def default_probe(self) -> Callable[[], dict[str, Any]]:
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


def _pr_bucket(head_check: HeadCheck) -> tuple[str, str]:
    conclusion = head_check.get("conclusion")
    if is_pending(head_check):
        return "pending", "PENDING"
    if conclusion == "skipped":
        return "skipping", "SKIPPED"
    if conclusion in PASSING_CONCLUSIONS:
        return "pass", "SUCCESS"
    if conclusion in CANCELLED_CONCLUSIONS:
        return "cancel", "CANCELLED"
    return "fail", "FAILURE"


def _details_urls(output: str) -> dict[int, str]:
    raws = [json.loads(line) for line in output.splitlines() if line.strip()]
    return {int(raw.get("id") or 0): str(raw.get("details_url") or "") for raw in raws}


def _as_pr_check(head_check: HeadCheck, links: Mapping[int, str]) -> dict[str, Any]:
    bucket, state = _pr_bucket(head_check)
    link = links.get(int(head_check.get("check_run_id") or 0), "")
    return {"name": head_check.get("name"), "bucket": bucket, "state": state, "link": link}


def _lookup_failed(detail: str) -> dict[str, Any]:
    head = CredentialRedactor.redact_text(detail.strip().splitlines()[0] if detail.strip() else "")[:200]
    return {**_UNREGISTERED, "unknown_reason": f"{LOOKUP_FAILED_REASON}: {head}".rstrip(": ")}


def _commit_probe(check: _FinalHeadCheck, sha: str) -> Probe:
    argv = ["api", "--paginate", f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs", "--jq", CHECK_RUNS_JQ]

    def probe() -> dict[str, Any]:
        try:
            proc = check.github.gh(argv, check.repo_dir)
        except Exception as exc:
            return _lookup_failed(str(exc))
        if proc.returncode != 0:
            return _lookup_failed(proc.stderr or f"gh exited {proc.returncode}")
        checks = latest_checks_for_head(parse_check_runs(proc.stdout), sha)
        links = _details_urls(proc.stdout)
        return summarise_ci_checks([_as_pr_check(c, links) for c in checks]) if checks else dict(_UNREGISTERED)

    return probe


def _select_probe(check: _FinalHeadCheck) -> tuple[Probe, str | None]:
    seams = check.seams
    default_pushed, default_pr = _git_gh_head(check)
    pushed = (seams.pushed_head or default_pushed)()
    pr_head = (seams.pr_head or default_pr)() if pushed else None
    if not pushed or not pr_head or pr_head == pushed:
        return seams.probe or check.default_probe(), None
    return _commit_probe(check, pushed), f"{PR_HEAD_LAGGED_REASON} ({pr_head[:8]} vs {pushed[:8]})"


def _registered(ci_status: dict[str, Any]) -> bool:
    return ci_status.get("source") == "github_cli"


def _failed_checks_by_name(ci_status: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
    checks = [c for c in ci_status.get("checks") or [] if isinstance(c, dict)]
    return [next((c for c in checks if c.get("name") == n), {"name": n}) for n in names]


def _record(state: str, reason: str = "", **extra: Any) -> dict[str, Any]:
    return {"state": state, "failed_checks": [], "failed_details": [], "ci_wait": None, "reason": reason, **extra}


def not_pushed_record() -> dict[str, Any]:
    return _record("skipped", "nothing pushed")


def _failed_details(ci_status: dict[str, Any], names: list[str], detail_lookup: DetailLookup) -> list[dict[str, Any]]:
    checks = _failed_checks_by_name(ci_status, names)
    looked_up = [detail_lookup(c) if i < MAX_STEP_LOOKUPS else None for i, c in enumerate(checks)]
    return [d or bare_detail(c["name"]) for c, d in zip(checks, looked_up, strict=True)]


def classify_final_head(
    ci_status: dict[str, Any], wait_record: dict[str, Any] | None, detail_lookup: DetailLookup
) -> dict[str, Any]:
    if ci_status.get("source") != "github_cli":
        reason = ci_status.get("unknown_reason") or "CI checks were not measured on GitHub."
        return _record("unknown", reason, ci_wait=wait_record)
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


def _branch_status(check: _FinalHeadCheck, probe: Probe, policy: CiWaitPolicy) -> dict[str, Any]:
    seams = check.seams
    ci_status = probe()
    if _registered(ci_status):
        return ci_status
    _wait_for_ci_registration(
        check.context,
        check.repo_dir,
        check.branch,
        dict(seams.proxy_env),
        resolve_ci_registration_timeout(seams.registration_timeout),
        CI_REGISTRATION_POLL_SECONDS,
        policy.clock,
        policy.sleep,
        probe=check.default_probe(),
    )
    ci_status = probe()
    return ci_status if _registered(ci_status) else {**ci_status, "unknown_reason": NO_CHECKS_REASON}


def _lagged_status(check: _FinalHeadCheck, probe: Probe, policy: CiWaitPolicy) -> tuple[Probe, dict[str, Any]]:
    ci_status = probe()
    if _registered(ci_status):
        return probe, ci_status
    policy.sleep(CI_REGISTRATION_POLL_SECONDS)
    probe, still_lagged = _select_probe(check)
    if still_lagged is None:
        return probe, _branch_status(check, probe, policy)
    ci_status = probe()
    if _registered(ci_status):
        return probe, ci_status
    cause = ci_status.get("unknown_reason") or NO_CHECKS_REASON
    return probe, {**ci_status, "unknown_reason": f"{still_lagged}; {cause}"}


def _settled_status(check: _FinalHeadCheck) -> tuple[dict[str, Any], dict[str, Any] | None]:
    policy = check.seams.policy or CiWaitPolicy(resolve_review_ci_wait_seconds(os.environ))
    probe, lagged = _select_probe(check)
    if lagged is None:
        ci_status = _branch_status(check, probe, policy)
    else:
        probe, ci_status = _lagged_status(check, probe, policy)
    if not _registered(ci_status) or ci_status.get("pending", 0) <= 0:
        return ci_status, None
    ci_status, waited = wait_for_pending_ci(check.context, probe, ci_status, policy, stop_on_failure=True)
    return ci_status, waited.as_record()


def _classify_settled(check: _FinalHeadCheck) -> tuple[dict[str, Any], dict[str, Any]]:
    ci_status, wait_record = _settled_status(check)
    lookup = check.seams.detail_lookup or gh_detail_lookup(check.repo_dir, check.seams.proxy_env)
    return classify_final_head(ci_status, wait_record, lookup), ci_status


def _git_changed_paths(repo_dir: str, proxy_env: Mapping[str, str]) -> Callable[[str, str], list[str] | None]:
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
    return bool(paths) and all(path.startswith(BOOKKEEPING_PATH_PREFIXES) for path in paths)


def _code_unchanged(paths: list[str] | None) -> bool:
    return paths is not None and all(path.startswith(BOOKKEEPING_PATH_PREFIXES) for path in paths)


def _timing_gate_only(details: list[dict[str, Any]]) -> bool:
    return bool(details) and all(detail.get("timing_gate") for detail in details)


def _failed_run_ids(ci_status: dict[str, Any], names: list[str]) -> list[str] | None:
    run_ids: list[str] = []
    for check in _failed_checks_by_name(ci_status, names):
        run_id = parse_run_id(check.get("link"))
        if run_id is None:
            return None
        if run_id not in run_ids:
            run_ids.append(run_id)
    return run_ids


def _resolve_final_head(check: _FinalHeadCheck) -> str | None:
    default_pushed, default_pr = _git_gh_head(check)
    return (check.seams.pushed_head or default_pushed)() or (check.seams.pr_head or default_pr)()


def _eligible_run_ids(check: _FinalHeadCheck, record: dict[str, Any], ci_status: dict[str, Any]) -> list[str] | None:
    if ci_status.get("pending", 0) > 0 or not _timing_gate_only(record["failed_details"]):
        return None
    approved = (check.seams.approved_head or (lambda: approved_head_of(check.context)))()
    if not approved:
        return None
    measured_head = _resolve_final_head(check)
    if not measured_head:
        return None
    paths = (check.seams.changed_paths or _git_changed_paths(check.repo_dir, check.seams.proxy_env))(
        approved, measured_head
    )
    if not _code_unchanged(paths):
        return None
    return _failed_run_ids(ci_status, record["failed_checks"])


def _rerun_timing_once(
    check: _FinalHeadCheck, record: dict[str, Any], run_ids: list[str]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    seams = check.seams
    rerun = seams.rerun_dispatcher or functools.partial(_dispatch_rerun, check.github)
    if not all(rerun(run_id) for run_id in run_ids):
        return None
    probe = _select_probe(check)[0]
    policy = seams.policy or CiWaitPolicy(resolve_review_ci_wait_seconds(os.environ))
    registration_timeout = resolve_ci_registration_timeout(seams.registration_timeout)
    _wait_for_rerun_start(probe, record["failed_checks"], policy, registration_timeout)
    return _classify_settled(check)


def check_final_head_ci(
    context: FlowContext, repo_dir: str, branch: str, seams: FinalHeadSeams | None = None
) -> dict[str, Any]:
    if context.dry_run or context.skip_push:
        return _record("skipped", "dry run" if context.dry_run else "push skipped")
    seams = seams or FinalHeadSeams()
    github = seams.github or GitHubAdapter(repo_dir, dict(seams.proxy_env))
    check = _FinalHeadCheck(context, repo_dir, branch, seams, github)
    try:
        record, ci_status = _classify_settled(check)
        if record["state"] != "failed":
            return record
        eligible = functools.partial(_eligible_run_ids, check)
        rerun_once = functools.partial(_rerun_timing_once, check)
        return _retry_timing_noise(context.log, record, ci_status, eligible, rerun_once)
    except Exception as exc:
        return _record("unknown", CredentialRedactor.redact_text(str(exc))[:MAX_REASON_CHARS])


def format_final_head_ci(record: Mapping[str, Any] | None) -> str:
    state = (record or {}).get("state", "skipped")
    reason = (record or {}).get("reason") or ""
    if state == "success":
        return "final head CI: success"
    if state == "failed":
        details = record.get("failed_details") or [bare_detail(n) for n in record["failed_checks"]]  # type: ignore[index]
        return f"final head CI: failed: {', '.join(describe_failure(d) for d in details)}"
    return f"final head CI: {state}" + (f" ({reason})" if reason else "")
