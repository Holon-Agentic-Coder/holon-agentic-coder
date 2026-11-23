"""The repository-scoped GitHub adapter: the only ``flow/github`` module that spawns ``gh`` for PR create/update, CI
checks, mergeability, PR head lookups, review and comment posting, and the guarded merge's ``gh`` runner (whose
``gh pr merge`` argv only :func:`merge_argv` builds).

Each method is a thin argv builder over one spawn (:func:`.adapter_runner.spawn_gh`), with the timeouts of the call
sites it replaced: 60 seconds for PR views, edits, creates, reviews and comments, the caller's timeout for
``gh pr checks``, and 120 seconds with no proxy environment for the merge runner. ``gh`` is spawned through
``subprocess.run`` looked up at call time, so the suite's write guard (``tests/gh_write_guard.py``) sees every write.
Decisions stay in the callers and their policy modules; tests inject ``tests/flow/github/fake_github.FakeGitHub``.
"""

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

PR_TIMEOUT_SECONDS = 60
MERGE_TIMEOUT_SECONDS = 120
CI_CHECK_FIELDS = "bucket,name,state,event,workflow,link"
MERGEABILITY_FIELDS = "number,mergeable,mergeStateStatus,statusCheckRollup"
#: The only ``gh pr merge`` method flags the adapter runs; never ``--rebase``, ``--auto`` or ``--admin``.
ALLOWED_MERGE_FLAGS = ("--merge", "--squash")


def parse_gh_json(stdout: str | None) -> Any:
    """Return the JSON ``gh`` printed, or None when it printed nothing or something that is not JSON."""
    try:
        return json.loads(stdout) if stdout and stdout.strip() else None
    except (json.JSONDecodeError, TypeError):
        return None


class GitHubAdapter:
    """``gh`` for one repository checkout, with an optional proxy environment for the PR, CI and review calls."""

    def __init__(
        self,
        repo_dir: str,
        proxy_env: dict[str, str] | None = None,
        *,
        run: RunFn | None = None,
        which: WhichFn | None = None,
    ) -> None:
        """Bind the adapter to ``repo_dir``; ``run``/``which`` default to ``subprocess.run``/``shutil.which``."""
        self.repo_dir = repo_dir
        self.proxy_env = proxy_env
        self._run = run
        self._which = which

    def _runner(self) -> RunFn:
        # Looked up at call time: a default argument would bind the real function and defeat global test patches.
        return self._run or subprocess.run

    @property
    def available(self) -> bool:
        """True when the ``gh`` binary is on ``PATH``."""
        return bool((self._which or shutil.which)("gh"))

    def _gh(self, args: list[str], timeout: float) -> GhResult:
        """Run ``gh <args>`` with the proxy environment, or return a 127 result without spawning when it is absent."""
        if not self.available:
            return GhResult(False, 127, "", GH_MISSING, error=GH_MISSING)
        return spawn_gh(self._runner(), args, self.repo_dir, timeout, gh_env(self.proxy_env))

    def pr_view_number(self, branch: str) -> GhResult:
        """``gh pr view <branch> --json number,title``."""
        return self._gh(["pr", "view", branch, "--json", "number,title"], PR_TIMEOUT_SECONDS)

    def open_or_update(self, branch: str, base: str, title: str, body: str) -> tuple[str, int | None, GhResult]:
        """Edit the open PR of ``branch`` or create one into ``base``; return ``(action, pr_number, result)``.

        ``action`` is ``edit``, ``create`` or ``error`` (the PR lookup could not run). An edit passes ``--title`` and
        ``--body`` only when non-empty; a create defaults the title to the branch and the body to an automated note.

        Raises:
            json.JSONDecodeError, AttributeError: The PR lookup printed something other than a JSON object.
        """
        view = self.pr_view_number(branch)
        if view.error:
            return "error", None, view
        if view.ok and view.stdout.strip():
            number = json.loads(view.stdout).get("number")
            edit = ["pr", "edit", branch, *(["--title", title] if title else []), *(["--body", body] if body else [])]
            return "edit", number, self._gh(edit, PR_TIMEOUT_SECONDS)
        created = self._gh(
            [
                "pr",
                "create",
                *("--base", base, "--head", branch),
                *("--title", title or branch),
                *("--body", body or f"Automated PR for branch {branch}"),
            ],
            PR_TIMEOUT_SECONDS,
        )
        match = re.search(r"/pull/(\d+)", f"{created.stdout}\n{created.stderr}")
        return "create", int(match.group(1)) if match else None, created

    def ci_checks(self, target: str, timeout: float) -> GhResult:
        """``gh pr checks <target> --json bucket,name,state,event,workflow,link`` bounded by ``timeout``."""
        return self._gh(["pr", "checks", target, "--json", CI_CHECK_FIELDS], timeout)

    def mergeability(self, branch: str) -> GhResult:
        """One ``gh pr view <branch> --json number,mergeable,mergeStateStatus,statusCheckRollup`` read."""
        return self._gh(["pr", "view", branch, "--json", MERGEABILITY_FIELDS], PR_TIMEOUT_SECONDS)

    def post_review(self, target: str, flag: str, body: str) -> GhResult:
        """``gh pr review <target> <flag> --body <body>``."""
        return self._gh(["pr", "review", target, flag, "--body", body], PR_TIMEOUT_SECONDS)

    def post_comment(self, target: str, body: str) -> GhResult:
        """``gh pr comment <target> --body <body>``."""
        return self._gh(["pr", "comment", target, "--body", body], PR_TIMEOUT_SECONDS)

    def gh(self, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        """The guarded merge's ``GhRunner``: ``gh <args>`` in ``cwd`` (default the checkout), no proxy, 120 seconds.

        A missing binary or a timeout becomes a returncode-127 process with redacted stderr, never a raise.
        """
        env = gh_env(None)
        result = spawn_gh(self._runner(), args, cwd or self.repo_dir, MERGE_TIMEOUT_SECONDS, env)
        return subprocess.CompletedProcess(["gh", *args], result.returncode, result.stdout, result.stderr)

    def pr_head_sha(self, target: str, timeout: float = PR_TIMEOUT_SECONDS) -> str | None:
        """The ``headRefOid`` of ``gh pr view <target> --json headRefOid``, or None when it cannot be read."""
        if not target:
            return None
        result = self._gh(["pr", "view", target, "--json", "headRefOid"], timeout)
        data = parse_gh_json(result.stdout) if result.ok else None
        return (str(data.get("headRefOid") or "") or None) if isinstance(data, dict) else None


def merge_argv(number: int | str, flag: str, head_sha: str) -> list[str]:
    """The ``gh pr merge <number> <flag> --match-head-commit <head_sha>`` argv; refuses any flag but merge or squash.

    Raises:
        ValueError: ``flag`` is not ``--merge`` or ``--squash``, or ``head_sha`` is empty.
    """
    if flag not in ALLOWED_MERGE_FLAGS or not head_sha:
        raise ValueError(f"refusing gh pr merge with flag {flag!r} and head {head_sha!r}")
    return ["pr", "merge", str(number), flag, "--match-head-commit", head_sha]
