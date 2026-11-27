"""Stage 3 resume salvage: commit an interrupted run's leftover edits as labelled partial work.

A killed container or a usage limit leaves the executor's edits uncommitted in the mounted host worktree, and the next
dispatch refuses a dirty mount. Before dispatching a resume, the host commits those leftovers onto the failed execution
branch the worktree is on, pushes it and appends one superseding ``failure`` row carrying ``partial_head``, so the
existing partial-work lookup finds the branch. The ledgers and this run's notes are engine-owned and never committed
here, and nothing is reset, cleaned, stashed or amended.

An execution branch a host crash left with commits past the plan tip but no summary row is adopted the same way: its
leftovers, its own notes file included, are committed first and a superseding ``failure`` row records its tip as
``partial_head``. A dirty worktree that would still block the executor's checkout is reported with the file names.
"""

from __future__ import annotations

import functools
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sandbox_executor.entrypoint.execution_workspace import (
    ENGINE_OWNED_LEDGER_DIRS,
    ENGINE_OWNED_LEDGER_PATHS,
    ENGINE_OWNED_NOTES_GLOB,
    format_skipped_scratch_message,
    split_sidecar_scratch_paths,
)
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.execution_publish import publish_with_first_row
from sandbox_executor.flow.git_refs import (
    EXECUTOR_GIT_ENV,
    _append_superseding_execution_row,
    _fetch_branch_prefix_error,
    _is_ancestor_commit,
    _latest_execution_summary,
    _origin_has_branch,
    _read_ledger_rows,
    _snapshot_child_branches,
)
from sandbox_executor.flow.host_remote import push_branch, remote_error
from sandbox_executor.flow.unrecorded_branch_adoption import (
    _blocking_checkout_error,
    _checkout_blocking_paths,
    _find_unrecorded_branch,
    _resume_start,
    _UnrecordedBranch,
)
from sandbox_executor.git import run_git
from sandbox_executor.ledger.order import utc_now_z
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


def _excluded_pathspecs(exec_id: str | None, include_notes: bool = False) -> list[str]:
    paths = [*ENGINE_OWNED_LEDGER_PATHS, *ENGINE_OWNED_LEDGER_DIRS]
    if exec_id and not include_notes:
        paths.append(os.path.join("executions", f"{exec_id}.md"))
    return ["--", ".", *(f":(exclude,literal){path}" for path in paths)]


def _status_paths(
    repo_dir: str, exec_id: str | None = None, *, include_notes: bool = False
) -> tuple[list[str], list[str]]:
    spec = _excluded_pathspecs(exec_id, include_notes)
    if exec_id is None:
        spec.append(f":(exclude){ENGINE_OWNED_NOTES_GLOB}")
    res = run_git(
        ["status", "--porcelain", "-z", "--untracked-files=all", "--no-renames", *spec], cwd=repo_dir, check=False
    )
    if res.returncode != 0 or not isinstance(res.stdout, str):
        raise SalvageError(f"could not read the worktree status: {redact_agent_secrets((res.stderr or '').strip())}")
    return split_sidecar_scratch_paths([entry[3:] for entry in res.stdout.split("\0") if entry])


def leftover_paths(repo_dir: str, exec_id: str | None = None) -> list[str]:
    """Paths with uncommitted edits, deletions or non-ignored untracked files, minus engine-owned and scratch files."""
    return _status_paths(repo_dir, exec_id)[0]


def _current_branch(repo_dir: str) -> str | None:
    head = run_git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=repo_dir, check=False)
    return (
        head.stdout.strip() if head.returncode == 0 and isinstance(head.stdout, str) and head.stdout.strip() else None
    )


def _salvage_branch(repo_dir: str, plan_prefix: str) -> tuple[str, str]:
    """The failed execution branch of this plan the dirty worktree is on, with its execution id."""
    branch = _current_branch(repo_dir)
    match = _EXECUTION_BRANCH.search(branch) if branch else None
    if not branch or not match or not branch.startswith(f"{plan_prefix}/E-"):
        raise SalvageError(
            f"the mounted workspace has uncommitted changes on '{branch or 'a detached HEAD'}', which is not an "
            f"execution branch of {plan_prefix}; refusing to commit them there. Commit or discard them in the host "
            "worktree before resuming"
        )
    return branch, match.group(1)


