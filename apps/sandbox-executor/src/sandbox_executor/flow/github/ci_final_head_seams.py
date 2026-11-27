from __future__ import annotations

import functools
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.adapter import GitHubAdapter
from sandbox_executor.flow.github.ci import probe_ci_checks
from sandbox_executor.flow.github.ci_failure_detail import STEP_LOOKUP_TIMEOUT_SECONDS, DetailLookup
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy

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


def _failed_checks_by_name(ci_status: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
    checks = [c for c in ci_status.get("checks") or [] if isinstance(c, dict)]
    return [next((c for c in checks if c.get("name") == n), {"name": n}) for n in names]


def _resolve_final_head(check: _FinalHeadCheck) -> str | None:
    default_pushed, default_pr = _git_gh_head(check)
    return (check.seams.pushed_head or default_pushed)() or (check.seams.pr_head or default_pr)()
