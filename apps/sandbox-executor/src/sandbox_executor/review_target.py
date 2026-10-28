"""Fail-closed resolution of Stage 4 review targets.

A review target is an execution branch, a plain branch, a PR number or a GitHub PR URL. This module
turns one into a verified head branch and head SHA, and classifies the diff the review would judge as
verified, empty or unverified. It only reads: it never posts a review, never creates a PR and never
merges. Every error message is redacted before it is raised, so callers can log it as it is.

An all-digit target is always treated as a PR number, never as a branch name.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sandbox_executor.git import changed_paths, run_git
from sandbox_executor.redaction import redact_agent_secrets

__all__ = [
    "GH_TIMEOUT_SECONDS",
    "PR_VIEW_FIELDS",
    "DiffVerdict",
    "ReviewTarget",
    "ReviewTargetError",
    "RunGh",
    "classify_review_diff",
    "github_repo_slug",
    "is_pr_reference",
    "resolve_review_target",
    "verify_review_diff",
]

PR_VIEW_FIELDS = "number,headRefName,headRefOid,baseRefName,title,body"
GH_TIMEOUT_SECONDS = 60

_PR_URL = re.compile(r"^https://github\.com/[^/\s]+/[^/\s]+/pull/(\d+)(?:[/?#].*)?$")
_GITHUB_URL = re.compile(
    r"^(?:https?://(?:[^@/]*@)?github\.com/|(?:ssh://)?git@github\.com[:/])([^/\s]+)/([^/\s]+?)/?$"
)

RunGh = Callable[[list[str], str | None], "subprocess.CompletedProcess[str]"]


class ReviewTargetError(RuntimeError):
    """Raised when a review target cannot be resolved; the message is already redacted."""

    def __init__(self, message: str) -> None:
        """Store the redacted form of ``message``."""
        super().__init__(redact_agent_secrets(message))


@dataclass(frozen=True)
class ReviewTarget:
    """A review target resolved to the branch and commit the review actually judges."""

    raw: str
    head_branch: str
    head_sha: str
    pr_number: int | None = None
    title: str | None = None
    body: str | None = None
    base_branch: str | None = None

    @property
    def is_pr(self) -> bool:
        """Return True when the target was resolved from a pull request."""
        return self.pr_number is not None


class DiffVerdict(StrEnum):
    """Outcome of the empty-diff guard."""

    VERIFIED = "verified"
    EMPTY = "empty"
    UNVERIFIED = "unverified"


def is_pr_reference(target: str) -> bool:
    """Return True for a bare PR number or a GitHub pull request URL."""
    clean = target.strip()
    return clean.isdigit() or bool(_PR_URL.match(clean))


def github_repo_slug(repo_url: str) -> str | None:
    """Return 'owner/repo' for an https or ssh GitHub URL (userinfo and '.git' dropped), else None."""
    match = _GITHUB_URL.match(repo_url.strip())
    if not match:
        return None
    owner, repo = match.group(1), match.group(2).removesuffix(".git")
    return f"{owner}/{repo}" if owner and repo else None


def _default_run_gh(argv: list[str], cwd: str | None) -> subprocess.CompletedProcess[str]:
    """Run gh with the same bounded, non-raising shape the flow uses."""
    if shutil.which("gh") is None:
        raise ReviewTargetError("gh is not available on PATH, so the pull request head cannot be resolved")
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False, timeout=GH_TIMEOUT_SECONDS)


def _pr_number(target: str) -> int:
    """Return the PR number a bare number or PR URL names."""
    match = _PR_URL.match(target)
    return int(match.group(1) if match else target)


def _resolve_pr(repo_dir: str, target: str, run_gh: RunGh) -> ReviewTarget:
    """Resolve a PR number or URL to its head branch and head SHA through ``gh pr view``."""
    argv = ["gh", "pr", "view", target, "--json", PR_VIEW_FIELDS]
    try:
        proc = run_gh(argv, repo_dir)
    except subprocess.TimeoutExpired as exc:
        raise ReviewTargetError(f"gh pr view {target} timed out after {exc.timeout}s") from exc
    except OSError as exc:
        raise ReviewTargetError(f"gh pr view {target} could not run: {exc}") from exc
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip() or f"exit code {proc.returncode}"
        raise ReviewTargetError(f"gh pr view {target} failed: {stderr}")
    try:
        data: Any = json.loads(proc.stdout or "")
    except json.JSONDecodeError as exc:
        raise ReviewTargetError(f"gh pr view {target} returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise ReviewTargetError(f"gh pr view {target} returned an unexpected payload")
    head_branch = str(data.get("headRefName") or "").strip()
    head_sha = str(data.get("headRefOid") or "").strip()
    if not head_branch or not head_sha:
        raise ReviewTargetError(f"gh pr view {target} reported no head branch or head SHA")
    number = data.get("number")
    base_branch = str(data.get("baseRefName") or "").strip() or None
    return ReviewTarget(
        raw=target,
        head_branch=head_branch,
        head_sha=head_sha,
        pr_number=int(number) if str(number or "").isdigit() else _pr_number(target),
        title=data.get("title"),
        body=data.get("body"),
        base_branch=base_branch,
    )


def _resolve_branch(repo_dir: str, branch: str) -> ReviewTarget:
    """Resolve a branch to its full tip SHA, reusing the flow's fetch-then-resolve logic."""
    from sandbox_executor.flow import _resolve_review_commit

    short_sha = _resolve_review_commit(repo_dir, branch, fetch=True)
    if not short_sha or short_sha == branch:
        raise ReviewTargetError(f"branch '{branch}' could not be resolved to a commit")
    proc = run_git(["rev-parse", "--verify", f"{short_sha}^{{commit}}"], cwd=repo_dir, check=False)
    full_sha = proc.stdout.strip() if proc.returncode == 0 and isinstance(proc.stdout, str) else ""
    if not full_sha:
        raise ReviewTargetError(f"branch '{branch}' resolved to {short_sha}, which is not a commit")
    return ReviewTarget(raw=branch, head_branch=branch, head_sha=full_sha)


