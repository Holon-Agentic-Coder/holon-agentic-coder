from __future__ import annotations

import functools
import os
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from sandbox_executor.budgets import resolve_review_ci_wait_seconds
from sandbox_executor.flow.github.ci import resolve_ci_registration_timeout
from sandbox_executor.flow.github.ci_failure_detail import STEP_LOOKUP_TIMEOUT_SECONDS, parse_run_id
from sandbox_executor.flow.github.ci_final_head_seams import (
    Probe,
    _failed_checks_by_name,
    _FinalHeadCheck,
    _resolve_final_head,
)
from sandbox_executor.flow.github.ci_reverify import approved_head_of
from sandbox_executor.flow.github.ci_timing_rerun import _dispatch_rerun, _wait_for_rerun_start
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy

BOOKKEEPING_PATH_PREFIXES = ("plans/", "executions/", "holon-knowledge/")


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
    check: _FinalHeadCheck,
    record: dict[str, Any],
    run_ids: list[str],
    select_probe: Callable[[_FinalHeadCheck], tuple[Probe, str | None]],
    classify_settled: Callable[[_FinalHeadCheck], tuple[dict[str, Any], dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    seams = check.seams
    rerun = seams.rerun_dispatcher or functools.partial(_dispatch_rerun, check.github)
    if not all(rerun(run_id) for run_id in run_ids):
        return None
    probe = select_probe(check)[0]
    policy = seams.policy or CiWaitPolicy(resolve_review_ci_wait_seconds(os.environ))
    registration_timeout = resolve_ci_registration_timeout(seams.registration_timeout)
    _wait_for_rerun_start(probe, record["failed_checks"], policy, registration_timeout)
    return classify_settled(check)
