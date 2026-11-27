from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from sandbox_executor.flow.conflict_classes import classify_merge_conflicts
from sandbox_executor.flow.conflict_classes_resolve import resolve_mechanical
from sandbox_executor.flow.host_remote import push_commit
from sandbox_executor.flow.merge_git import git_out, has_conflict_markers
from sandbox_executor.flow.target_probe import TargetProbe
from sandbox_executor.flow.target_sync_record import append_sync_row
from sandbox_executor.flow.target_sync_result import SyncRequest, SyncResult
from sandbox_executor.git import run_git
from sandbox_executor.ledger.fragments import ledger_commit_paths

logger = logging.getLogger(__name__)

# why: every merge the sync starts carries this message, so a resume adopts only merges the flow itself started
SYNC_MERGE_PREFIX = "sync: merge target "
_Step = Callable[[SyncRequest, TargetProbe, SyncResult], SyncResult]


def merge_in_progress(worktree: str) -> bool:
    return run_git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=worktree, check=False).returncode == 0


def unmerged_paths(worktree: str) -> list[str]:
    return [path for path in git_out(worktree, "diff", "--name-only", "--diff-filter=U", "-z").split("\0") if path]


def merge_verification_error(worktree: str, conflicted_paths: tuple[str, ...] | list[str]) -> str:
    remaining = unmerged_paths(worktree)
    # why: only conflicted paths are scanned, so a cleanly merged file with marker-like lines never blocks every sync
    if remaining:
        return f"unmerged paths remain: {', '.join(remaining)}"
    files = [(p, Path(worktree) / p) for p in conflicted_paths]
    marked = [p for p, file in files if file.is_file() and has_conflict_markers(file)]
    return f"conflict markers remain in: {', '.join(marked)}" if marked else ""


def abort_merge(worktree: str) -> None:
    if merge_in_progress(worktree):
        run_git(["merge", "--abort"], cwd=worktree, check=False)


def _resolve(request: SyncRequest, probe: TargetProbe, result: SyncResult) -> SyncResult:
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
        resolver_test_edits=tuple(code.test_edits),
        resolver_tier=code.tier,
        attempts=code.attempts,
        test_result=code.test_result,
    )
    if not code.resolved:
        return replace(result, status="blocked", error=f"code conflicts unresolved: {', '.join(outcome.code_paths)}")
    code_resolutions = tuple((path, "code", code.tier or "code_resolver") for path in outcome.code_paths)
    return replace(result, resolutions=resolutions + code_resolutions)


def _ledger_paths(request: SyncRequest) -> list[str]:
    return ledger_commit_paths(request.worktree, "executions.jsonl")


def _commit_message(probe: TargetProbe, result: SyncResult) -> str:
    classes = sorted({cls for _, cls, _ in result.resolutions}) or ["clean"]
    return f"{SYNC_MERGE_PREFIX}{probe.target_ref} at {probe.target_sha[:12]} ({', '.join(classes)})"


def _commit_and_push(request: SyncRequest, probe: TargetProbe, result: SyncResult) -> SyncResult:
    # why: the row joins the merge commit, so its success records the local commit and a push failure is reported
    # why: by the returned blocked result, not by a second row
    result = append_sync_row(request, replace(result, status="merged"))
    run_git(["add", "--", *_ledger_paths(request)], cwd=request.worktree, check=False)
    commit = run_git(["commit", "--no-edit", "-m", _commit_message(probe, result)], cwd=request.worktree, check=False)
    if commit.returncode != 0:
        raise RuntimeError(f"merge commit failed: {(commit.stderr or '').strip()}")
    # why: once the merge commit with its row has landed, a later error keeps the row id so the merge is never aborted
    try:
        head_after = git_out(request.worktree, "rev-parse", "HEAD").strip()
        changed = git_out(request.worktree, "diff", "--name-only", "-z", f"{result.head_before}..{head_after}")
        result = replace(result, head_after=head_after, changed_paths=tuple(p for p in changed.split("\0") if p))
        push_commit(request.worktree, head_after, request.branch)
    except Exception as exc:
        logger.warning("Target sync of %s merged locally but was not pushed: %s", request.branch, exc)
        return replace(result, status="blocked", error=str(exc) or f"push failed: {type(exc).__name__}")
    return result