def resolve_review_target(repo_dir: str, target: str, run_gh: RunGh | None = None) -> ReviewTarget:
    """Resolve a branch, PR number or PR URL to a verified head branch and head SHA.

    Args:
        repo_dir: Repository the review runs in (gh's working directory for PR lookups).
        target: Raw review target.
        run_gh: Optional gh runner taking ``(argv, cwd)``; defaults to a bounded ``subprocess.run``.

    Returns:
        ReviewTarget: The resolved target.

    Raises:
        ReviewTargetError: If gh is missing, fails, times out or reports no head, or the branch does not resolve.
    """
    clean = target.strip()
    if not clean:
        raise ReviewTargetError("the review target is empty")
    if is_pr_reference(clean):
        return _resolve_pr(repo_dir, clean, run_gh or _default_run_gh)
    return _resolve_branch(repo_dir, clean)


def _diff_verdict(repo_dir: str, base_ref: str, head_ref: str) -> tuple[DiffVerdict, str]:
    """Classify the net diff between ``base_ref`` and ``head_ref``."""
    try:
        changed = changed_paths(repo_dir, base_ref, head_ref)
    except (RuntimeError, TimeoutError) as exc:
        return DiffVerdict.UNVERIFIED, f"diff of {head_ref} against {base_ref} failed: {redact_agent_secrets(str(exc))}"
    if not changed:
        return DiffVerdict.EMPTY, f"no changes outside the ledgers, plans and execution notes relative to {base_ref}"
    return DiffVerdict.VERIFIED, f"{len(changed)} changed path(s) relative to {base_ref}"


def _base_ref(repo_dir: str, base_branch: str) -> str | None:
    """Fetch ``base_branch`` from origin when there is one and return the ref to diff against."""
    from sandbox_executor.flow import _fetch_refspecs_error, _has_origin_remote, _resolve_branch_ref

    if _has_origin_remote(repo_dir):
        _fetch_refspecs_error(repo_dir, [f"+refs/heads/{base_branch}:refs/remotes/origin/{base_branch}"])
    return _resolve_branch_ref(repo_dir, base_branch, prefer_remote=True)


