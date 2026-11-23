"""Merge the probed target into the flow branch: resolve, verify, record, commit and push, or abort and block.

The merge is a normal ``--no-ff`` merge of the probed target SHA (never a rebase, never a forced push). ``--no-commit``
stops before the merge commit so the ``target_sync`` ledger row is part of it. Any unhandled error aborts the merge,
so the worktree is never left half-merged; a merge that cannot be completed is aborted before its row is appended.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from sandbox_executor.flow.conflict_classes import classify_merge_conflicts
from sandbox_executor.flow.conflict_classes_resolve import resolve_mechanical
from sandbox_executor.flow.host_remote import HostRemoteError, push_commit
from sandbox_executor.flow.target_probe import TargetProbe
from sandbox_executor.flow.target_sync_record import append_sync_row
from sandbox_executor.flow.target_sync_result import SyncRequest, SyncResult
from sandbox_executor.git import run_git

logger = logging.getLogger(__name__)

_MARKER_PREFIXES = ("<<<<<<< ", ">>>>>>> ")


def _git_out(worktree: str, *args: str) -> str:
    res = run_git(list(args), cwd=worktree, check=False)
    return res.stdout if res.returncode == 0 and isinstance(res.stdout, str) else ""


def merge_in_progress(worktree: str) -> bool:
    """Whether ``worktree`` holds an unfinished merge (``MERGE_HEAD`` exists)."""
    return run_git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=worktree, check=False).returncode == 0


def unmerged_paths(worktree: str) -> list[str]:
    """The paths the in-progress merge left unmerged, in git's order."""
    return [path for path in _git_out(worktree, "diff", "--name-only", "--diff-filter=U", "-z").split("\0") if path]


def _has_markers(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    return any(line.startswith(_MARKER_PREFIXES) for line in lines)


def merge_verification_error(worktree: str) -> str:
    """Why the staged merge must not be committed (unmerged paths, conflict markers), or ``""``."""
    if unmerged_paths(worktree):
        return f"unmerged paths remain: {', '.join(unmerged_paths(worktree))}"
    staged = [p for p in _git_out(worktree, "diff", "--cached", "--name-only", "-z", "HEAD").split("\0") if p]
    marked = [p for p in staged if (Path(worktree) / p).is_file() and _has_markers(Path(worktree) / p)]
    return f"conflict markers remain in: {', '.join(marked)}" if marked else ""


def abort_merge(worktree: str) -> None:
    """Abort the in-progress merge, if any, restoring the pre-merge worktree."""
    if merge_in_progress(worktree):
        run_git(["merge", "--abort"], cwd=worktree, check=False)


def _resolve(request: SyncRequest, probe: TargetProbe, result: SyncResult) -> SyncResult:
    """Resolve the unmerged paths: mechanical classes first, then the injected code resolver for ``CODE`` paths."""
    conflicted = unmerged_paths(request.worktree)
    outcome = resolve_mechanical(request.worktree, classify_merge_conflicts(request.worktree, conflicted))
    resolutions = tuple((path, cls.value, "mechanical") for path, cls in outcome.resolved)
    result = replace(result, conflicted_paths=tuple(conflicted), resolutions=resolutions)
    if not outcome.code_paths:
        return result
    code = request.code_resolver(request.worktree, list(outcome.code_paths), probe)
    result = replace(
        result,
        code_paths=tuple(outcome.code_paths),
        resolver_tier=code.tier,
        attempts=code.attempts,
        test_result=code.test_result,
    )
    if not code.resolved:
        return replace(result, status="blocked", error=f"code conflicts unresolved: {', '.join(outcome.code_paths)}")
    code_resolutions = tuple((path, "code", code.tier or "code_resolver") for path in outcome.code_paths)
    return replace(result, resolutions=resolutions + code_resolutions)


def _commit_message(probe: TargetProbe, result: SyncResult) -> str:
    classes = sorted({cls for _, cls, _ in result.resolutions}) or ["clean"]
    return f"sync: merge target {probe.target_ref} at {probe.target_sha[:12]} ({', '.join(classes)})"


def _commit_and_push(request: SyncRequest, probe: TargetProbe, result: SyncResult) -> SyncResult:
    """Record the merged row, commit the merge (row included) and push it; a push failure keeps the local commit."""
    result = append_sync_row(request, replace(result, status="merged"))
    run_git(["add", "--", request.ledger_path], cwd=request.worktree, check=False)
    commit = run_git(["commit", "--no-edit", "-m", _commit_message(probe, result)], cwd=request.worktree, check=False)
    if commit.returncode != 0:
        raise RuntimeError(f"merge commit failed: {(commit.stderr or '').strip()}")
    head_after = _git_out(request.worktree, "rev-parse", "HEAD").strip()
    changed = _git_out(request.worktree, "diff", "--name-only", "-z", f"{result.head_before}..{head_after}")
    result = replace(result, head_after=head_after, changed_paths=tuple(p for p in changed.split("\0") if p))
    try:
        push_commit(request.worktree, head_after, request.branch)
    except HostRemoteError as exc:
        return replace(result, status="blocked", error=str(exc))
    return result


def _merge_and_resolve(request: SyncRequest, probe: TargetProbe, result: SyncResult) -> SyncResult:
    run_git(["merge", "--no-ff", "--no-commit", probe.target_sha], cwd=request.worktree, check=False)
    if not merge_in_progress(request.worktree):
        return replace(result, status="blocked", error="git merge did not start (is the worktree clean?)")
    result = _resolve(request, probe, result)
    if result.status == "blocked":
        return result
    error = merge_verification_error(request.worktree)
    return replace(result, status="blocked", error=error) if error else _commit_and_push(request, probe, result)


def merge_target(request: SyncRequest, probe: TargetProbe) -> SyncResult:
    """Merge ``probe.target_sha`` into the flow branch and return ``merged`` or ``blocked``, with its one ledger row.

    A blocked merge is aborted first, then its row is appended to the clean worktree (committed with the flow's next
    commit). A push failure after the merge commit returns ``blocked`` with the push error and no second row.
    """
    started = SyncResult("merged", probe.head_sha, probe.head_sha, probe.target_sha, probe=probe)
    try:
        result = _merge_and_resolve(request, probe, started)
    except Exception as exc:
        logger.warning("Target sync of %s aborted: %s", request.branch, exc)
        abort_merge(request.worktree)
        return append_sync_row(request, replace(started, status="blocked", error=str(exc)))
    if result.status == "blocked" and not result.ledger_row_id:
        abort_merge(request.worktree)
        return append_sync_row(request, replace(result, head_after=probe.head_sha))
    return result
