from __future__ import annotations

import functools
import json
import os
from collections.abc import Mapping
from typing import Any

from sandbox_executor.budgets import resolve_review_ci_wait_seconds
from sandbox_executor.flow.constants import CI_REGISTRATION_POLL_SECONDS
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.adapter import GitHubAdapter
from sandbox_executor.flow.github.ci import (
    _extract_failed_ci_check_names,
    _wait_for_ci_registration,
    resolve_ci_registration_timeout,
)
from sandbox_executor.flow.github.ci_failure_detail import (
    MAX_STEP_LOOKUPS,
    DetailLookup,
    bare_detail,
    describe_failure,
    gh_detail_lookup,
)
from sandbox_executor.flow.github.ci_final_head_rerun import BOOKKEEPING_PATH_PREFIXES as BOOKKEEPING_PATH_PREFIXES
from sandbox_executor.flow.github.ci_final_head_rerun import _bookkeeping_only as _bookkeeping_only
from sandbox_executor.flow.github.ci_final_head_rerun import _code_unchanged as _code_unchanged
from sandbox_executor.flow.github.ci_final_head_rerun import _eligible_run_ids, _rerun_timing_once
from sandbox_executor.flow.github.ci_final_head_rerun import _failed_run_ids as _failed_run_ids
from sandbox_executor.flow.github.ci_final_head_rerun import _git_changed_paths as _git_changed_paths
from sandbox_executor.flow.github.ci_final_head_rerun import _timing_gate_only as _timing_gate_only
from sandbox_executor.flow.github.ci_final_head_seams import (
    FinalHeadSeams,
    Probe,
    _failed_checks_by_name,
    _FinalHeadCheck,
    _git_gh_head,
)
from sandbox_executor.flow.github.ci_final_head_seams import _resolve_final_head as _resolve_final_head
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
from sandbox_executor.flow.github.ci_timing_rerun import _retry_timing_noise
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy, wait_for_pending_ci
from sandbox_executor.knowledge import CredentialRedactor

MAX_REASON_CHARS = 300

PR_HEAD_LAGGED_REASON = "pull request head lagged the pushed head"
NO_CHECKS_REASON = "no checks registered for the pushed head"
LOOKUP_FAILED_REASON = "check-runs lookup failed for the pushed head"
_UNREGISTERED = {"source": "unregistered", "total": 0, "pending": 0, "checks": []}


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
        rerun_once = functools.partial(
            _rerun_timing_once, check, select_probe=_select_probe, classify_settled=_classify_settled
        )
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
