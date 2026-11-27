from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.checkpoint import save_checkpoint
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.git_refs import _origin_has_branch
from sandbox_executor.flow.github.token_scopes import REFRESH_COMMAND
from sandbox_executor.flow.host_remote import (
    DEFAULT_REMOTE,
    new_local_child_branches,
    push_commit,
    push_commit_once,
    remote_error,
)
from sandbox_executor.flow.partial_head import _execution_id, _execution_seq, _has_summary_row
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


def _resolved_commit(repo_dir: str, ref: str) -> str:
    res = run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo_dir, check=False)
    return res.stdout.strip() if res.returncode == 0 and isinstance(res.stdout, str) else ""


def _pin(repo_dir: str, branch: str) -> RecoveredExecution | None:
    exec_id = _execution_id(branch)
    sha = _resolved_commit(repo_dir, f"refs/heads/{branch}")
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
    context: FlowContext,
    start_time: str,
    recovery: tuple[str, dict[str, Any]] | None,
    exit_code: int,
    execution_branch: str | None = None,
) -> StageResult | None:
    if recovery is None:
        return None
    error, payload = recovery
    if execution_branch is not None:
        payload = {**payload, "execution_branch": execution_branch}
    return _stage_failure(context, FlowStage.EXECUTE, start_time, error, exit_code=exit_code, **payload)


def record_execution_before_push(context: FlowContext, plan_prefix: str, before: set[str]) -> str | None:
    branches = new_local_child_branches(context.target_worktree, plan_prefix, "E", before)
    if not branches:
        return None
    # why: the record must exist before the push so a refused push cannot lead to a second execution branch
    context.execution_branch = branch = max(branches, key=_execution_seq)
    try:
        save_checkpoint(context, context.checkpoint_path)
    except OSError as exc:
        context.log(f"Stage 3: could not record '{branch}' in the checkpoint before the host push: {exc}")
    return branch


def _restore_local_branch(repo_dir: str, branch: str) -> bool:
    local_ref = f"refs/heads/{branch}"
    if _resolved_commit(repo_dir, local_ref):
        return True
    sha = _resolved_commit(repo_dir, f"{RECOVERY_REF_PREFIX}{_execution_id(branch)}")
    return bool(sha) and run_git(["update-ref", local_ref, sha, ""], cwd=repo_dir, check=False).returncode == 0


def _reusable_execution(context: FlowContext, plan_prefix: str, existing: set[str]) -> str | None:
    branch, repo_dir = context.execution_branch, context.target_worktree
    # why: a branch already on origin keeps the resume-from-partial path, and a branch with no row must never reach it
    if not branch or not branch.startswith(f"{plan_prefix}/E-") or branch in existing:
        return None
    if _origin_has_branch(repo_dir, branch) or not _restore_local_branch(repo_dir, branch):
        return None
    return branch if _has_summary_row(repo_dir, f"refs/heads/{branch}", _execution_id(branch)) else None


def republish_recorded_execution(
    context: FlowContext, plan_prefix: str, existing: set[str]
) -> tuple[str | None, str | None]:
    branch = _reusable_execution(context, plan_prefix, existing)
    if branch is None:
        return None, None
    repo_dir = context.target_worktree
    context.log(f"Stage 3 resume: re-pushing recorded execution branch {branch} instead of re-running execute.")
    push_error = remote_error(push_commit, repo_dir, f"refs/heads/{branch}", branch, DEFAULT_REMOTE, context.log)
    if push_error is None:
        return branch, None
    kept = _pin(repo_dir, branch)
    error = rejected_push_error(push_error, [kept] if kept else [])
    context.log(error)
    return branch, error
