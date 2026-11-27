from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.git_refs import EXECUTOR_GIT_ENV, _fetch_refspecs_error
from sandbox_executor.flow.github.adapter import GitHubAdapter, merge_argv, parse_gh_json
from sandbox_executor.flow.github.forge import GitForge
from sandbox_executor.flow.github.merge_method import lookup_merge_flag
from sandbox_executor.flow.github.mergeability_policy import PROTECTED_BRANCHES as PROTECTED_BRANCHES
from sandbox_executor.flow.github.mergeability_policy import _bare_branch as _bare_branch
from sandbox_executor.flow.github.mergeability_policy import merge_refusal_reason as merge_refusal_reason
from sandbox_executor.flow.ledger_interleave import resolve_ledger_only_conflicts
from sandbox_executor.git import run_git
from sandbox_executor.redaction import redact_agent_secrets

GhRunner = Callable[[list[str], str], subprocess.CompletedProcess[str]]

PR_VIEW_FIELDS = "number,state,baseRefName,headRefName,headRefOid,mergeable,mergeCommit,url"
MERGE_SETTLE_SECONDS = 900.0
MERGE_POLL_SECONDS = 15.0

_CI_FAIL_BUCKETS = frozenset({"fail", "cancel"})
_CI_PASS_BUCKETS = frozenset({"pass", "skipping"})
_RETRYABLE_CI_STATES = frozenset({"pending", "unmeasured"})