def _commit_leftovers(
    repo_dir: str, plan_branch: str, exec_id: str, paths: tuple[list[str], list[str]], include_notes: bool = False
) -> str:
    files, skipped = paths
    spec = [*_excluded_pathspecs(exec_id, include_notes), *(f":(exclude,literal){path}" for path in skipped)]
    run_git(["add", "--all", *spec], cwd=repo_dir)
    env = {**os.environ, **EXECUTOR_GIT_ENV}
    message = f"execute: {exec_id} partial work (interrupted) for plan {plan_branch}"
    literal = [f":(literal){f}" for f in files]
    run_git(["commit", "-m", redact_agent_secrets(message), "--", *literal], cwd=repo_dir, env=env)
    return run_git(["rev-parse", "HEAD"], cwd=repo_dir).stdout.strip()


def _salvage_row(
    context: FlowContext, target: tuple[str, str], sha: str, file_count: int, extras: dict[str, Any] | None = None
) -> dict[str, Any]:
    branch, exec_id = target
    local = _read_ledger_rows(context.target_worktree, f"refs/remotes/origin/{branch}", "executions.jsonl")
    prior = _latest_execution_summary(local, exec_id, branch) or {}
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
            "created_at": utc_now_z(),
            "ledger_revision": max(1, revision),
            "partial_head": sha,
            "salvaged": True,
            "salvaged_file_count": file_count,
            **(extras or {}),
        }
    )
    return row


def _local_ahead_of_origin(repo_dir: str, branch: str) -> bool:
    """Whether the local ``branch`` exists and holds commits origin's copy lacks (so it has to be pushed)."""
    local = run_git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}"], cwd=repo_dir, check=False)
    if local.returncode != 0 or not isinstance(local.stdout, str) or not local.stdout.strip():
        return False
    return not _is_ancestor_commit(repo_dir, local.stdout.strip(), f"refs/remotes/origin/{branch}")


def _publish_partial(
    context: FlowContext, target: tuple[str, str], sha: str, plan_prefix: str, row: Callable[[], dict[str, Any]]
) -> None:
    """Push the branch and record ``sha`` as its partial head (a never-pushed branch is pushed with that row).

    A branch whose origin copy already holds every local commit is not pushed; only its superseding row is appended.

    Raises:
        SalvageError: If the push or the ledger append fails; the local commit stays so a later resume retries.
    """
    repo_dir = context.target_worktree
    branch = target[0]
    if _origin_has_branch(repo_dir, branch):
        error = remote_error(push_branch, repo_dir, branch) if _local_ahead_of_origin(repo_dir, branch) else None
        error = error or _fetch_branch_prefix_error(repo_dir, plan_prefix)
        error = error or _append_superseding_execution_row(context, branch, row())
    else:
        # Never pushed: the row is committed first and the branch reaches origin with it, never without one.
        error = publish_with_first_row(context, branch, row)
    if error is not None:
        raise SalvageError(f"partial work committed as {sha[:12]} on '{branch}' but not recorded: {error}")


def _unrecorded_salvage_commit(repo_dir: str, branch: str, exec_id: str) -> tuple[str, list[str]] | None:
    """HEAD and its files when HEAD is this run's salvage commit and no ledger row at or after it records it."""
    head = run_git(["log", "-1", "--format=%H%x00%cI%x00%s"], cwd=repo_dir, check=False)
    parts = head.stdout.strip().split("\0", 2) if head.returncode == 0 and isinstance(head.stdout, str) else []
    if len(parts) != 3 or not parts[2].startswith(f"execute: {exec_id} partial work (interrupted)"):
        return None
    sha, committed_at = parts[:2]
    recorded = _latest_execution_summary(
        _read_ledger_rows(repo_dir, f"refs/remotes/origin/{branch}", "executions.jsonl"), exec_id, branch
    )
    try:
        if recorded and datetime.fromisoformat(recorded["created_at"]) > datetime.fromisoformat(committed_at):
            return None
    except (KeyError, TypeError, ValueError):
        pass
    listing = run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha], cwd=repo_dir, check=False)
    return sha, [f for f in listing.stdout.splitlines() if f] if isinstance(listing.stdout, str) else []


def _publish_pending_salvage(context: FlowContext, plan_prefix: str, extras: dict[str, Any]) -> SalvageResult:
    """Record a salvage commit an earlier resume made but could not record; an empty result when there is none."""
    try:
        branch, exec_id = _salvage_branch(context.target_worktree, plan_prefix)
        pending = _unrecorded_salvage_commit(context.target_worktree, branch, exec_id)
    except SalvageError:
        return SalvageResult()
    if pending is None:
        return SalvageResult()
    sha, files = pending
    row = functools.partial(_salvage_row, context, (branch, exec_id), sha, len(files), extras)
    _publish_partial(context, (branch, exec_id), sha, plan_prefix, row)
    return SalvageResult(branch=branch, sha=sha, files=files)


