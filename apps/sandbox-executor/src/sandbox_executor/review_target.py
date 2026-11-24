from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sandbox_executor.entrypoint.execution_workspace import workspace_mounted
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
    "is_pr_reference",
    "resolve_review_target",
    "verify_review_diff",
]

PR_VIEW_FIELDS = "number,headRefName,headRefOid,baseRefName,title,body"
GH_TIMEOUT_SECONDS = 60
_PR_FETCH_TIMEOUT_SECONDS = 300

_PR_URL = re.compile(r"^https://github\.com/[^/\s]+/[^/\s]+/pull/(\d+)(?:[/?#].*)?$")

RunGh = Callable[[list[str], str | None], "subprocess.CompletedProcess[str]"]


class ReviewTargetError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(redact_agent_secrets(message))


@dataclass(frozen=True)
class ReviewTarget:
    raw: str
    head_branch: str
    head_sha: str
    pr_number: int | None = None
    title: str | None = None
    body: str | None = None
    base_branch: str | None = None

    @property
    def is_pr(self) -> bool:
        return self.pr_number is not None


class DiffVerdict(StrEnum):
    VERIFIED = "verified"
    EMPTY = "empty"
    UNVERIFIED = "unverified"


_DIFF_VERDICT_BY_HAS_DIFF: dict[bool | None, DiffVerdict] = {
    True: DiffVerdict.VERIFIED,
    False: DiffVerdict.EMPTY,
}


def is_pr_reference(target: str) -> bool:
    cleaned_target = target.strip()
    return cleaned_target.isdigit() or bool(_PR_URL.match(cleaned_target))


def _default_run_gh(argv: list[str], cwd: str | None) -> subprocess.CompletedProcess[str]:
    if shutil.which("gh") is None:
        raise ReviewTargetError("gh is not available on PATH, so the pull request head cannot be resolved")
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False, timeout=GH_TIMEOUT_SECONDS)


def _pr_number(target: str) -> int:
    match = _PR_URL.match(target)
    return int(match.group(1) if match else target)


def _parse_pr_view_payload(target: str, stdout: str) -> dict[str, Any]:
    try:
        pr_payload: Any = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ReviewTargetError(f"gh pr view {target} returned invalid JSON") from exc
    if not isinstance(pr_payload, dict):
        raise ReviewTargetError(f"gh pr view {target} returned an unexpected payload")
    return pr_payload


def _resolve_pr(repo_dir: str, target: str, run_gh: RunGh) -> ReviewTarget:
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
    pr_payload = _parse_pr_view_payload(target, proc.stdout or "")
    head_branch = str(pr_payload.get("headRefName") or "").strip()
    head_sha = str(pr_payload.get("headRefOid") or "").strip()
    if not head_branch or not head_sha:
        raise ReviewTargetError(f"gh pr view {target} reported no head branch or head SHA")
    raw_number = pr_payload.get("number")
    resolved_pr_number = int(raw_number) if str(raw_number or "").isdigit() else _pr_number(target)
    base_branch = str(pr_payload.get("baseRefName") or "").strip() or None
    return ReviewTarget(
        raw=target,
        head_branch=head_branch,
        head_sha=head_sha,
        pr_number=resolved_pr_number,
        title=pr_payload.get("title"),
        body=pr_payload.get("body"),
        base_branch=base_branch,
    )


def _resolve_branch(repo_dir: str, branch: str) -> ReviewTarget:
    from sandbox_executor.flow import _resolve_review_commit

    short_sha = _resolve_review_commit(repo_dir, branch, fetch=not workspace_mounted())
    if not short_sha or short_sha == branch:
        raise ReviewTargetError(f"branch '{branch}' could not be resolved to a commit")
    proc = run_git(["rev-parse", "--verify", f"{short_sha}^{{commit}}"], cwd=repo_dir, check=False)
    full_sha = proc.stdout.strip() if proc.returncode == 0 and isinstance(proc.stdout, str) else ""
    if not full_sha:
        raise ReviewTargetError(f"branch '{branch}' resolved to {short_sha}, which is not a commit")
    return ReviewTarget(raw=branch, head_branch=branch, head_sha=full_sha)


def resolve_review_target(repo_dir: str, target: str, run_gh: RunGh | None = None) -> ReviewTarget:
    cleaned_target = target.strip()
    if not cleaned_target:
        raise ReviewTargetError("the review target is empty")
    if is_pr_reference(cleaned_target):
        return _resolve_pr(repo_dir, cleaned_target, run_gh or _default_run_gh)
    return _resolve_branch(repo_dir, cleaned_target)


def _diff_verdict(repo_dir: str, base_ref: str, head_ref: str) -> tuple[DiffVerdict, str]:
    try:
        changed = changed_paths(repo_dir, base_ref, head_ref)
    except (RuntimeError, TimeoutError) as exc:
        return DiffVerdict.UNVERIFIED, f"diff of {head_ref} against {base_ref} failed: {redact_agent_secrets(str(exc))}"
    if not changed:
        return DiffVerdict.EMPTY, f"no changes outside the ledgers, plans and execution notes relative to {base_ref}"
    return DiffVerdict.VERIFIED, f"{len(changed)} changed path(s) relative to {base_ref}"


