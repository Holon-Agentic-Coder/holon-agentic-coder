"""An in-memory ``GitHubAdapter`` double for the ``flow/github`` modules: scripted PRs, checks and posting results.

``FakeGitHub`` has the adapter's public methods and parameter names and returns the adapter's own ``GhResult``, so a
shape drift fails loudly. Every call is recorded in ``calls`` as ``(method, args)``. It spawns nothing; the merge
runner ``gh`` delegates to an optional ``cli`` backend, any ``GhRunner`` (typically ``tests.flow.fake_gh.FakeGitHub``
imported as ``GhCliFake``), which simulates real merges over a bare origin.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sandbox_executor.flow.github.adapter_runner import GH_MISSING, GhResult

if TYPE_CHECKING:
    import subprocess

    from sandbox_executor.flow.github.merge import GhRunner

#: A scripted ``gh`` outcome: a ready ``GhResult`` or ``(returncode, stdout, stderr)``.
Scripted = GhResult | tuple[int, str, str]


def _result(scripted: Scripted) -> GhResult:
    if isinstance(scripted, GhResult):
        return scripted
    returncode, stdout, stderr = scripted
    return GhResult(True, returncode, stdout, stderr)


def ci_checks_payload(
    passed: int = 0, failed: int = 0, pending: int = 0, names: list[str] | None = None
) -> list[dict[str, Any]]:
    """A ``gh pr checks --json`` payload with ``passed``, then ``failed``, then ``pending`` checks.

    ``names`` names the checks in that order (default ``check-1``, ``check-2``, ...); failed checks link to Actions run
    ``100 + n`` so the final-head rerun can parse their run id.
    """
    buckets = ["pass"] * passed + ["fail"] * failed + ["pending"] * pending
    labels = list(names or []) + [f"check-{n}" for n in range(len(names or []) + 1, len(buckets) + 1)]
    states = {"pass": "SUCCESS", "fail": "FAILURE", "pending": "PENDING"}
    return [
        {
            "bucket": bucket,
            "name": labels[n],
            "state": states[bucket],
            "event": "pull_request",
            "workflow": "CI",
            "link": f"https://github.com/o/r/actions/runs/{100 + n}/job/{n}",
        }
        for n, bucket in enumerate(buckets)
    ]


def merge_view(mergeable: str = "MERGEABLE", state: str = "CLEAN", number: int = 1) -> dict[str, Any]:
    """A ``gh pr view --json number,mergeable,mergeStateStatus`` mergeability payload."""
    return {"number": number, "mergeable": mergeable, "mergeStateStatus": state}


def view_for(merge: dict[str, Any]) -> dict[str, Any]:
    """The ``gh pr view`` payload that ``check_pr_mergeability`` turns back into the given mergeability dict's state."""
    dirty = bool(merge.get("is_dirty"))
    return merge_view(
        str(merge.get("mergeable") or ("CONFLICTING" if dirty else "MERGEABLE")),
        str(merge.get("merge_state_status") or ("DIRTY" if dirty else "CLEAN")),
        int(merge.get("pr_number") or 1),
    )


