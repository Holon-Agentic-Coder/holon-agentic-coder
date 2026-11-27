from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.github.token_scopes import REFRESH_COMMAND
from sandbox_executor.flow.host_remote import new_local_child_branches, push_commit_once, remote_error
from sandbox_executor.git import run_git

# why: a ref in the common repository survives worktree removal and branch deletion when the host push is refused
RECOVERY_REF_PREFIX = "refs/holon/recovery/"
RECOVERY_BRANCH_PREFIX = "holon-recovery/"


@dataclass(frozen=True)
class RecoveredExecution:
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
    # why: best effort: only a refusal tied to the branch name, not the content, lets this succeed
    remote_branch = f"{RECOVERY_BRANCH_PREFIX}{exec_id}"
    pushed = remote_error(push_commit_once, repo_dir, sha, remote_branch) is None
    return RecoveredExecution(branch, local_ref, sha, remote_branch if pushed else None)


def keep_rejected_executions(repo_dir: str, plan_prefix: str, before: set[str]) -> list[RecoveredExecution]:
    branches = new_local_child_branches(repo_dir, plan_prefix, "E", before)
    return [kept for branch in branches if (kept := _pin(repo_dir, branch)) is not None]


def rejected_push_error(push_error: str, recovered: list[RecoveredExecution]) -> str:
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
    repo_dir: str,
    plan_prefix: str,
    before: set[str],
    push_error: str | None,
    log: Callable[[str], None] | None = None,
) -> tuple[str, dict[str, Any]] | None:
    if push_error is None:
        return None
    recovered = keep_rejected_executions(repo_dir, plan_prefix, before)
    payload = {
        "recovery_refs": [kept.local_ref for kept in recovered],
        "recovery_pushed": any(kept.remote_branch for kept in recovered),
    }
    error = rejected_push_error(push_error, recovered)
    if log is not None:
        log(error)
    return error, payload


def rejected_push_failure(
    context: FlowContext, start_time: str, recovery: tuple[str, dict[str, Any]] | None, exit_code: int
) -> StageResult | None:
    if recovery is None:
        return None
    error, payload = recovery
    return _stage_failure(context, FlowStage.EXECUTE, start_time, error, exit_code=exit_code, **payload)