def _fetched_branch_ref(repo_dir: str, branch: str, role: str) -> tuple[str | None, str]:
    from sandbox_executor.flow.git_refs import (
        _fetch_refspecs_error,
        _has_origin_remote,
        _resolve_branch_ref,
        _unfetched_branch_message,
    )

    has_origin = _has_origin_remote(repo_dir)
    mounted = has_origin and workspace_mounted()
    if has_origin and not mounted:
        remote_refspec = f"+refs/heads/{branch}:refs/remotes/origin/{branch}"
        fetch_error = _fetch_refspecs_error(repo_dir, [remote_refspec])
        if fetch_error is not None:
            return None, f"{role} '{branch}' could not be fetched from origin: {fetch_error}"
    if mounted and (unfetched_error := _unfetched_branch_message(repo_dir, [branch], role)):
        return None, unfetched_error
    ref = _resolve_branch_ref(repo_dir, branch, prefer_remote=True)
    if ref is None:
        return None, f"{role} '{branch}' could not be resolved"
    return ref, ""


def _base_ref(repo_dir: str, base_branch: str) -> tuple[str | None, str]:
    return _fetched_branch_ref(repo_dir, base_branch, "base branch")


def _pr_head_ref(repo_dir: str, sha: str) -> str | None:
    from sandbox_executor.flow import _has_origin_remote

    if _commit_exists(repo_dir, sha):
        return sha
    if _has_origin_remote(repo_dir) and not workspace_mounted():
        run_git(
            ["fetch", "--no-tags", "origin", sha],
            cwd=repo_dir,
            check=False,
            timeout=_PR_FETCH_TIMEOUT_SECONDS,
        )
    return sha if _commit_exists(repo_dir, sha) else None


def _head_ref(repo_dir: str, target: ReviewTarget | str) -> tuple[str | None, str]:
    if isinstance(target, ReviewTarget):
        ref = _pr_head_ref(repo_dir, target.head_sha)
        return ref, "" if ref else f"'{target.head_branch}' could not be resolved to a commit"
    if target == "HEAD":
        ref = "HEAD" if _commit_exists(repo_dir, "HEAD") else None
        return ref, "" if ref else "'HEAD' could not be resolved to a commit"
    return _fetched_branch_ref(repo_dir, target, "head branch")


def _commit_exists(repo_dir: str, rev: str) -> bool:
    proc = run_git(["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], cwd=repo_dir, check=False)
    return proc.returncode == 0


def verify_review_diff(repo_dir: str, target: ReviewTarget | str, base_branch: str = "main") -> tuple[DiffVerdict, str]:
    try:
        return _classify_target_diff(repo_dir, target, base_branch)
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        return DiffVerdict.UNVERIFIED, f"git timed out while checking the review diff: {redact_agent_secrets(str(exc))}"


def _classify_target_diff(repo_dir: str, target: ReviewTarget | str, base_branch: str) -> tuple[DiffVerdict, str]:
    from sandbox_executor.flow import _execution_effective_diff, _execution_parent_plan_branch

    if isinstance(target, ReviewTarget) and not target.is_pr:
        target = target.head_branch
    branch = target.head_branch if isinstance(target, ReviewTarget) else target.strip()
    if isinstance(target, str) and is_pr_reference(branch):
        return DiffVerdict.UNVERIFIED, f"pull request '{branch}' was not resolved to a head commit"
    if isinstance(target, str) and _execution_parent_plan_branch(branch) is not None:
        has_diff, detail = _execution_effective_diff(repo_dir, branch, target_branch=base_branch)
        verdict = _DIFF_VERDICT_BY_HAS_DIFF.get(has_diff, DiffVerdict.UNVERIFIED)
        return verdict, detail
    effective_base_branch = target.base_branch if isinstance(target, ReviewTarget) and target.base_branch else base_branch
    base_ref, base_problem = _base_ref(repo_dir, effective_base_branch)
    if base_ref is None:
        return DiffVerdict.UNVERIFIED, base_problem
    resolved_head_target: ReviewTarget | str = target if isinstance(target, ReviewTarget) else branch
    head_ref, head_problem = _head_ref(repo_dir, resolved_head_target)
    if head_ref is None:
        return DiffVerdict.UNVERIFIED, head_problem
    return _diff_verdict(repo_dir, base_ref, head_ref)


def classify_review_diff(
    repo_dir: str, raw_target: str, base_branch: str = "main", run_gh: RunGh | None = None
) -> tuple[DiffVerdict, str]:
    target: ReviewTarget | str = raw_target
    if is_pr_reference(raw_target):
        try:
            target = resolve_review_target(repo_dir, raw_target, run_gh=run_gh)
        except ReviewTargetError as exc:
            return DiffVerdict.UNVERIFIED, str(exc)
    return verify_review_diff(repo_dir, target, base_branch=base_branch)