def checks_for(status: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The ``gh pr checks`` payload ``probe_ci_checks`` summarises into the given CI status dict's counts.

    A non-``github_cli`` status maps to None ("no checks reported"), so the probe falls back to local tests; a status
    with a ``checks`` list replays it; otherwise the counts (an ``all_passed`` status without counts is one pass).
    """
    if status.get("source") != "github_cli":
        return None
    if isinstance(status.get("checks"), list) and status["checks"]:
        return [dict(check) for check in status["checks"]]
    failed, pending = int(status.get("failed") or 0), int(status.get("pending") or 0)
    passed = int(status.get("passed") or (1 if status.get("all_passed") and not failed and not pending else 0))
    return ci_checks_payload(passed=passed, failed=failed, pending=pending)


def spawn_failure(message: str = "timed out after 60 seconds") -> GhResult:
    """The result the adapter returns when ``gh`` could not be run (``OSError`` or a timeout)."""
    return GhResult(True, 127, "", message, error=message)


@dataclass
class FakeGitHub:
    """Scripted GitHub state answering the ``GitHubAdapter`` methods.

    ``prs`` maps a head branch to its open PR number (an edit); any other branch is created as ``create_number``.
    ``checks`` is the ``gh pr checks`` payload, None for "no checks reported", or a scripted result;
    ``checks_sequence``, when non-empty, takes precedence: successive ``checks`` values, the last one repeating.
    ``views`` are the successive ``gh pr view`` mergeability payloads (or scripted results); ``review_results`` the
    successive ``post_review`` outcomes, default success; ``standing_view`` answers every mergeability read once
    ``views`` run out (None: a failed read, so the caller falls back to local git). ``head_sha`` answers
    ``pr_head_sha`` (None when its ``timeout`` is not positive). ``rerun_ok`` answers ``rerun_failed``.
    ``open_error`` makes the PR lookup fail to run. ``proxy_env`` is the environment the adapter would spawn ``gh``
    with.
    """

    available: bool = True
    prs: dict[str, int] = field(default_factory=dict)
    create_number: int | None = 1
    edit_ok: bool = True
    create_ok: bool = True
    open_error: str = ""
    checks: list[dict[str, Any]] | Scripted | None = None
    checks_sequence: list[list[dict[str, Any]] | Scripted | None] = field(default_factory=list)
    views: list[dict[str, Any] | Scripted] = field(default_factory=list)
    standing_view: dict[str, Any] | None = None
    review_results: list[Scripted] = field(default_factory=list)
    comment_result: Scripted = (0, "comment posted", "")
    head_sha: str | None = None
    rerun_ok: bool = True
    proxy_env: dict[str, str] = field(default_factory=dict)
    cli: GhRunner | None = None
    repo_dir: str = "/fake/repo"
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    def methods(self) -> list[str]:
        """The recorded method names, in call order."""
        return [name for name, _ in self.calls]

    def _call(self, name: str, *args: Any) -> GhResult | None:
        """Record the call and return the unavailable result when ``gh`` is scripted as missing."""
        self.calls.append((name, args))
        return None if self.available else GhResult(False, 127, "", GH_MISSING, error=GH_MISSING)

    def pr_view_number(self, branch: str) -> GhResult:
        """Answer ``gh pr view <branch> --json number,title`` from ``prs``."""
        if missing := self._call("pr_view_number", branch):
            return missing
        if branch not in self.prs:
            return GhResult(True, 1, "", "no pull requests found")
        return GhResult(True, 0, json.dumps({"number": self.prs[branch], "title": "Old"}), "")

    def open_or_update(self, branch: str, base: str, title: str, body: str) -> tuple[str, int | None, GhResult]:
        """Edit the scripted PR of ``branch`` or create ``create_number``, like ``GitHubAdapter.open_or_update``."""
        if missing := self._call("open_or_update", branch, base, title, body):
            return "error", None, missing
        if self.open_error:
            return "error", None, spawn_failure(self.open_error)
        if branch in self.prs:
            number = self.prs[branch]
            return "edit", number, GhResult(True, 0 if self.edit_ok else 1, f"https://github.com/o/r/pull/{number}", "")
        url = f"https://github.com/o/r/pull/{self.create_number}\n" if self.create_ok else ""
        return "create", self.create_number if self.create_ok else None, GhResult(True, 0 if url else 1, url, "")

    def ci_checks(self, target: str, timeout: float) -> GhResult:
        """Answer ``gh pr checks`` from ``checks``."""
        if missing := self._call("ci_checks", target, timeout):
            return missing
        if self.checks_sequence:
            self.checks = self.checks_sequence.pop(0) if len(self.checks_sequence) > 1 else self.checks_sequence[0]
        if self.checks is None:
            return GhResult(True, 1, "", f"no checks reported on the '{target}' branch")
        if isinstance(self.checks, list):
            return GhResult(True, 0, json.dumps(self.checks), "")
        return _result(self.checks)

    def mergeability(self, branch: str) -> GhResult:
        """Answer one mergeability read with the next of ``views`` (a failed read once they run out)."""
        if missing := self._call("mergeability", branch):
            return missing
        if not self.views and self.standing_view is None:
            return GhResult(True, 1, "", "no pull requests found")
        view = self.views.pop(0) if self.views else self.standing_view
        return GhResult(True, 0, json.dumps(view), "") if isinstance(view, dict) else _result(view)

    def post_review(self, target: str, flag: str, body: str) -> GhResult:
        """Answer ``gh pr review`` with the next of ``review_results`` (success once they run out)."""
        if missing := self._call("post_review", target, flag, body):
            return missing
        return _result(self.review_results.pop(0)) if self.review_results else GhResult(True, 0, "reviewed", "")

    def post_comment(self, target: str, body: str) -> GhResult:
        """Answer ``gh pr comment`` with ``comment_result``."""
        return self._call("post_comment", target, body) or _result(self.comment_result)

    def pr_head_sha(self, target: str, timeout: float = 60) -> str | None:
        """Answer ``gh pr view <target> --json headRefOid`` with ``head_sha``; a non-positive timeout reads nothing."""
        return None if self._call("pr_head_sha", target, timeout) or not target or timeout <= 0 else self.head_sha

    def rerun_run(self, run_id: str, timeout: float = 30.0) -> bool:
        """Answer ``gh run rerun <run_id>`` (the whole run) with ``rerun_ok``."""
        return not self._call("rerun_run", run_id, timeout) and self.rerun_ok

    def rerun_failed(self, run_id: str, timeout: float = 60) -> bool:
        """Answer ``gh run rerun <run_id> --failed`` with ``rerun_ok``."""
        return not self._call("rerun_failed", run_id, timeout) and self.rerun_ok

    def gh(self, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        """Pass ``gh <args>`` to the ``cli`` backend (the merge runner)."""
        self.calls.append(("gh", (list(args), cwd)))
        if self.cli is None:
            raise AssertionError(f"FakeGitHub has no cli backend for gh {args}")
        return self.cli(list(args), cwd or self.repo_dir)
