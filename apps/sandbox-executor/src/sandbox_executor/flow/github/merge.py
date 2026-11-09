"""Guarded merge of a child PR into its plan branch: merge commit only, CI green and mergeable, never ``main``.

This is the only place the flow engine merges a pull request. :func:`merge_refusal_reason` is a pure, fail-closed check
run twice per merge (before the plan branch is brought into the child and immediately before the merge): it refuses
``main``, ``master``, the repository default branch, any base other than the expected plan branch, and a failed
default-branch lookup. Only ``gh pr merge <n> --merge --match-head-commit <sha>`` is ever run; never ``--squash``,
``--rebase``, ``--auto``, ``--admin`` or ``--delete-branch``. ``gh`` is reached only through an injectable runner.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.git_refs import EXECUTOR_GIT_ENV, _fetch_refspecs_error
from sandbox_executor.flow.ledger_interleave import resolve_ledger_only_conflicts
from sandbox_executor.git import run_git
from sandbox_executor.redaction import redact_agent_secrets

#: Runs ``gh <args>`` in a directory and returns the completed process; tests pass a fake.
GhRunner = Callable[[list[str], str], subprocess.CompletedProcess[str]]

PROTECTED_BRANCHES = frozenset({"main", "master"})
PR_VIEW_FIELDS = "number,state,baseRefName,headRefName,headRefOid,mergeable,mergeCommit,url"
# How long a merge waits for GitHub to recompute mergeability and for CI to settle after the head moved.
MERGE_SETTLE_SECONDS = 900.0
MERGE_POLL_SECONDS = 15.0


def run_gh(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    """Run ``gh`` with ``args`` in ``cwd``; a missing binary or a timeout becomes a failed process, never a raise."""
    env = {**os.environ, "DOCKER_CLI_HINTS": "false"}
    try:
        return subprocess.run(["gh", *args], cwd=cwd, capture_output=True, text=True, check=False, timeout=120, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["gh", *args], 127, "", redact_agent_secrets(str(exc)))


def gh_runner(runner: GhRunner | None) -> GhRunner:
    """Return ``runner``, or this module's :func:`run_gh` looked up at call time (the seam tests patch)."""
    return runner or run_gh


@dataclass
class MergeResult:
    """Outcome of :func:`merge_child_pr`: a refusal is ``merged=False`` with a ``reason``, never a raise."""

    merged: bool
    merge_commit: str | None = None
    reason: str | None = None
    already_merged: bool = False


def _bare_branch(name: str | None) -> str:
    """Strip ``refs/heads/``, ``refs/remotes/`` and ``origin/`` prefixes and surrounding blanks from a branch name."""
    clean = str(name or "").strip()
    for prefix in ("refs/heads/", "refs/remotes/", "origin/"):
        clean = clean.removeprefix(prefix)
    return clean


def merge_refusal_reason(base_branch: str | None, expected_base: str | None, default_branch: str | None) -> str | None:
    """Return why a PR with base ``base_branch`` must not be merged into ``expected_base``, or None when it may.

    Fail closed: a failed default-branch lookup (``default_branch`` None or blank), an empty base or expected base,
    ``main``, ``master`` or the default branch on either side, and any base other than the expected plan branch are
    all refused.
    """
    default = _bare_branch(default_branch)
    if not default:
        return "the repository default branch could not be determined, so the merge is refused (fail closed)"
    base, expected = _bare_branch(base_branch), _bare_branch(expected_base)
    if not base or not expected:
        return "the PR base or the expected plan branch is empty"
    for name, label in ((expected, "expected base"), (base, "PR base")):
        if name in PROTECTED_BRANCHES or name == default:
            return f"the {label} '{name}' is main, master or the repository default branch '{default}'"
    if base != expected:
        return f"the PR base '{base}' is not the expected plan branch '{expected}'"
    return None


def _gh_json(repo_dir: str, args: list[str], runner: GhRunner) -> Any:
    """Run a ``gh ... --json`` command and return its parsed output, or None when it failed or printed no JSON."""
    proc = runner(args, repo_dir)
    try:
        return json.loads(proc.stdout) if proc.stdout and proc.stdout.strip() else None
    except (json.JSONDecodeError, TypeError):
        return None


