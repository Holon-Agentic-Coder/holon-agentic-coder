from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

from sandbox_executor.flow.github.adapter_runner import (
    GH_MISSING,
    GhResult,
    RunFn,
    WhichFn,
    gh_env,
    spawn_gh,
)
from sandbox_executor.flow.github.forge import GitForge

PR_TIMEOUT_SECONDS = 60
MERGE_TIMEOUT_SECONDS = 120
RERUN_RUN_TIMEOUT_SECONDS = 30.0
CI_CHECK_FIELDS = "bucket,name,state,event,workflow,link"
MERGEABILITY_FIELDS = "number,mergeable,mergeStateStatus,statusCheckRollup"
ALLOWED_MERGE_FLAGS = ("--merge", "--squash")

_PR_VIEW_FIELDS = "number,title"
_PR_HEAD_SHA_FIELD = "headRefOid"
_PULL_NUMBER_PATTERN = re.compile(r"/pull/(\d+)")
_MISSING_GH_EXIT_CODE = 127


def parse_gh_json(stdout: str | None) -> Any:
    try:
        return json.loads(stdout) if stdout and stdout.strip() else None
    except (json.JSONDecodeError, TypeError):
        return None


def _build_pr_edit_args(branch: str, title: str, body: str) -> list[str]:
    return ["pr", "edit", branch, *(["--title", title] if title else []), *(["--body", body] if body else [])]


def _build_pr_create_args(branch: str, base: str, title: str, body: str) -> list[str]:
    return [
        "pr",
        "create",
        *("--base", base, "--head", branch),
        *("--title", title or branch),
        *("--body", body or f"Automated PR for branch {branch}"),
    ]


def _parse_created_pr_number(result: GhResult) -> int | None:
    match = _PULL_NUMBER_PATTERN.search(f"{result.stdout}\n{result.stderr}")
    return int(match.group(1)) if match else None


class GitHubAdapter(GitForge):
    def __init__(
        self,
        repo_dir: str,
        proxy_env: dict[str, str] | None = None,
        *,
        run: RunFn | None = None,
        which: WhichFn | None = None,
    ) -> None:
        self.repo_dir = repo_dir
        self._proxy_env = proxy_env
        self._run = run
        self._which = which

    @property
    def proxy_env(self) -> dict[str, str] | None:
        return self._proxy_env

    @proxy_env.setter
    def proxy_env(self, value: dict[str, str] | None) -> None:
        self._proxy_env = value

    def _runner(self) -> RunFn:
        return self._run or subprocess.run

    @property
    def available(self) -> bool:
        return bool((self._which or shutil.which)("gh"))

    def _gh(self, args: list[str], timeout: float) -> GhResult:
        if not self.available:
            return GhResult(False, _MISSING_GH_EXIT_CODE, "", GH_MISSING, error=GH_MISSING)
        return spawn_gh(self._runner(), args, self.repo_dir, timeout, gh_env(self.proxy_env))

    def pr_view_number(self, branch: str) -> GhResult:
        return self._gh(["pr", "view", branch, "--json", _PR_VIEW_FIELDS], PR_TIMEOUT_SECONDS)

    def open_or_update(self, branch: str, base: str, title: str, body: str) -> tuple[str, int | None, GhResult]:
        view = self.pr_view_number(branch)
        if view.error:
            return "error", None, view
        if view.ok and view.stdout.strip():
            number = json.loads(view.stdout).get("number")
            return "edit", number, self._gh(_build_pr_edit_args(branch, title, body), PR_TIMEOUT_SECONDS)
        created = self._gh(_build_pr_create_args(branch, base, title, body), PR_TIMEOUT_SECONDS)
        return "create", _parse_created_pr_number(created), created

    def ci_checks(self, target: str, timeout: float) -> GhResult:
        return self._gh(["pr", "checks", target, "--json", CI_CHECK_FIELDS], timeout)

    def mergeability(self, branch: str) -> GhResult:
        return self._gh(["pr", "view", branch, "--json", MERGEABILITY_FIELDS], PR_TIMEOUT_SECONDS)

    def post_review(self, target: str, flag: str, body: str) -> GhResult:
        return self._gh(["pr", "review", target, flag, "--body", body], PR_TIMEOUT_SECONDS)

    def post_comment(self, target: str, body: str) -> GhResult:
        return self._gh(["pr", "comment", target, "--body", body], PR_TIMEOUT_SECONDS)

    def rerun_failed(self, run_id: str, timeout: float = PR_TIMEOUT_SECONDS) -> bool:
        return self._gh(["run", "rerun", run_id, "--failed"], timeout).ok

    def rerun_run(self, run_id: str, timeout: float = RERUN_RUN_TIMEOUT_SECONDS) -> bool:
        return self._gh(["run", "rerun", run_id], timeout).ok

    def gh(self, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        env = gh_env(None)
        result = spawn_gh(self._runner(), args, cwd or self.repo_dir, MERGE_TIMEOUT_SECONDS, env)
        return subprocess.CompletedProcess(["gh", *args], result.returncode, result.stdout, result.stderr)

    def pr_head_sha(self, target: str, timeout: float = PR_TIMEOUT_SECONDS) -> str | None:
        if not target:
            return None
        result = self._gh(["pr", "view", target, "--json", _PR_HEAD_SHA_FIELD], timeout)
        data = parse_gh_json(result.stdout) if result.ok else None
        return (str(data.get(_PR_HEAD_SHA_FIELD) or "") or None) if isinstance(data, dict) else None


def merge_argv(number: int | str, flag: str, head_sha: str) -> list[str]:
    if flag not in ALLOWED_MERGE_FLAGS or not head_sha:
        raise ValueError(f"refusing gh pr merge with flag {flag!r} and head {head_sha!r}")
    return ["pr", "merge", str(number), flag, "--match-head-commit", head_sha]