def run_gh(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return GitHubAdapter(cwd).gh(args)


def gh_runner(runner: GhRunner | None, github: GitForge | None = None) -> GhRunner:
    return runner or (github.gh if github is not None else run_gh)


@dataclass
class MergeResult:
    merged: bool
    merge_commit: str | None = None
    reason: str | None = None
    already_merged: bool = False


def _gh_json(repo_dir: str, args: list[str], runner: GhRunner) -> Any:
    return parse_gh_json(runner(args, repo_dir).stdout)


def lookup_default_branch(
    repo_dir: str, runner: GhRunner | None = None, *, github: GitForge | None = None
) -> str | None:
    data = _gh_json(repo_dir, ["repo", "view", "--json", "defaultBranchRef"], gh_runner(runner, github))
    ref = data.get("defaultBranchRef") if isinstance(data, dict) else None
    name = ref.get("name") if isinstance(ref, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


def view_pr(
    repo_dir: str, pr: str, runner: GhRunner | None = None, *, github: GitForge | None = None
) -> dict[str, Any] | None:
    data = _gh_json(repo_dir, ["pr", "view", str(pr), "--json", PR_VIEW_FIELDS], gh_runner(runner, github))
    return data if isinstance(data, dict) else None


def _ci_state(repo_dir: str, pr_number: int | str, runner: GhRunner) -> str:
    checks = _gh_json(repo_dir, ["pr", "checks", str(pr_number), "--json", "bucket,name,state"], runner)
    if not isinstance(checks, list) or not checks:
        return "unmeasured"
    buckets = {str(c.get("bucket") or "").lower() for c in checks if isinstance(c, dict)}
    if buckets & _CI_FAIL_BUCKETS:
        return "red"
    if buckets - _CI_PASS_BUCKETS:
        return "pending"
    return "green"


def _settle_reason(repo_dir: str, pr_number: int | str, head_sha: str, runner: GhRunner) -> tuple[str | None, bool]:
    pr = view_pr(repo_dir, str(pr_number), runner) or {}
    if pr.get("headRefOid") != head_sha:
        return f"the PR head is {pr.get('headRefOid')!r}, not the verified head {head_sha!r}", True
    mergeable = str(pr.get("mergeable") or "UNKNOWN").upper()
    if mergeable != "MERGEABLE":
        return f"the PR is not mergeable (mergeable={mergeable})", mergeable == "UNKNOWN"
    ci = _ci_state(repo_dir, pr_number, runner)
    if ci != "green":
        return f"CI is not green on the PR head (checks {ci})", ci in _RETRYABLE_CI_STATES
    return None, False


def _await_ready(repo_dir: str, pr_number: int | str, head_sha: str, runner: GhRunner) -> str | None:
    deadline = time.monotonic() + MERGE_SETTLE_SECONDS
    while True:
        reason, retry = _settle_reason(repo_dir, pr_number, head_sha, runner)
        if reason is None or not retry or time.monotonic() >= deadline:
            return reason
        time.sleep(MERGE_POLL_SECONDS)


def _commit_env() -> dict[str, str]:
    env = os.environ.copy()
    for key, value in EXECUTOR_GIT_ENV.items():
        env.setdefault(key, value)
    return env


def _merge_in_worktree(worktree: str, source_ref: str, message: str) -> tuple[str | None, tuple[str, ...]]:
    merge = run_git(["merge", "--no-ff", "-m", message, source_ref], cwd=worktree, check=False, env=_commit_env())
    if merge.returncode == 0:
        return None, ()
    unmerged = run_git(["diff", "--name-only", "--diff-filter=U"], cwd=worktree, check=False).stdout or ""
    conflicts = tuple(sorted(path for path in unmerged.splitlines() if path))
    if resolve_ledger_only_conflicts(worktree):
        commit = run_git(["commit", "--no-edit"], cwd=worktree, check=False, env=_commit_env())
        if commit.returncode == 0:
            return None, ()
    run_git(["merge", "--abort"], cwd=worktree, check=False)
    files = f" in {', '.join(conflicts)}" if conflicts else ""
    scope = "outside holon-knowledge/ledger/*.jsonl or not a pure ledger append"
    return f"merging '{source_ref}' conflicts{files} ({scope})", conflicts


@dataclass(frozen=True)
class BranchMerge:
    target_sha: str | None
    reason: str = ""
    conflicts: tuple[str, ...] = ()
    source_sha: str | None = None
    merge_commit: str | None = None


def merge_into_branch(
    repo_dir: str, source_branch: str, target_branch: str, message: str, default_branch: str | None = None
) -> tuple[str | None, str]:
    merged = merge_branch_detailed(repo_dir, source_branch, target_branch, message, default_branch)
    return merged.target_sha, merged.reason


def _fetched_tips(repo_dir: str, source_branch: str, target_branch: str) -> tuple[tuple[str, str], str]:
    fetch_error = _fetch_refspecs_error(
        repo_dir, [f"+refs/heads/{b}:refs/remotes/origin/{b}" for b in (source_branch, target_branch)]
    )
    if fetch_error:
        return ("", ""), f"fetching '{source_branch}' and '{target_branch}' failed: {fetch_error}"
    refs = [f"refs/remotes/origin/{target_branch}", f"refs/remotes/origin/{source_branch}"]
    tips = run_git(["rev-parse", *refs], cwd=repo_dir, check=False).stdout.split()
    if len(tips) != 2:
        return ("", ""), f"'{source_branch}' or '{target_branch}' does not resolve after the fetch"
    return (tips[0], tips[1]), ""


def merge_branch_detailed(
    repo_dir: str, source_branch: str, target_branch: str, message: str, default_branch: str | None = None
) -> BranchMerge:
    target = _bare_branch(target_branch)
    if not target or target in PROTECTED_BRANCHES or target == _bare_branch(default_branch):
        reason = f"refusing to push to '{target}': it is empty, main, master or the repository default branch"
        return BranchMerge(None, reason)
    source_ref, target_ref = f"refs/remotes/origin/{source_branch}", f"refs/remotes/origin/{target_branch}"
    tips, fetch_error = _fetched_tips(repo_dir, source_branch, target_branch)
    if fetch_error:
        return BranchMerge(None, fetch_error)
    target_sha, source_sha = tips
    if run_git(["merge-base", "--is-ancestor", source_sha, target_sha], cwd=repo_dir, check=False).returncode == 0:
        return BranchMerge(target_sha, source_sha=source_sha)
    tmp_root = tempfile.mkdtemp(prefix="holon-flow-merge-")
    worktree = os.path.join(tmp_root, "worktree")
    try:
        run_git(["worktree", "add", "--detach", worktree, target_ref], cwd=repo_dir)
        reason, conflicts = _merge_in_worktree(worktree, source_ref, message)
        if reason:
            return BranchMerge(None, reason, conflicts)
        pushed_sha, merged_sha = run_git(["rev-parse", "HEAD", "HEAD^2"], cwd=worktree).stdout.split()
        run_git(["push", "origin", f"HEAD:refs/heads/{target_branch}"], cwd=worktree, timeout=300)
        run_git(["update-ref", target_ref, pushed_sha], cwd=repo_dir, check=False)
        return BranchMerge(pushed_sha, source_sha=merged_sha, merge_commit=pushed_sha)
    except (RuntimeError, TimeoutError, OSError) as exc:
        return BranchMerge(None, redact_agent_secrets(str(exc)))
    finally:
        run_git(["worktree", "remove", "--force", worktree], cwd=repo_dir, check=False)
        run_git(["worktree", "prune"], cwd=repo_dir, check=False)
        shutil.rmtree(tmp_root, ignore_errors=True)


def _merged_result(pr: dict[str, Any], plan_branch: str, already_merged: bool) -> MergeResult:
    base = _bare_branch(pr.get("baseRefName"))
    if base != _bare_branch(plan_branch):
        return MergeResult(False, reason=f"the PR is merged into '{base}', not '{plan_branch}'")
    commit = pr.get("mergeCommit")
    oid = commit.get("oid") if isinstance(commit, dict) else None
    return MergeResult(True, merge_commit=oid, already_merged=already_merged)


def _guard(
    repo_dir: str, pr: dict[str, Any], plan_branch: str, expected_head: str | None, runner: GhRunner
) -> tuple[str | None, str | None]:
    default = lookup_default_branch(repo_dir, runner)
    reason = merge_refusal_reason(pr.get("baseRefName"), plan_branch, default)
    head = _bare_branch(pr.get("headRefName"))
    if reason is None and (not head or head in PROTECTED_BRANCHES or head == _bare_branch(default)):
        reason = f"the PR head '{head}' is empty, main, master or the repository default branch '{default}'"
    if reason is None and expected_head is not None and head != _bare_branch(expected_head):
        reason = f"the PR head '{head}' is not the child's execution branch '{_bare_branch(expected_head)}'"
    return reason, default


def _reread_and_guard(
    repo_dir: str, pr_number: Any, plan_branch: str, expected_head: str | None, gh: GhRunner
) -> str | None:
    fresh_pr = view_pr(repo_dir, str(pr_number), gh) or {}
    return _guard(repo_dir, fresh_pr, plan_branch, expected_head, gh)[0]


def _verified_head(
    repo_dir: str, pr: dict[str, Any], plan_branch: str, expected_head: str | None, gh: GhRunner
) -> tuple[str | None, str]:
    number, state = pr.get("number"), str(pr.get("state") or "").upper()
    reason = f"PR #{number} is {state or 'in an unknown state'}" if state != "OPEN" else None
    if reason:
        return None, reason
    reason, default = _guard(repo_dir, pr, plan_branch, expected_head, gh)
    if reason:
        return None, reason
    head_branch, message = _bare_branch(pr.get("headRefName")), f"merge: bring {plan_branch} in"
    head_sha, merge_error = merge_into_branch(repo_dir, _bare_branch(plan_branch), head_branch, message, default)
    if head_sha is None:
        return None, f"bringing '{plan_branch}' into the child failed: {merge_error}"
    reason = _await_ready(repo_dir, number, head_sha, gh)
    reason = reason or _reread_and_guard(repo_dir, number, plan_branch, expected_head, gh)
    return (None, reason) if reason else (head_sha, "")


def _merge_argv(pr_number: int | str, flag: str, head_sha: str | None, reason: str) -> tuple[list[str] | None, str]:
    if head_sha is None:
        return None, reason
    try:
        return merge_argv(pr_number, flag, head_sha), ""
    except ValueError as exc:
        return None, str(exc)


def merge_child_pr(
    repo_dir: str,
    pr_number: int | str,
    plan_branch: str,
    runner: GhRunner | None = None,
    expected_head: str | None = None,
) -> MergeResult:
    gh = gh_runner(runner)
    pr = view_pr(repo_dir, str(pr_number), gh)
    if pr is None:
        return MergeResult(False, reason=f"PR #{pr_number} could not be read with gh")
    if str(pr.get("state") or "").upper() == "MERGED":
        return _merged_result(pr, plan_branch, already_merged=True)
    flag, reason = lookup_merge_flag(repo_dir, gh)
    if flag is None:
        return MergeResult(False, reason=f"no usable merge method for PR #{pr_number}: {reason}")
    head_sha, reason = _verified_head(repo_dir, pr, plan_branch, expected_head, gh)
    argv, reason = _merge_argv(pr_number, flag, head_sha, reason)
    if argv is None:
        return MergeResult(False, reason=reason)
    merge = gh(argv, repo_dir)
    after = view_pr(repo_dir, str(pr_number), gh) or {}
    if str(after.get("state") or "").upper() != "MERGED":
        detail = redact_agent_secrets(f"{merge.stdout or ''} {merge.stderr or ''}".strip()) or "no output"
        return MergeResult(False, reason=f"gh pr merge did not merge PR #{pr_number}: {detail}")
    return _merged_result(after, plan_branch, already_merged=False)