def lookup_default_branch(repo_dir: str, runner: GhRunner | None = None) -> str | None:
    """Return the repository default branch from ``gh repo view``, or None on any failure (never a ``main`` guess)."""
    data = _gh_json(repo_dir, ["repo", "view", "--json", "defaultBranchRef"], gh_runner(runner))
    ref = data.get("defaultBranchRef") if isinstance(data, dict) else None
    name = ref.get("name") if isinstance(ref, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


def view_pr(repo_dir: str, pr: str, runner: GhRunner | None = None) -> dict[str, Any] | None:
    """Return the ``gh pr view`` facts of a PR (number, URL or head branch), or None when they cannot be read."""
    data = _gh_json(repo_dir, ["pr", "view", str(pr), "--json", PR_VIEW_FIELDS], gh_runner(runner))
    return data if isinstance(data, dict) else None


def _ci_state(repo_dir: str, pr_number: int | str, runner: GhRunner) -> str:
    """Return ``green``, ``pending``, ``red`` or ``unmeasured`` for a PR's checks (no checks is never green)."""
    checks = _gh_json(repo_dir, ["pr", "checks", str(pr_number), "--json", "bucket,name,state"], runner)
    if not isinstance(checks, list) or not checks:
        return "unmeasured"
    buckets = {str(c.get("bucket") or "").lower() for c in checks if isinstance(c, dict)}
    if buckets & {"fail", "cancel"}:
        return "red"
    if buckets - {"pass", "skipping"}:
        return "pending"
    return "green"


def _settle_reason(repo_dir: str, pr_number: int | str, head_sha: str, runner: GhRunner) -> tuple[str | None, bool]:
    """Return ``(reason, retry)``: why the PR is not ready to merge at ``head_sha`` and whether waiting may help."""
    pr = view_pr(repo_dir, str(pr_number), runner) or {}
    if pr.get("headRefOid") != head_sha:
        return f"the PR head is {pr.get('headRefOid')!r}, not the verified head {head_sha!r}", True
    mergeable = str(pr.get("mergeable") or "UNKNOWN").upper()
    if mergeable != "MERGEABLE":
        return f"the PR is not mergeable (mergeable={mergeable})", mergeable == "UNKNOWN"
    ci = _ci_state(repo_dir, pr_number, runner)
    if ci != "green":
        return f"CI is not green on the PR head (checks {ci})", ci == "pending"
    return None, False


def _await_ready(repo_dir: str, pr_number: int | str, head_sha: str, runner: GhRunner) -> str | None:
    """Wait (bounded by ``MERGE_SETTLE_SECONDS``) until the PR is mergeable with green CI; return a refusal or None."""
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


def _merge_in_worktree(worktree: str, source_ref: str, message: str) -> str | None:
    """Merge ``source_ref`` into the worktree's HEAD with a merge commit; return why it failed, or None."""
    merge = run_git(["merge", "--no-ff", "-m", message, source_ref], cwd=worktree, check=False, env=_commit_env())
    if merge.returncode == 0:
        return None
    if resolve_ledger_only_conflicts(worktree):
        commit = run_git(["commit", "--no-edit"], cwd=worktree, check=False, env=_commit_env())
        if commit.returncode == 0:
            return None
    run_git(["merge", "--abort"], cwd=worktree, check=False)
    return f"merging '{source_ref}' conflicts outside holon-knowledge/ledger/*.jsonl or is not a pure ledger append"


def merge_into_branch(repo_dir: str, source_branch: str, target_branch: str, message: str) -> tuple[str | None, str]:
    """Merge ``origin/source_branch`` into ``target_branch`` on origin with a merge commit, in a temporary worktree.

    Both branches are fetched first. When the source is already an ancestor of the target nothing is written.
    Ledger-only conflicts are resolved with :mod:`sandbox_executor.flow.ledger_interleave`; any other conflict aborts
    the merge and nothing is pushed. The operator's checkout is never touched.

    Returns:
        tuple[str | None, str]: ``(target_sha, "")`` with the target's tip on origin after the merge, or
        ``(None, reason)`` when it could not be merged or pushed.
    """
    fetch_error = _fetch_refspecs_error(
        repo_dir, [f"+refs/heads/{b}:refs/remotes/origin/{b}" for b in (source_branch, target_branch)]
    )
    if fetch_error:
        return None, f"fetching '{source_branch}' and '{target_branch}' failed: {fetch_error}"
    source_ref, target_ref = f"refs/remotes/origin/{source_branch}", f"refs/remotes/origin/{target_branch}"
    target_sha = run_git(["rev-parse", target_ref], cwd=repo_dir, check=False).stdout.strip()
    if run_git(["merge-base", "--is-ancestor", source_ref, target_ref], cwd=repo_dir, check=False).returncode == 0:
        return target_sha, ""
    tmp_root = tempfile.mkdtemp(prefix="holon-flow-merge-")
    worktree = os.path.join(tmp_root, "worktree")
    try:
        run_git(["worktree", "add", "--detach", worktree, target_ref], cwd=repo_dir)
        reason = _merge_in_worktree(worktree, source_ref, message)
        if reason:
            return None, reason
        pushed_sha = run_git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()
        run_git(["push", "origin", f"HEAD:refs/heads/{target_branch}"], cwd=worktree, timeout=300)
        run_git(["update-ref", target_ref, pushed_sha], cwd=repo_dir, check=False)
        return pushed_sha, ""
    except (RuntimeError, TimeoutError, OSError) as exc:
        return None, redact_agent_secrets(str(exc))
    finally:
        run_git(["worktree", "remove", "--force", worktree], cwd=repo_dir, check=False)
        run_git(["worktree", "prune"], cwd=repo_dir, check=False)
        shutil.rmtree(tmp_root, ignore_errors=True)


def _merged_result(pr: dict[str, Any], plan_branch: str, already_merged: bool) -> MergeResult:
    """Result for a merged PR: success when it merged into the plan branch, a refusal otherwise."""
    base = _bare_branch(pr.get("baseRefName"))
    if base != _bare_branch(plan_branch):
        return MergeResult(False, reason=f"the PR is merged into '{base}', not '{plan_branch}'")
    commit = pr.get("mergeCommit")
    oid = commit.get("oid") if isinstance(commit, dict) else None
    return MergeResult(True, merge_commit=oid, already_merged=already_merged)


def _guard(repo_dir: str, pr: dict[str, Any], plan_branch: str, runner: GhRunner) -> str | None:
    """Run the refusal check against a freshly read PR base and a fresh default-branch lookup."""
    return merge_refusal_reason(pr.get("baseRefName"), plan_branch, lookup_default_branch(repo_dir, runner))


def _verified_head(repo_dir: str, pr: dict[str, Any], plan_branch: str, gh: GhRunner) -> tuple[str | None, str]:
    """Guard an open PR, bring the plan branch into its head, and wait until it is ready to merge at that head.

    Returns:
        tuple[str | None, str]: ``(head_sha, "")`` when the PR may be merged at ``head_sha``, else ``(None, reason)``.
    """
    number, state = pr.get("number"), str(pr.get("state") or "").upper()
    reason = f"PR #{number} is {state or 'in an unknown state'}" if state != "OPEN" else None
    reason = reason or _guard(repo_dir, pr, plan_branch, gh)
    if reason:
        return None, reason
    head_sha, merge_error = merge_into_branch(
        repo_dir, _bare_branch(plan_branch), _bare_branch(pr.get("headRefName")), f"merge: bring {plan_branch} in"
    )
    if head_sha is None:
        return None, f"bringing '{plan_branch}' into the child failed: {merge_error}"
    reason = _await_ready(repo_dir, number, head_sha, gh)
    # Re-read the PR immediately before merging: its base may have been edited since the first check.
    reason = reason or _guard(repo_dir, view_pr(repo_dir, str(number), gh) or {}, plan_branch, gh)
    return (None, reason) if reason else (head_sha, "")


def merge_child_pr(
    repo_dir: str, pr_number: int | str, plan_branch: str, runner: GhRunner | None = None
) -> MergeResult:
    """Merge a child PR into ``plan_branch`` with a merge commit, only when every guard passes.

    Steps: read the PR (an already merged PR into the plan branch is an idempotent success); refuse through
    :func:`merge_refusal_reason`; bring the plan branch into the child head when it is not already an ancestor
    (ledger-only conflicts interleaved, anything else refused); wait, bounded, for the PR to be mergeable with green CI
    on that head; re-read the PR and re-run the refusal check; then ``gh pr merge --merge --match-head-commit`` and
    verify the PR reports ``MERGED``.
    """
    gh = gh_runner(runner)
    pr = view_pr(repo_dir, str(pr_number), gh)
    if pr is None:
        return MergeResult(False, reason=f"PR #{pr_number} could not be read with gh")
    if str(pr.get("state") or "").upper() == "MERGED":
        return _merged_result(pr, plan_branch, already_merged=True)
    head_sha, reason = _verified_head(repo_dir, pr, plan_branch, gh)
    if head_sha is None:
        return MergeResult(False, reason=reason)
    merge = gh(["pr", "merge", str(pr_number), "--merge", "--match-head-commit", head_sha], repo_dir)
    after = view_pr(repo_dir, str(pr_number), gh) or {}
    if str(after.get("state") or "").upper() != "MERGED":
        detail = redact_agent_secrets(f"{merge.stdout or ''} {merge.stderr or ''}".strip()) or "no output"
        return MergeResult(False, reason=f"gh pr merge did not merge PR #{pr_number}: {detail}")
    return _merged_result(after, plan_branch, already_merged=False)
