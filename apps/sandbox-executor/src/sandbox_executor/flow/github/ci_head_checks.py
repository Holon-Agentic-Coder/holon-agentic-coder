"""CI check runs of one exact commit SHA, read through the GitHub check-runs API and classified fail-closed.

``gh pr checks`` reports the latest run per check name on the pull request, whichever commit it ran on, and has no
notion of a cancelled or stale run. This module reads ``repos/{owner}/{repo}/commits/<sha>/check-runs`` instead, drops
every run registered against another SHA (a superseded run never stands in for the head) and keeps the newest run per
check name, so a re-triggered run replaces the cancelled one. Only ``success``, ``neutral`` and ``skipped`` pass.

A workflow registers the check run of a ``needs:``-dependent job only once the jobs it needs finish, so every visible
run can be complete while its workflow is still running. Each run therefore carries the status of its check suite (read
from ``commits/<sha>/check-suites``), and the head only passes once every suite holding one of its runs completed.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from sandbox_executor.flow.github.ci_failure_detail import STEP_LOOKUP_TIMEOUT_SECONDS, parse_run_id

PASSED = "passed"
PENDING = "pending"
CANCELLED = "cancelled"
FAILED = "failed"

PASSING_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
CANCELLED_CONCLUSIONS = frozenset({"cancelled", "stale"})
PENDING_STATUSES = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})
CHECK_RUNS_JQ = ".check_runs[] | {id, name, status, conclusion, head_sha, details_url, check_suite_id: .check_suite.id}"
CHECK_SUITES_JQ = ".check_suites[] | {id, status}"

HeadCheck = dict[str, Any]
HeadChecksProbe = Callable[[str], "list[HeadCheck] | None"]
RunRerunner = Callable[[str], bool]


def _gh_env(proxy_env: Mapping[str, str] | None) -> dict[str, str]:
    return {**os.environ, **(proxy_env or {}), "DOCKER_CLI_HINTS": "false"}


def _run_gh(argv: list[str], repo_dir: str, proxy_env: Mapping[str, str] | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
        timeout=STEP_LOOKUP_TIMEOUT_SECONDS,
        env=_gh_env(proxy_env),
    )


def _as_check(raw: Mapping[str, Any]) -> HeadCheck:
    """One check run as a plain record; ``run_id`` is None for a check that is not a GitHub Actions run."""
    return {
        "check_run_id": int(raw.get("id") or 0),
        "name": str(raw.get("name") or ""),
        "status": str(raw.get("status") or "").lower(),
        "conclusion": str(raw.get("conclusion") or "").lower(),
        "head_sha": str(raw.get("head_sha") or ""),
        "check_suite_id": int(raw.get("check_suite_id") or 0),
        "run_id": parse_run_id(raw.get("details_url")),
    }


def parse_check_runs(output: str) -> list[HeadCheck]:
    """Parse the one-object-per-line ``--jq`` output of the check-runs endpoint."""
    return [_as_check(json.loads(line)) for line in output.splitlines() if line.strip()]


def latest_checks_for_head(checks: list[HeadCheck], sha: str) -> list[HeadCheck]:
    """Only the runs on exactly ``sha``, keeping the newest run (highest check-run id) per check suite and name.

    Keying on the suite as well keeps two workflows that both define a job of the same name apart, so a passing run
    of one never hides a failed run of the other; a re-run stays in its suite and still replaces the cancelled run.
    """
    newest: dict[tuple[int, str], HeadCheck] = {}
    for check in checks:
        if check.get("head_sha") != sha:
            continue
        key = (int(check.get("check_suite_id") or 0), str(check.get("name") or ""))
        if key not in newest or int(check.get("check_run_id") or 0) > int(newest[key].get("check_run_id") or 0):
            newest[key] = check
    return sorted(newest.values(), key=lambda check: (str(check.get("name")), int(check.get("check_suite_id") or 0)))


def parse_suite_statuses(output: str) -> dict[int, str]:
    """Parse the one-object-per-line ``--jq`` output of the check-suites endpoint into suite id -> status."""
    suites = [json.loads(line) for line in output.splitlines() if line.strip()]
    return {int(suite.get("id") or 0): str(suite.get("status") or "").lower() for suite in suites}


def with_suite_status(checks: list[HeadCheck], statuses: Mapping[int, str]) -> list[HeadCheck]:
    """Each check with the status of its suite; a suite the endpoint did not list counts as not completed."""
    return [{**check, "suite_status": statuses.get(int(check.get("check_suite_id") or 0), "")} for check in checks]


def probe_head_checks(repo_dir: str, sha: str, proxy_env: Mapping[str, str] | None = None) -> list[HeadCheck] | None:
    """Newest check run per suite/name on exactly ``sha`` with its suite status; None on any error (never passed)."""
    base = f"repos/{{owner}}/{{repo}}/commits/{sha}"
    runs_argv = ["gh", "api", "--paginate", f"{base}/check-runs", "--jq", CHECK_RUNS_JQ]
    suites_argv = ["gh", "api", "--paginate", f"{base}/check-suites", "--jq", CHECK_SUITES_JQ]
    try:
        runs = _run_gh(runs_argv, repo_dir, proxy_env)
        suites = _run_gh(suites_argv, repo_dir, proxy_env) if runs.returncode == 0 else runs
        if suites.returncode != 0:
            return None
        checks = latest_checks_for_head(parse_check_runs(runs.stdout), sha)
        return with_suite_status(checks, parse_suite_statuses(suites.stdout))
    except Exception:
        return None


def gh_head_checks_probe(repo_dir: str, proxy_env: Mapping[str, str] | None = None) -> HeadChecksProbe:
    """:func:`probe_head_checks` bound to a repository, for the gate's injectable probe seam."""
    return lambda sha: probe_head_checks(repo_dir, sha, proxy_env)


def is_pending(check: Mapping[str, Any]) -> bool:
    """True for a check run that has not completed (queued, in progress, waiting or requested)."""
    return check.get("status") != "completed"


def suite_pending(check: Mapping[str, Any]) -> bool:
    """True when the probe read the run's check suite and it has not completed (a ``needs:`` job may still register)."""
    return "suite_status" in check and check.get("suite_status") != "completed"


def is_cancelled(check: Mapping[str, Any]) -> bool:
    """True for a completed run that was cancelled or went stale: never a pass."""
    return not is_pending(check) and check.get("conclusion") in CANCELLED_CONCLUSIONS


def is_failed(check: Mapping[str, Any]) -> bool:
    """True for a completed run whose conclusion is neither passing nor cancelled (unknown conclusions included)."""
    conclusion = check.get("conclusion")
    return not is_pending(check) and conclusion not in PASSING_CONCLUSIONS | CANCELLED_CONCLUSIONS


def classify_head_checks(checks: list[HeadCheck]) -> str:
    """``failed`` beats ``pending`` beats ``cancelled``; ``passed`` needs every run and every suite of one completed.

    A run whose suite is still running keeps the head ``pending`` even when every visible run passed or was cancelled.
    """
    if any(is_failed(check) for check in checks):
        return FAILED
    if not checks or any(is_pending(check) or suite_pending(check) for check in checks):
        return PENDING
    if any(is_cancelled(check) for check in checks):
        return CANCELLED
    return PASSED