def salvage_leftovers(context: FlowContext, plan_prefix: str, *, include_notes: bool = False) -> SalvageResult:
    """Commit the worktree's leftovers as partial work, publish the branch and record it in ``executions.jsonl``.

    ``include_notes`` is set only when adopting the unrecorded execution branch the worktree is on: its own notes file
    is then committed with the leftovers and the row is marked ``adopted_unrecorded``.
    """
    repo_dir = context.target_worktree
    extras: dict[str, Any] = {"adopted_unrecorded": True} if include_notes else {}
    notes_id = _salvage_branch(repo_dir, plan_prefix)[1] if include_notes else None
    initial_files, initial_skipped = _status_paths(repo_dir, notes_id, include_notes=include_notes)
    if initial_skipped:
        context.log(format_skipped_scratch_message(initial_skipped))
    if not initial_files:
        return _publish_pending_salvage(context, plan_prefix, extras)
    branch, exec_id = _salvage_branch(repo_dir, plan_prefix)
    files, skipped = _status_paths(repo_dir, exec_id, include_notes=include_notes)
    if not files:
        return SalvageResult()
    try:
        sha = _commit_leftovers(repo_dir, context.plan_branch or plan_prefix, exec_id, (files, skipped), include_notes)
    except RuntimeError as exc:
        raise SalvageError(
            f"could not commit the partial work on '{branch}': {redact_agent_secrets(str(exc))}"
        ) from exc
    row = functools.partial(_salvage_row, context, (branch, exec_id), sha, len(files), extras)
    _publish_partial(context, (branch, exec_id), sha, plan_prefix, row)
    return SalvageResult(branch=branch, sha=sha, files=files)


def _adopt_unrecorded(context: FlowContext, candidate: _UnrecordedBranch, plan_prefix: str) -> None:
    """Record ``candidate``'s tip as its partial head with a superseding ``failure`` row (publishing it if needed)."""
    summary = (
        f"Interrupted run: {candidate.commits_past_plan} commit(s) past the plan tip adopted on resume (host stopped "
        "before a summary row was recorded)."
    )
    target = (candidate.branch, candidate.execution_id)
    extras = {"adopted_unrecorded": True, "summary": summary}
    row = functools.partial(_salvage_row, context, target, candidate.tip, 0, extras)
    _publish_partial(context, target, candidate.tip, plan_prefix, row)
    context.log(
        f"Stage 3 resume: adopted unrecorded execution branch {candidate.branch} at {candidate.tip[:12]} "
        f"({candidate.commits_past_plan} commit(s) past the plan tip)"
    )


def _salvage_and_adopt(context: FlowContext, plan_prefix: str, existing: set[str]) -> str | None:
    """Salvage leftovers and adopt the plan's unrecorded execution branch; the branch to resume from, if any."""
    candidate = _find_unrecorded_branch(
        context.target_worktree, context.plan_branch, plan_prefix, existing, context.log
    )
    salvaged = salvage_leftovers(context, plan_prefix, include_notes=bool(candidate and candidate.checked_out))
    if salvaged.sha:
        context.log(
            f"Stage 3 resume: committed {len(salvaged.files)} leftover file(s) as partial work on {salvaged.branch} "
            f"({salvaged.sha[:12]})"
        )
    if candidate is not None and salvaged.branch != candidate.branch:
        _adopt_unrecorded(context, candidate, plan_prefix)
        return candidate.branch
    return salvaged.branch if salvaged.sha else None


def salvage_for_resume(context: FlowContext, plan_prefix: str, existing: set[str]) -> tuple[set[str], str | None]:
    """Salvage leftovers and adopt an unrecorded execution branch before a Stage 3 resume dispatch.

    Returns the execution-branch snapshot to use from here on (refreshed when a branch was recorded, as it may be new
    on origin, so the executor's next branch is the only new one) and a Stage 3 failure message, or None when the
    resume may go on. The checkpoint's execution branch follows a recorded branch.
    """
    try:
        branch = _salvage_and_adopt(context, plan_prefix, existing)
    except SalvageError as exc:
        return existing, f"Execute stage failed: {exc}"
    repo_dir = context.target_worktree
    if branch:
        context.execution_branch = branch
        existing = _snapshot_child_branches(repo_dir, plan_prefix, "E") or (existing | {branch})
    start = _resume_start(repo_dir, context.plan_branch, existing)
    blocking = _checkout_blocking_paths(repo_dir, start)
    return existing, _blocking_checkout_error(blocking, start or "") if blocking else None
