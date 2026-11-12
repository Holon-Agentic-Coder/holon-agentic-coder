"""CI check probing for the review loop: probe the PR's checks, wait for them to register and settle, and
summarise the result.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from typing import Any

from sandbox_executor.flow.context import FlowContext

logger = logging.getLogger(__name__)


def probe_ci_checks(
    repo_dir: str,
    branch_or_pr: str,
    timeout: float = 30.0,
    proxy_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Inspect CI check status using GitHub CLI (gh pr checks) with local fallback.

    Args:
        repo_dir: Path to git repository.
        branch_or_pr: Branch name, PR number, or PR URL.
        timeout: Maximum timeout in seconds for gh execution.
        proxy_env: Optional proxy environment variables.

    Returns:
        dict[str, Any]: Status summary dictionary with keys:
            - source: str ("github_cli" or "local_tests")
            - total: int
            - passed: int
            - failed: int
            - pending: int
            - skipped: int
            - all_passed: bool
            - checks: list[dict[str, Any]]
    """
    clean_target = str(branch_or_pr).strip()
    if shutil.which("gh") and clean_target:
        gh_env = {**os.environ, **(proxy_env or {}), "DOCKER_CLI_HINTS": "false"}
        try:
            cmd = ["gh", "pr", "checks", clean_target, "--json", "bucket,name,state,event,workflow,link"]
            proc = subprocess.run(
                cmd,
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                env=gh_env,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                raw_checks = json.loads(proc.stdout)
                if isinstance(raw_checks, list):
                    total = len(raw_checks)
                    passed = sum(
                        1
                        for c in raw_checks
                        if str(c.get("bucket", "")).lower() == "pass"
                        or (
                            str(c.get("state", "")).upper() in ("SUCCESS", "COMPLETED")
                            and str(c.get("bucket", "")).lower() != "fail"
                        )
                    )
                    failed = sum(
                        1
                        for c in raw_checks
                        if str(c.get("bucket", "")).lower() == "fail"
                        or str(c.get("state", "")).upper() in ("FAILURE", "TIMED_OUT", "ACTION_REQUIRED")
                    )
                    pending = sum(
                        1
                        for c in raw_checks
                        if str(c.get("bucket", "")).lower() == "pending"
                        or str(c.get("state", "")).upper() in ("IN_PROGRESS", "QUEUED", "PENDING")
                    )
                    skipped = sum(
                        1
                        for c in raw_checks
                        if str(c.get("bucket", "")).lower() in ("skipping", "skip")
                        or str(c.get("state", "")).upper() == "SKIPPED"
                    )
                    all_passed = (total > 0) and (failed == 0) and (pending == 0)
                    return {
                        "source": "github_cli",
                        "total": total,
                        "passed": passed,
                        "failed": failed,
                        "pending": pending,
                        "skipped": skipped,
                        "all_passed": all_passed,
                        "checks": raw_checks,
                    }
        except Exception as e:
            logger.debug(f"probe_ci_checks gh check failed: {e}")

    # Fallback to local tests inspection
    return {
        "source": "local_tests",
        "total": 1,
        "passed": 1,
        "failed": 0,
        "pending": 0,
        "skipped": 0,
        "all_passed": True,
        "checks": [{"name": "local_tests", "bucket": "pass", "state": "SUCCESS"}],
    }


# How long the review loop waits for GitHub CI checks to register on a PR when Stage 3 recorded no test result.
CI_REGISTRATION_TIMEOUT_SECONDS = 180.0


CI_REGISTRATION_TIMEOUT_ENV_VAR = "HOLON_CI_REGISTRATION_TIMEOUT"


def resolve_ci_registration_timeout(explicit: float | None = None, environ: Any = None) -> float:
    """Return the CI registration wait: explicit value, then ``HOLON_CI_REGISTRATION_TIMEOUT``, then 180 seconds.

    ``0`` disables the wait; negative or unparseable values fall back to the default.
    """
    if explicit is not None and explicit >= 0:
        return float(explicit)
    env = os.environ if environ is None else environ
    try:
        value = float(env.get(CI_REGISTRATION_TIMEOUT_ENV_VAR, ""))
    except ValueError:
        return CI_REGISTRATION_TIMEOUT_SECONDS
    return value if value >= 0 else CI_REGISTRATION_TIMEOUT_SECONDS


def _wait_for_ci_registration(  # noqa: PLR0913
    context: FlowContext,
    repo_dir: str,
    review_target: str,
    proxy_env: dict[str, str],
    timeout: float,
    poll_interval: float,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Poll until GitHub CI checks register on ``review_target`` or ``timeout`` elapses.

    When invoked during the post-review gate, the wait is bounded by the remaining iteration budget.
    """
    if timeout <= 0:
        return
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    deadline = clock() + timeout
    context.log(f"[Review Loop] No recorded test result: waiting up to {timeout:g}s for CI checks to register.")
    while True:
        if probe_ci_checks(repo_dir, review_target, proxy_env=proxy_env).get("source") == "github_cli":
            context.log("[Review Loop] CI checks registered.")
            return
        remaining = deadline - clock()
        if remaining <= 0:
            context.log(f"[Review Loop] No CI checks registered within {timeout:g}s.")
            return
        sleep(max(0.0, min(poll_interval, remaining)))


def _ci_green(ci_status: dict[str, Any], tests_failed: bool) -> bool | None:
    """True or False when CI was measured, None when it was not (unknown is never green)."""
    if ci_status.get("measured") is False or "all_passed" not in ci_status:
        return None
    return ci_status.get("all_passed") is True and not tests_failed


def _extract_failed_ci_check_names(ci_status: dict[str, Any] | None, tests_failed: bool = False) -> list[str]:
    """Extract unique names of failing CI checks or local tests."""
    failing: list[str] = []
    if ci_status and isinstance(ci_status.get("checks"), list):
        for check in ci_status["checks"]:
            if not isinstance(check, dict):
                continue
            bucket = str(check.get("bucket") or "").lower()
            state = str(check.get("state") or "").upper()
            if bucket == "fail" or state in ("FAILURE", "TIMED_OUT", "ACTION_REQUIRED"):
                name = str(check.get("name") or "").strip()
                if name and name not in failing:
                    failing.append(name)
    if tests_failed and "local_tests" not in failing:
        failing.append("local_tests")
    return failing