def _changed_on_both_sides(worktree: str) -> tuple[str, ...]:
    base = git_out(worktree, "merge-base", "HEAD", "MERGE_HEAD").strip()
    ours, theirs = (
        git_out(worktree, "diff", "--name-only", "-z", base, side).split("\0") for side in ("HEAD", "MERGE_HEAD")
    )
    return tuple(p for p in theirs if p and p in set(ours)) if base else ()


def _complete(request: SyncRequest, probe: TargetProbe, result: SyncResult, scan: tuple[str, ...] = ()) -> SyncResult:
    result = _resolve(request, probe, result)
    if result.status == "blocked":
        return result
    error = merge_verification_error(request.worktree, tuple(dict.fromkeys((*result.conflicted_paths, *scan))))
    return replace(result, status="blocked", error=error) if error else _commit_and_push(request, probe, result)


def _merge_and_resolve(request: SyncRequest, probe: TargetProbe, result: SyncResult) -> SyncResult:
    message = _commit_message(probe, result)
    # why: a --no-ff merge of the probed SHA, never a rebase or forced push; --no-commit lets the ledger row join it
    run_git(["merge", "--no-ff", "--no-commit", "-m", message, probe.target_sha], cwd=request.worktree, check=False)
    if not merge_in_progress(request.worktree):
        return replace(result, status="blocked", error="git merge did not start (is the worktree clean?)")
    return _complete(request, probe, result)


def _guarded(request: SyncRequest, probe: TargetProbe, step: _Step, *, record_blocked: bool = True) -> SyncResult:
    started = SyncResult("merged", probe.head_sha, probe.head_sha, probe.target_sha, probe=probe)
    try:
        result = step(request, probe, started)
    except Exception as exc:
        logger.warning("Target sync of %s aborted: %s", request.branch, exc)
        result = replace(started, status="blocked", error=str(exc))
        head = git_out(request.worktree, "rev-parse", "HEAD").strip()
        # why: the merge commit with its row already landed, so it must not be aborted
        if head and head != probe.head_sha and not merge_in_progress(request.worktree):
            return replace(result, head_after=head)
    if result.status == "blocked" and not result.ledger_row_id:
        abort_merge(request.worktree)
        result = replace(result, head_after=probe.head_sha)
        return _commit_blocked_row(request, append_sync_row(request, result)) if record_blocked else result
    return result


def _commit_blocked_row(request: SyncRequest, result: SyncResult) -> SyncResult:
    # why: the blocked row is committed alone, unpushed, so the next merge of a target that changed the ledger can start
    message = f"sync: record blocked target sync of {result.target_sha[:12]}"
    # why: the row may create its fragment, and an empty pathspec would make `git commit --` commit the whole index
    ledger_paths = _ledger_paths(request)
    if not ledger_paths:
        logger.warning("Could not commit the blocked target_sync row: no ledger path exists")
        return result
    run_git(["add", "--", *ledger_paths], cwd=request.worktree, check=False)
    commit = run_git(["commit", "-q", "-m", message, "--", *ledger_paths], cwd=request.worktree, check=False)
    if commit.returncode != 0:
        logger.warning("Could not commit the blocked target_sync row: %s", (commit.stderr or "").strip())
        return result
    return replace(result, head_after=git_out(request.worktree, "rev-parse", "HEAD").strip() or result.head_after)


def merge_target(request: SyncRequest, probe: TargetProbe) -> SyncResult:
    # why: uncommitted tracked changes such as the running stage's ledger rows must never be swept into a sync commit
    if git_out(request.worktree, "status", "--porcelain", "--untracked-files=no").strip():
        logger.warning("Target sync of %s blocked: the worktree has uncommitted tracked changes", request.branch)
        error = "the worktree has uncommitted tracked changes; commit them before the target sync"
        return SyncResult("blocked", probe.head_sha, probe.head_sha, probe.target_sha, probe=probe, error=error)
    return _guarded(request, probe, _merge_and_resolve)


def complete_merge(request: SyncRequest, probe: TargetProbe) -> SyncResult:
    # why: a path staged before the interruption is no longer unmerged, so every path changed on both sides is scanned
    scan = _changed_on_both_sides(request.worktree)
    # why: an unfinishable resumed merge is aborted without a row because the caller redoes the sync
    return _guarded(request, probe, lambda req, prb, res: _complete(req, prb, res, scan), record_blocked=False)
