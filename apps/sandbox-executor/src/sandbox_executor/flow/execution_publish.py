"""Stage 3 host publish of execution branches: a branch reaches origin only together with its ledger row.

The executor container commits its summary row before it exits; one that crashes earlier leaves a local execution
branch with no row. Such a branch stays in the host worktree (never deleted, reset or forced) and is named in the
Stage 3 log, so origin never carries an execution branch the ledger does not know. The resume salvage publishes a
never-pushed branch the same way: its row is committed on the branch before the one push that creates it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.git_refs import (
    _append_superseding_execution_row,
    _latest_execution_summary,
    _read_ledger_rows,
)
from sandbox_executor.flow.host_remote import new_local_child_branches, push_branch, remote_error
from sandbox_executor.git import run_git


def publish_recorded_executions(repo_dir: str, plan_prefix: str, before: set[str]) -> tuple[list[str], list[str]]:
    """Push each execution branch committed during dispatch whose tip carries its summary row; return (pushed, held).

    Any summary status counts (success, failure, over budget, decomposed, sync conflict).

    Raises:
        HostRemoteError: If a push fails.
    """
    pushed: list[str] = []
    held: list[str] = []
    for branch in new_local_child_branches(repo_dir, plan_prefix, "E", before):
        exec_id = branch.removesuffix("/_").rsplit("/", 1)[-1]
        rows = _read_ledger_rows(repo_dir, f"refs/heads/{branch}", "executions.jsonl")
        if _latest_execution_summary(rows, exec_id, branch) is None:
            held.append(branch)
            continue
        push_branch(repo_dir, branch)
        pushed.append(branch)
    return pushed, held


def _publish_and_report(context: FlowContext, plan_prefix: str, before: set[str]) -> None:
    _pushed, held = publish_recorded_executions(context.target_worktree, plan_prefix, before)
    for branch in held:
        context.log(f"Stage 3: execution branch '{branch}' has no ledger row; kept local and not pushed.")


def publish_execution_branches(context: FlowContext, plan_prefix: str, before: set[str]) -> str | None:
    """Publish the recorded execution branches, log the held-back ones and return a push failure, or None."""
    return remote_error(_publish_and_report, context, plan_prefix, before)


def publish_with_first_row(context: FlowContext, branch: str, build_row: Callable[[], dict[str, Any]]) -> str | None:
    """Append the row ``build_row`` returns to the never-pushed local ``branch`` and push both in one push.

    The local branch is staged as ``refs/remotes/origin/<branch>`` (which must not exist yet), so the ledger append,
    which works on that ref, commits on top of it and its push creates the branch with the row. The staged ref is
    dropped again when the append or the push fails. Returns None on success, else what failed.
    """
    repo_dir = context.target_worktree
    staged = f"refs/remotes/origin/{branch}"
    if run_git(["update-ref", staged, f"refs/heads/{branch}", ""], cwd=repo_dir, check=False).returncode != 0:
        return f"could not stage '{branch}' for its first push"
    error = _append_superseding_execution_row(context, branch, build_row())
    if error is not None:
        run_git(["update-ref", "-d", staged], cwd=repo_dir, check=False)
    return error
