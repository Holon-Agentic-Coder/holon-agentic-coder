"""Adopt a target merge a flow left in progress, so a resumed flow finishes or redoes it instead of halting.

A merge counts as the flow's own only when its ``MERGE_MSG`` carries the sync's message (``SYNC_MERGE_PREFIX``) and
its ``MERGE_HEAD`` is reachable from the current target ref; any other merge (for example an operator merging by hand
a branch that already landed in the target) is never touched and is reported instead. A merge of the current target
SHA is finished when every path resolves; a merge of an older target SHA, or one that cannot be finished, is aborted,
and the flow's next boundary sync (``sync_with_target``) redoes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sandbox_executor.flow.host_remote import HostRemoteError, fetch_base
from sandbox_executor.flow.target_probe import TargetProbe
from sandbox_executor.flow.target_sync_merge import (
    SYNC_MERGE_PREFIX,
    abort_merge,
    complete_merge,
    merge_in_progress,
    unmerged_paths,
)
from sandbox_executor.flow.target_sync_result import SyncRequest, SyncResult
from sandbox_executor.git import run_git


@dataclass(frozen=True)
class MergeAdoption:
    """What adoption did: ``completed`` (merged), ``aborted`` (redo the sync) or ``refused`` (left untouched)."""

    action: str
    result: SyncResult | None = None
    error: str = ""


def _sha(worktree: str, ref: str) -> str:
    res = run_git(["rev-parse", "-q", "--verify", f"{ref}^{{commit}}"], cwd=worktree, check=False)
    return (res.stdout or "").strip() if res.returncode == 0 and isinstance(res.stdout, str) else ""


def _reachable(worktree: str, ancestor: str, descendant: str) -> bool:
    return run_git(["merge-base", "--is-ancestor", ancestor, descendant], cwd=worktree, check=False).returncode == 0


def _started_by_sync(worktree: str) -> bool:
    """Whether the in-progress merge's ``MERGE_MSG`` carries the message every sync merge is started with."""
    res = run_git(["rev-parse", "--git-path", "MERGE_MSG"], cwd=worktree, check=False)
    if res.returncode != 0 or not isinstance(res.stdout, str) or not res.stdout.strip():
        return False
    try:
        return (Path(worktree) / res.stdout.strip()).read_text(encoding="utf-8").startswith(SYNC_MERGE_PREFIX)
    except OSError:
        return False


def _target_sha(request: SyncRequest) -> str:
    """Fetch the target and return its SHA, or ``""`` when it cannot be fetched or resolved."""
    try:
        fetch_base(request.worktree, request.target_branch)
    except HostRemoteError:
        return ""
    return _sha(request.worktree, f"origin/{request.target_branch}")


def _refusal(request: SyncRequest, merge_head: str) -> tuple[str, str]:
    """The fetched target SHA and why the merge of ``merge_head`` is not the flow's own (``""`` when it is)."""
    if not _started_by_sync(request.worktree):
        return "", f"the merge of {merge_head[:12]} was not started by the target sync"
    target_sha = _target_sha(request)
    if not target_sha:
        return "", f"cannot fetch origin/{request.target_branch} to adopt the merge"
    if not _reachable(request.worktree, merge_head, target_sha):
        return target_sha, f"a merge of {merge_head[:12]} (not from origin/{request.target_branch}) is in progress"
    return target_sha, ""


def adopt_merge_in_progress(request: SyncRequest) -> MergeAdoption | None:
    """Finish, abort or refuse the merge in progress in ``request.worktree``; ``None`` when there is none."""
    worktree = request.worktree
    if not merge_in_progress(worktree):
        return None
    merge_head = _sha(worktree, "MERGE_HEAD")
    target_sha, refusal = _refusal(request, merge_head)
    if refusal:
        return MergeAdoption("refused", error=refusal)
    if merge_head != target_sha:
        abort_merge(worktree)
        return MergeAdoption("aborted", error="the merge was of an older target; redo the sync")
    target_ref, conflicted = f"origin/{request.target_branch}", tuple(unmerged_paths(worktree))
    probe = TargetProbe(target_ref, target_sha, _sha(worktree, "HEAD"), "", True, conflicted)
    result = complete_merge(request, probe)
    if result.status == "merged" or result.ledger_row_id:  # a failed push keeps the recorded merge commit
        return MergeAdoption("completed", result, result.error)
    return MergeAdoption("aborted" if not merge_in_progress(worktree) else "refused", result, result.error)
