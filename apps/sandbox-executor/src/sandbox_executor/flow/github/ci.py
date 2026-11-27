from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.adapter import GitHubAdapter
from sandbox_executor.flow.github.ci_policy import _ci_green as _ci_green
from sandbox_executor.flow.github.ci_policy import (
    _extract_failed_ci_check_names as _extract_failed_ci_check_names,
)
from sandbox_executor.flow.github.ci_policy import local_tests_ci_status, summarise_ci_checks

logger = logging.getLogger(__name__)


def probe_ci_checks(
    repo_dir: str,
    branch_or_pr: str,
    timeout: float = 30.0,
    proxy_env: dict[str, str] | None = None,
    *,
    github: GitHubAdapter | None = None,
) -> dict[str, Any]:
    clean_target = str(branch_or_pr).strip()
    github = github or GitHubAdapter(repo_dir, proxy_env)
    if clean_target and github.available:
        try:
            proc = github.ci_checks(clean_target, timeout)
            if proc.ok and proc.stdout.strip():
                raw_checks = json.loads(proc.stdout)
                if isinstance(raw_checks, list):
                    return summarise_ci_checks(raw_checks)
        except Exception as e:
            logger.debug(f"probe_ci_checks gh check failed: {e}")

    return local_tests_ci_status()


# why: Stage 3 recorded no test result, so the review loop must see CI checks register before judging them
CI_REGISTRATION_TIMEOUT_SECONDS = 180.0
CI_REGISTRATION_TIMEOUT_ENV_VAR = "HOLON_CI_REGISTRATION_TIMEOUT"


def resolve_ci_registration_timeout(explicit: float | None = None, environ: Any = None) -> float:
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
    probe: Callable[[], dict[str, Any]] | None = None,
) -> None:
    if timeout <= 0:
        return
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    deadline = clock() + timeout
    context.log(f"[Review Loop] No recorded test result: waiting up to {timeout:g}s for CI checks to register.")
    while True:
        status = probe() if probe is not None else probe_ci_checks(repo_dir, review_target, proxy_env=proxy_env)
        if status.get("source") == "github_cli":
            context.log("[Review Loop] CI checks registered.")
            return
        remaining = deadline - clock()
        if remaining <= 0:
            context.log(f"[Review Loop] No CI checks registered within {timeout:g}s.")
            return
        sleep(max(0.0, min(poll_interval, remaining)))
