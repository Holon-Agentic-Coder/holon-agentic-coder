"""Stage 3 resume salvage: commit an interrupted run's leftover edits as labelled partial work.

A killed container or a usage limit leaves the executor's edits uncommitted in the mounted host worktree, and the next
dispatch refuses a dirty mount. Before dispatching a resume, the host commits those leftovers onto the failed execution
branch the worktree is on, pushes it and appends one superseding ``failure`` row carrying ``partial_head``, so the
existing partial-work lookup finds the branch. The ledgers and this run's notes are engine-owned and never committed
here, and nothing is reset, cleaned, stashed or amended.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.entrypoint.execution_workspace import ENGINE_OWNED_LEDGER_PATHS, ENGINE_OWNED_NOTES_GLOB
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.git_refs import (
    EXECUTOR_GIT_ENV,
    _append_superseding_execution_row,
    _fetch_branch_prefix_error,
    _latest_execution_summary,
    _read_ledger_rows,
    _snapshot_child_branches,
)
from sandbox_executor.flow.host_remote import push_branch, remote_error
from sandbox_executor.git import run_git
from sandbox_executor.redaction import redact_agent_secrets

_EXECUTION_BRANCH = re.compile(r"/(E-\d+-[^/]+)/_$")


class SalvageError(RuntimeError):
    """The leftovers could not be salvaged safely; the message is redacted and names the branch."""


@dataclass(frozen=True)
class SalvageResult:
    """What a salvage did: ``sha`` is None and ``files`` empty when nothing was committed."""

    branch: str | None = None
    sha: str | None = None
    files: list[str] = field(default_factory=list)


def _excluded_pathspecs(exec_id: str | None) -> list[str]:
    paths = list(ENGINE_OWNED_LEDGER_PATHS)
    if exec_id:
        paths.append(os.path.join("executions", f"{exec_id}.md"))
    return ["--", ".", *(f":(exclude,literal){path}" for path in paths)]


def leftover_paths(repo_dir: str, exec_id: str | None = None) -> list[str]:
    """Paths with uncommitted edits, deletions or non-ignored untracked files, minus engine-owned files.

    The three ledgers and the execution notes of ``exec_id`` are excluded; with no ``exec_id`` every notes file under
    ``executions/`` is treated as engine-owned.
    """
    spec = _excluded_pathspecs(exec_id)
    if exec_id is None:
        spec.append(f":(exclude){ENGINE_OWNED_NOTES_GLOB}")
    res = run_git(
        ["status", "--porcelain", "-z", "--untracked-files=all", "--no-renames", *spec], cwd=repo_dir, check=False
    )
    if res.returncode != 0 or not isinstance(res.stdout, str):
        raise SalvageError(f"could not read the worktree status: {redact_agent_secrets((res.stderr or '').strip())}")
    return [entry[3:] for entry in res.stdout.split("\0") if entry]


def _current_branch(repo_dir: str) -> str | None:
    head = run_git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=repo_dir, check=False)
    return (
        head.stdout.strip() if head.returncode == 0 and isinstance(head.stdout, str) and head.stdout.strip() else None
    )


def _salvage_branch(repo_dir: str, plan_prefix: str) -> tuple[str, str]:
    """The failed execution branch of this plan the dirty worktree is on, with its execution id.

    Raises:
        SalvageError: If HEAD is detached, or on the plan branch, ``main`` or another intent's branch.
    """
    branch = _current_branch(repo_dir)
    match = _EXECUTION_BRANCH.search(branch) if branch else None
    if not branch or not match or not branch.startswith(f"{plan_prefix}/E-"):
        raise SalvageError(
            f"the mounted workspace has uncommitted changes on '{branch or 'a detached HEAD'}', which is not an "
            f"execution branch of {plan_prefix}; refusing to commit them there. Commit or discard them in the host "
            "worktree before resuming"
        )
    return branch, match.group(1)


def _commit_leftovers(repo_dir: str, plan_branch: str, exec_id: str, files: list[str]) -> str:
    spec = _excluded_pathspecs(exec_id)
    run_git(["add", "--all", *spec], cwd=repo_dir)
    env = {**os.environ, **EXECUTOR_GIT_ENV}
    message = f"execute: {exec_id} partial work (interrupted) for plan {plan_branch}"
    literal = [f":(literal){f}" for f in files]
    run_git(["commit", "-m", redact_agent_secrets(message), "--", *literal], cwd=repo_dir, env=env)
    return run_git(["rev-parse", "HEAD"], cwd=repo_dir).stdout.strip()


def _salvage_row(context: FlowContext, branch: str, exec_id: str, sha: str, file_count: int) -> dict[str, Any]:
    local = _read_ledger_rows(context.target_worktree, f"refs/remotes/origin/{branch}", "executions.jsonl")
    prior = _latest_execution_summary(local, exec_id) or {}
    try:
        revision = int(prior.get("ledger_revision") or 0) + 1
    except (TypeError, ValueError):
        revision = 2
    row = {k: v for k, v in prior.items() if k not in ("test_pass_rate", "partial_head")}
    row.update(
        {
            "entry_type": "summary",
            "execution_id": exec_id,
            "plan_branch": context.plan_branch,
            "agent": prior.get("agent", context.agent),
            "model": prior.get("model", context.model),
            "status": "failure",
            "failure_reason": "interrupted",
            "summary": f"Interrupted run: {file_count} uncommitted file(s) committed as partial work on resume.",
            "created_at": datetime.now(UTC).isoformat(),
            "ledger_revision": max(1, revision),
            "partial_head": sha,
            "salvaged": True,
            "salvaged_file_count": file_count,
        }
    )
    return row


def _publish_partial(context: FlowContext, target: tuple[str, str], sha: str, plan_prefix: str, count: int) -> None:
    """Push the branch and record ``sha`` as its partial head.

    Raises:
        SalvageError: If the push or the ledger append fails; the local commit stays so a later resume retries.
    """
    repo_dir = context.target_worktree
    branch, exec_id = target
    error = remote_error(push_branch, repo_dir, branch) or _fetch_branch_prefix_error(repo_dir, plan_prefix)
    if error is None:
        error = _append_superseding_execution_row(context, branch, _salvage_row(context, branch, exec_id, sha, count))
    if error is not None:
        raise SalvageError(f"partial work committed as {sha[:12]} on '{branch}' but not recorded: {error}")


def _unrecorded_salvage_commit(repo_dir: str, branch: str, exec_id: str) -> tuple[str, int] | None:
    """HEAD and its file count when HEAD is this run's salvage commit and the branch ledger does not record it."""
    head = run_git(["log", "-1", "--format=%H%x00%s"], cwd=repo_dir, check=False)
    if head.returncode != 0 or not isinstance(head.stdout, str) or "\0" not in head.stdout:
        return None
    sha, subject = head.stdout.strip().split("\0", 1)
    if not subject.startswith(f"execute: {exec_id} partial work (interrupted)"):
        return None
    recorded = _latest_execution_summary(
        _read_ledger_rows(repo_dir, f"refs/remotes/origin/{branch}", "executions.jsonl"), exec_id
    )
    if (recorded or {}).get("partial_head") == sha:
        return None
    files = run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha], cwd=repo_dir, check=False)
    count = len([f for f in (files.stdout or "").splitlines() if f]) if isinstance(files.stdout, str) else 0
    return sha, count


