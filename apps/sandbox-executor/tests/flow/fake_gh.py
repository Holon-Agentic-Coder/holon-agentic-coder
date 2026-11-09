"""An in-memory GitHub for the guarded child merge: PRs over a real bare origin, driven through the ``gh`` runner seam.

``FakeGitHub`` is passed as (or patched in for) ``sandbox_executor.flow.github.merge.run_gh``. It answers the ``gh``
commands the merge and the child progress reconciliation run, with the JSON field names ``gh`` uses, and performs
``gh pr merge --merge`` as a real ``git merge --no-ff`` of the PR head into its base on origin. Every call is recorded,
so tests can assert which verbs and flags were (never) used. It never reaches the network.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from tests.flow.helpers import _git


class FakeGitHub:
    """Pull requests over a bare origin repository, answering ``gh`` through ``__call__(args, cwd)``."""

    def __init__(self, origin: Path, scratch: Path, default_branch: str | None = "main"):
        self.origin = origin
        self.scratch = scratch
        self.default_branch = default_branch
        self.prs: dict[int, dict[str, Any]] = {}
        self.calls: list[list[str]] = []
        # Bucket every check reports ("pass", "fail", "pending"), or None for a PR with no checks at all.
        self.checks_bucket: str | None = "pass"
        self.mergeable = "MERGEABLE"
        # How many ``gh pr checks`` calls report no checks before they register (CI not yet started on a new head).
        self.unregistered_checks_calls = 0

    def open_pr(self, head: str, base: str) -> int:
        """Record an open PR from ``head`` into ``base`` and return its number (101, 102, ...)."""
        number = 101 + len(self.prs)
        url = f"https://github.com/example/app/pull/{number}"
        self.prs[number] = {"number": number, "head": head, "base": base, "state": "OPEN", "oid": None, "url": url}
        return number

    def verbs(self) -> list[tuple[str, str]]:
        return [(c[0], c[1]) for c in self.calls if len(c) >= 2]

    def merge_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["pr", "merge"]]

    def sha(self, branch: str) -> str:
        return _git(self.origin, "rev-parse", f"refs/heads/{branch}")

    def _find(self, ref: str) -> dict[str, Any] | None:
        if ref.isdigit():
            return self.prs.get(int(ref))
        return next((pr for pr in self.prs.values() if pr["head"] == ref), None)

    def _view(self, pr: dict[str, Any]) -> dict[str, Any]:
        return {
            "number": pr["number"],
            "state": pr["state"],
            "baseRefName": pr["base"],
            "headRefName": pr["head"],
            "headRefOid": self.sha(pr["head"]),
            "mergeable": self.mergeable,
            "mergeCommit": {"oid": pr["oid"]} if pr["oid"] else None,
            "url": pr["url"],
        }

    def __call__(self, args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        handlers = {
            ("pr", "view"): self._pr_view,
            ("pr", "list"): self._pr_list,
            ("pr", "checks"): self._pr_checks,
            ("pr", "merge"): self._pr_merge,
            ("repo", "view"): self._repo_view,
        }
        handler = handlers.get((args[0], args[1]) if len(args) >= 2 else ("", ""))
        if handler is None:
            return self._done(args, 1, err=f"fake gh does not support {args[:2]}")
        return handler(args)

    def _done(self, args: list[str], code: int, out: Any = None, err: str = "") -> subprocess.CompletedProcess[str]:
        stdout = json.dumps(out) if out is not None else ""
        return subprocess.CompletedProcess(["gh", *args], code, stdout, err)

    def _pr_view(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        pr = self._find(args[2])
        return self._done(args, 0, self._view(pr)) if pr else self._done(args, 1, err="no pull requests found")

    def _pr_list(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        head = args[args.index("--head") + 1]
        found = [self._view(pr) for pr in self.prs.values() if pr["head"] == head]
        return self._done(args, 0, found)

    def _pr_checks(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        if self.unregistered_checks_calls > 0:
            self.unregistered_checks_calls -= 1
            return self._done(args, 1, err="no checks reported on the branch")
        if self.checks_bucket is None:
            return self._done(args, 1, err="no checks reported on the branch")
        return self._done(args, 0, [{"bucket": self.checks_bucket, "name": "test", "state": "COMPLETED"}])

    def _repo_view(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        if self.default_branch is None:
            return self._done(args, 1, err="HTTP 502")
        return self._done(args, 0, {"defaultBranchRef": {"name": self.default_branch}})

    def _pr_merge(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        pr = self._find(args[2])
        expected = args[args.index("--match-head-commit") + 1] if "--match-head-commit" in args else None
        if pr is None or pr["state"] != "OPEN" or "--merge" not in args or expected != self.sha(pr["head"]):
            return self._done(args, 1, err="merge refused by the fake")
        work = self.scratch / f"merge-{pr['number']}"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(work)], check=True, capture_output=True)
        _git(work, "checkout", "-q", "-B", pr["base"], f"origin/{pr['base']}")
        _git(work, "merge", "--no-ff", "-q", "-m", f"Merge pull request #{pr['number']}", f"origin/{pr['head']}")
        _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{pr['base']}")
        pr["state"], pr["oid"] = "MERGED", _git(work, "rev-parse", "HEAD")
        return self._done(args, 0)
