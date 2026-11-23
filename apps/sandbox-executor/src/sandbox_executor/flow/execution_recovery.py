"""Keep an execution's commits when the host's Stage 3 push is refused, and say where they are.

The executor container only commits; the host pushes each new execution branch afterwards (see
:func:`~sandbox_executor.flow.host_remote.publish_new_child_branches`). When that push is refused, for example
because the GitHub token lacks the ``workflow`` scope, each new execution branch's tip is pinned under
``refs/holon/recovery/<execution id>`` in the worktree's common repository, so it survives worktree removal and
branch deletion. A best-effort push to ``holon-recovery/<execution id>`` follows: it can succeed for a refusal tied to
the branch name, but not for one tied to the commits' content (a workflow-scope refusal), and its outcome is reported.
Nothing is forced, reset or deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.github.token_scopes import REFRESH_COMMAND
from sandbox_executor.flow.host_remote import new_local_child_branches, push_commit, remote_error
from sandbox_executor.git import run_git

RECOVERY_REF_PREFIX = "refs/holon/recovery/"
RECOVERY_BRANCH_PREFIX = "holon-recovery/"


@dataclass(frozen=True)
class RecoveredExecution:
    """An execution branch whose tip is pinned on ``local_ref``; ``remote_branch`` is set when its push succeeded."""

    branch: str
    local_ref: str
    sha: str
    remote_branch: str | None


def _pin(repo_dir: str, branch: str) -> RecoveredExecution | None:
    exec_id = branch.removesuffix("/_").rsplit("/", 1)[-1]
    res = run_git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}"], cwd=repo_dir, check=False)
    sha = res.stdout.strip() if res.returncode == 0 and isinstance(res.stdout, str) else ""
    if not sha:
        return None
    local_ref = f"{RECOVERY_REF_PREFIX}{exec_id}"
    if run_git(["update-ref", local_ref, sha], cwd=repo_dir, check=False).returncode != 0:
        return None
    remote_branch = f"{RECOVERY_BRANCH_PREFIX}{exec_id}"
    pushed = remote_error(push_commit, repo_dir, sha, remote_branch) is None
    return RecoveredExecution(branch, local_ref, sha, remote_branch if pushed else None)


def keep_rejected_executions(repo_dir: str, plan_prefix: str, before: set[str]) -> list[RecoveredExecution]:
    """Pin every new local execution branch (absent from ``before``) on a recovery ref, then try to push it."""
    branches = new_local_child_branches(repo_dir, plan_prefix, "E", before)
    return [kept for branch in branches if (kept := _pin(repo_dir, branch)) is not None]


def rejected_push_error(push_error: str, recovered: list[RecoveredExecution]) -> str:
    """The stage error for a refused push: the git error, where each execution's commits were kept, and the fix."""
    parts = [f"Execute stage failed: {push_error}."]
    for kept in recovered:
        where = f"the commits of '{kept.branch}' were kept on {kept.local_ref} ({kept.sha[:12]})"
        if kept.remote_branch:
            where += f" and pushed to origin {kept.remote_branch}"
        parts.append(f"{where}; republish them with: git push origin {kept.local_ref}:refs/heads/{kept.branch}")
    if not recovered:
        parts.append("No new local execution branch was found to keep.")
    lowered = push_error.lower()
    if "workflow" in lowered and "scope" in lowered:
        parts.append(f"The token lacks the 'workflow' scope; run: {REFRESH_COMMAND}")
    return " ".join(parts)


def rejected_push_recovery(
    repo_dir: str, plan_prefix: str, before: set[str], push_error: str | None
) -> tuple[str, dict[str, Any]] | None:
    """For a refused push, keep the executions and return the stage error and payload; None when the push succeeded."""
    if push_error is None:
        return None
    recovered = keep_rejected_executions(repo_dir, plan_prefix, before)
    payload = {
        "recovery_refs": [kept.local_ref for kept in recovered],
        "recovery_pushed": any(kept.remote_branch for kept in recovered),
    }
    return rejected_push_error(push_error, recovered), payload