def salvage_leftovers(context: FlowContext, plan_prefix: str) -> SalvageResult:
    """Commit the worktree's leftovers as partial work, publish the branch and record it in ``executions.jsonl``.

    Returns an empty result, touching nothing, when only engine-owned files (or nothing) are dirty and no earlier
    salvage commit is waiting to be published. An earlier salvage whose push or ledger append failed is retried
    from its local commit, so a clean worktree never hides unrecorded partial work.

    Raises:
        SalvageError: If the worktree is on the wrong branch or the commit, push or ledger append fails.
    """
    repo_dir = context.target_worktree
    if not leftover_paths(repo_dir):
        pending = None
        try:
            branch, exec_id = _salvage_branch(repo_dir, plan_prefix)
            pending = _unrecorded_salvage_commit(repo_dir, branch, exec_id)
        except SalvageError:
            return SalvageResult()
        if pending is None:
            return SalvageResult()
        sha, count = pending
        _publish_partial(context, (branch, exec_id), sha, plan_prefix, count)
        return SalvageResult(branch=branch, sha=sha)
    branch, exec_id = _salvage_branch(repo_dir, plan_prefix)
    files = leftover_paths(repo_dir, exec_id)
    if not files:
        return SalvageResult()
    try:
        sha = _commit_leftovers(repo_dir, context.plan_branch or plan_prefix, exec_id, files)
    except RuntimeError as exc:
        raise SalvageError(
            f"could not commit the partial work on '{branch}': {redact_agent_secrets(str(exc))}"
        ) from exc
    _publish_partial(context, (branch, exec_id), sha, plan_prefix, len(files))
    return SalvageResult(branch=branch, sha=sha, files=files)


def salvage_for_resume(context: FlowContext, plan_prefix: str, existing: set[str]) -> tuple[set[str], str | None]:
    """Salvage leftovers before a Stage 3 resume dispatch.

    Returns the execution-branch snapshot to use from here on (refreshed when a commit was made, as the branch may be
    new on origin) and a Stage 3 failure message, or None when the resume may go on.
    """
    try:
        salvaged = salvage_leftovers(context, plan_prefix)
    except SalvageError as exc:
        return existing, f"Execute stage failed: {exc}"
    if not salvaged.sha:
        return existing, None
    context.log(
        f"Stage 3 resume: committed {len(salvaged.files)} leftover file(s) as partial work on {salvaged.branch} "
        f"({salvaged.sha[:12]})"
    )
    return _snapshot_child_branches(context.target_worktree, plan_prefix, "E") or existing, None