def _head_ref(repo_dir: str, target: ReviewTarget | str) -> str | None:
    """Return the ref or SHA to diff for a target, fetching a PR head SHA that is not present yet."""
    from sandbox_executor.flow import _has_origin_remote, _resolve_branch_ref

    if isinstance(target, ReviewTarget):
        sha = target.head_sha
        if _commit_exists(repo_dir, sha):
            return sha
        if _has_origin_remote(repo_dir):
            run_git(["fetch", "--no-tags", "origin", sha], cwd=repo_dir, check=False, timeout=300)
        return sha if _commit_exists(repo_dir, sha) else None
    if target == "HEAD":
        return "HEAD" if _commit_exists(repo_dir, "HEAD") else None
    return _resolve_branch_ref(repo_dir, target, prefer_remote=True)


def _commit_exists(repo_dir: str, rev: str) -> bool:
    """Return True when ``rev`` names a commit in ``repo_dir``."""
    proc = run_git(["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], cwd=repo_dir, check=False)
    return proc.returncode == 0


def verify_review_diff(repo_dir: str, target: ReviewTarget | str, base_branch: str = "main") -> tuple[DiffVerdict, str]:
    """Classify the diff a review of ``target`` would judge, failing closed.

    Execution branches are compared with their parent plan branch (the flow's existing guard). A resolved
    PR is compared by its head SHA with its own base branch, falling back to ``base_branch`` when gh
    reported none; a branch is compared by its tip with ``base_branch``.
    A raw PR reference that was never resolved is unverified, and so is any git failure. No gh call is
    made here, so the guard never depends on gh being installed.

    Args:
        repo_dir: Repository to inspect.
        target: A resolved target, or a raw target string.
        base_branch: Branch a non-execution target is compared with (the intent's target branch) when a
            resolved PR carries no base branch of its own.

    Returns:
        tuple[DiffVerdict, str]: The verdict and a redacted, human-readable detail.
    """
    from sandbox_executor.flow import _execution_effective_diff, _execution_parent_plan_branch

    if isinstance(target, ReviewTarget) and not target.is_pr:
        target = target.head_branch
    branch = target.head_branch if isinstance(target, ReviewTarget) else target.strip()
    if isinstance(target, str) and is_pr_reference(branch):
        return DiffVerdict.UNVERIFIED, f"pull request '{branch}' was not resolved to a head commit"
    if isinstance(target, str) and _execution_parent_plan_branch(branch) is not None:
        has_diff, detail = _execution_effective_diff(repo_dir, branch)
        verdict = {True: DiffVerdict.VERIFIED, False: DiffVerdict.EMPTY}.get(has_diff, DiffVerdict.UNVERIFIED)
        return verdict, detail
    if isinstance(target, ReviewTarget) and target.base_branch:
        base_branch = target.base_branch
    base_ref = _base_ref(repo_dir, base_branch)
    if base_ref is None:
        return DiffVerdict.UNVERIFIED, f"base branch '{base_branch}' could not be resolved"
    head_ref = _head_ref(repo_dir, target if isinstance(target, ReviewTarget) else branch)
    if head_ref is None:
        return DiffVerdict.UNVERIFIED, f"'{branch}' could not be resolved to a commit"
    return _diff_verdict(repo_dir, base_ref, head_ref)


def classify_review_diff(
    repo_dir: str, raw_target: str, base_branch: str = "main", run_gh: RunGh | None = None
) -> tuple[DiffVerdict, str]:
    """Run the empty-diff guard for a raw target, resolving a PR reference to its head first.

    A PR reference that cannot be resolved is unverified, with the redacted resolution error as detail.

    Args:
        repo_dir: Repository to inspect.
        raw_target: Branch, PR number or PR URL as the caller received it.
        base_branch: Branch a non-execution target is compared with when a PR names no base of its own.
        run_gh: Optional gh runner for the PR lookup; defaults to a bounded ``subprocess.run``.

    Returns:
        tuple[DiffVerdict, str]: The verdict and a redacted, human-readable detail.
    """
    target: ReviewTarget | str = raw_target
    if is_pr_reference(raw_target):
        try:
            target = resolve_review_target(repo_dir, raw_target, run_gh=run_gh)
        except ReviewTargetError as exc:
            return DiffVerdict.UNVERIFIED, str(exc)
    return verify_review_diff(repo_dir, target, base_branch=base_branch)
