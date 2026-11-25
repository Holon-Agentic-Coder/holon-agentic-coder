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

_COMPLETED_STATUS = "completed"
_NON_FAILING_CONCLUSIONS = PASSING_CONCLUSIONS | CANCELLED_CONCLUSIONS

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
    return [_as_check(json.loads(line)) for line in output.splitlines() if line.strip()]


def latest_checks_for_head(checks: list[HeadCheck], sha: str) -> list[HeadCheck]:
    newest_by_suite_and_name: dict[tuple[int, str], HeadCheck] = {}
    for check in checks:
        if check.get("head_sha") != sha:
            continue
        suite_and_name_key = (int(check.get("check_suite_id") or 0), str(check.get("name") or ""))
        if suite_and_name_key not in newest_by_suite_and_name or int(check.get("check_run_id") or 0) > int(
            newest_by_suite_and_name[suite_and_name_key].get("check_run_id") or 0
        ):
            newest_by_suite_and_name[suite_and_name_key] = check
    return sorted(
        newest_by_suite_and_name.values(),
        key=lambda check: (str(check.get("name")), int(check.get("check_suite_id") or 0)),
    )


def parse_suite_statuses(output: str) -> dict[int, str]:
    suites = [json.loads(line) for line in output.splitlines() if line.strip()]
    return {int(suite.get("id") or 0): str(suite.get("status") or "").lower() for suite in suites}


def with_suite_status(checks: list[HeadCheck], statuses: Mapping[int, str]) -> list[HeadCheck]:
    return [{**check, "suite_status": statuses.get(int(check.get("check_suite_id") or 0), "")} for check in checks]


def probe_head_checks(repo_dir: str, sha: str, proxy_env: Mapping[str, str] | None = None) -> list[HeadCheck] | None:
    commit_api_base = f"repos/{{owner}}/{{repo}}/commits/{sha}"
    check_runs_argv = ["gh", "api", "--paginate", f"{commit_api_base}/check-runs", "--jq", CHECK_RUNS_JQ]
    check_suites_argv = ["gh", "api", "--paginate", f"{commit_api_base}/check-suites", "--jq", CHECK_SUITES_JQ]
    try:
        runs = _run_gh(check_runs_argv, repo_dir, proxy_env)
        suites = _run_gh(check_suites_argv, repo_dir, proxy_env) if runs.returncode == 0 else runs
        if suites.returncode != 0:
            return None
        checks = latest_checks_for_head(parse_check_runs(runs.stdout), sha)
        suite_status_by_id = parse_suite_statuses(suites.stdout)
        return with_suite_status(checks, suite_status_by_id)
    except Exception:
        return None


def gh_head_checks_probe(repo_dir: str, proxy_env: Mapping[str, str] | None = None) -> HeadChecksProbe:
    return lambda sha: probe_head_checks(repo_dir, sha, proxy_env)


def is_pending(check: Mapping[str, Any]) -> bool:
    return check.get("status") != _COMPLETED_STATUS


def suite_pending(check: Mapping[str, Any]) -> bool:
    return "suite_status" in check and check.get("suite_status") != _COMPLETED_STATUS


def is_cancelled(check: Mapping[str, Any]) -> bool:
    return not is_pending(check) and check.get("conclusion") in CANCELLED_CONCLUSIONS


def is_failed(check: Mapping[str, Any]) -> bool:
    return not is_pending(check) and check.get("conclusion") not in _NON_FAILING_CONCLUSIONS


def classify_head_checks(checks: list[HeadCheck]) -> str:
    if any(is_failed(check) for check in checks):
        return FAILED
    if not checks or any(is_pending(check) or suite_pending(check) for check in checks):
        return PENDING
    if any(is_cancelled(check) for check in checks):
        return CANCELLED
    return PASSED
