"""An in-memory ``GitHubAdapter`` double for the ``flow/github`` modules: scripted PRs, checks and posting results.

``FakeGitHub`` has the adapter's public methods and parameter names and returns the adapter's own ``GhResult``, so a
shape drift fails loudly. Every call is recorded in ``calls`` as ``(method, args)``. It spawns nothing; the merge
primitives delegate to an optional ``cli`` backend, any ``GhRunner`` (typically ``tests.flow.fake_gh.FakeGitHub``
imported as ``GhCliFake``), which simulates real merges over a bare origin.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sandbox_executor.flow.github.adapter import ALLOWED_MERGE_FLAGS, parse_gh_json
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


def spawn_failure(message: str = "timed out after 60 seconds") -> GhResult:
    """The result the adapter returns when ``gh`` could not be run (``OSError`` or a timeout)."""
    return GhResult(True, 127, "", message, error=message)


@dataclass
class FakeGitHub:
    """Scripted GitHub state answering the ``GitHubAdapter`` methods.

    ``prs`` maps a head branch to its open PR number (an edit); any other branch is created as ``create_number``.
    ``checks`` is the ``gh pr checks`` payload, None for "no checks reported", or a scripted result. ``views`` are the
    successive ``gh pr view`` mergeability payloads (or scripted results); ``review_results`` the successive
    ``post_review`` outcomes, default success. ``open_error`` makes the PR lookup fail to run.
    """

    available: bool = True
    prs: dict[str, int] = field(default_factory=dict)
    create_number: int | None = 1
    edit_ok: bool = True
    create_ok: bool = True
    open_error: str = ""
    checks: list[dict[str, Any]] | Scripted | None = None
    views: list[dict[str, Any] | Scripted] = field(default_factory=list)
    review_results: list[Scripted] = field(default_factory=list)
    comment_result: Scripted = (0, "comment posted", "")
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
        if self.checks is None:
            return GhResult(True, 1, "", f"no checks reported on the '{target}' branch")
        if isinstance(self.checks, list):
            return GhResult(True, 0, json.dumps(self.checks), "")
        return _result(self.checks)

    def mergeability(self, branch: str) -> GhResult:
        """Answer one mergeability read with the next of ``views`` (a failed read once they run out)."""
        if missing := self._call("mergeability", branch):
            return missing
        if not self.views:
            return GhResult(True, 1, "", "no pull requests found")
        view = self.views.pop(0)
        return GhResult(True, 0, json.dumps(view), "") if isinstance(view, dict) else _result(view)

    def post_review(self, target: str, flag: str, body: str) -> GhResult:
        """Answer ``gh pr review`` with the next of ``review_results`` (success once they run out)."""
        if missing := self._call("post_review", target, flag, body):
            return missing
        return _result(self.review_results.pop(0)) if self.review_results else GhResult(True, 0, "reviewed", "")

    def post_comment(self, target: str, body: str) -> GhResult:
        """Answer ``gh pr comment`` with ``comment_result``."""
        return self._call("post_comment", target, body) or _result(self.comment_result)

    def gh(self, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        """Pass ``gh <args>`` to the ``cli`` backend (the merge runner)."""
        self.calls.append(("gh", (list(args), cwd)))
        if self.cli is None:
            raise AssertionError(f"FakeGitHub has no cli backend for gh {args}")
        return self.cli(list(args), cwd or self.repo_dir)

    def view_pr(self, pr: str, fields: str) -> dict[str, Any] | None:
        """``gh pr view <pr> --json <fields>`` through ``gh``."""
        data = parse_gh_json(self.gh(["pr", "view", str(pr), "--json", fields]).stdout)
        return data if isinstance(data, dict) else None

    def default_branch(self) -> str | None:
        """``gh repo view --json defaultBranchRef`` through ``gh``."""
        data = parse_gh_json(self.gh(["repo", "view", "--json", "defaultBranchRef"]).stdout)
        ref = data.get("defaultBranchRef") if isinstance(data, dict) else None
        name = ref.get("name") if isinstance(ref, dict) else None
        return name.strip() if isinstance(name, str) and name.strip() else None

    def merge_pr(self, number: int | str, flag: str, head_sha: str) -> subprocess.CompletedProcess[str]:
        """``gh pr merge <number> <flag> --match-head-commit <head_sha>``, refusing what the adapter refuses."""
        if flag not in ALLOWED_MERGE_FLAGS or not head_sha:
            raise ValueError(f"refusing gh pr merge with flag {flag!r} and head {head_sha!r}")
        return self.gh(["pr", "merge", str(number), flag, "--match-head-commit", head_sha])
