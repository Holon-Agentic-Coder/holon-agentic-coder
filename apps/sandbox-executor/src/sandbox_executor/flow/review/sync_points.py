"""Stage 4 target-sync points: pre-iteration, pre-post, and post-resolver-turn ``sync_with_target`` helpers."""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Callable
from typing import Any

from sandbox_executor.entrypoint.execution_workspace import workspace_mounted
from sandbox_executor.flow import target_sync_hooks as _hooks
from sandbox_executor.flow.conflict_classes import ConflictClass
from sandbox_executor.flow.conflict_resolver_role import conflict_code_resolver
from sandbox_executor.flow.context import FlowContext, FlowStage
from sandbox_executor.flow.git_refs import _resolve_branch_ref, _track_local_branch
from sandbox_executor.flow.github.merge import PROTECTED_BRANCHES, _bare_branch
from sandbox_executor.flow.review.ledger_commit import _commit_pending_ledger_rows
from sandbox_executor.flow.review.main_baseline_guard import MAIN_BASELINE_FAILING, guard_target_sync
from sandbox_executor.flow.review.sync_scope import compute_rereview_scope as compute_rereview_scope
from sandbox_executor.flow.review.sync_scope import format_conflict_block_reason as format_conflict_block_reason
from sandbox_executor.flow.review.sync_scope import is_mechanical_only as is_mechanical_only
from sandbox_executor.flow.review.sync_scope import record_rereview_scope as record_rereview_scope
from sandbox_executor.flow.target_probe import TargetProbeError, probe_target
from sandbox_executor.flow.target_sync import sync_with_target
from sandbox_executor.flow.target_sync_result import SyncBoundary, SyncRequest, SyncResult
from sandbox_executor.review_target import ReviewTarget

_STRICT_BOUNDARIES = frozenset({SyncBoundary.PUSH, SyncBoundary.CALIBRATE})


def is_ledger_only_sync(result: SyncResult) -> bool:
    """True when ``result`` merged and every resolved conflict was a JSONL ledger."""
    return (
        result.status == "merged"
        and bool(result.resolutions)
        and all(c == ConflictClass.LEDGER.value for _, c, _ in result.resolutions)
    )


def _precheck_review_sync(
    context: FlowContext, branch: str, target_branch: str, boundary: SyncBoundary
) -> SyncResult | None:
    strict = boundary in _STRICT_BOUNDARIES
    if context.dry_run or workspace_mounted():
        return SyncResult("unchanged", "", "", "")
    if not branch or branch == "HEAD":
        err = "cannot sync detached HEAD or empty branch" if strict else ""
        return SyncResult("blocked" if strict else "unchanged", "", "", "", error=err)
    if _bare_branch(branch) in PROTECTED_BRANCHES:
        return SyncResult("blocked", "", "", "", error=f"refusing to push to protected branch '{branch}'")
    worktree = context.target_worktree
    if not _hooks._can_sync_worktree(worktree, target_branch):
        err = f"fetching '{target_branch}' and '{branch}' failed" if strict else ""
        return SyncResult("blocked" if strict else "unchanged", "", "", "", error=err)
    if _resolve_branch_ref(worktree, branch, prefer_remote=True) is None:
        err = f"branch '{branch}' could not be resolved" if strict else ""
        return SyncResult("blocked" if strict else "unchanged", "", "", "", error=err)
    return None


def sync_review_head(
    context: FlowContext,
    branch: str,
    target_branch: str,
    boundary: SyncBoundary,
    *,
    stage: FlowStage = FlowStage.REVIEW,
) -> SyncResult:
    """Run ``sync_with_target`` on ``branch`` against ``target_branch`` using ``conflict_code_resolver``."""
    if early := _precheck_review_sync(context, branch, target_branch, boundary):
        return early
    worktree = context.target_worktree
    prev, detached, checkout_err = _hooks._checkout_active_branch(context, branch)
    if checkout_err is not None:
        return SyncResult("blocked", "", "", "", error=checkout_err)
    try:
        probe = probe_target(worktree, target_branch, getattr(context, _hooks._PROBE_ATTR, None))
        if not probe.moved:
            result = SyncResult("unchanged", probe.head_sha, probe.head_sha, probe.target_sha, probe=probe)
        elif boundary is SyncBoundary.TURN and not probe.conflicted_paths:
            result = SyncResult("deferred", probe.head_sha, probe.head_sha, probe.target_sha, probe=probe)
        elif baseline := guard_target_sync(context, target_branch, branch):
            err = f"{MAIN_BASELINE_FAILING}: {baseline.reason}"
            result = SyncResult("blocked", probe.head_sha, probe.head_sha, probe.target_sha, probe=probe, error=err)
        else:
            if _commit_pending_ledger_rows(context, context.review_iterations_done, "before target sync"):
                probe = None
            ledger, exec_id = (
                os.path.join(worktree, _hooks._LEDGER_REL),
                _hooks._execution_id_of(branch, stage, boundary),
            )
            resolver = conflict_code_resolver(context)
            req = SyncRequest(worktree, branch, target_branch, stage.value, boundary, ledger, exec_id, probe, resolver)
            result = sync_with_target(req, probe=probe)
    except TargetProbeError as exc:
        return SyncResult("blocked", "", "", "", error=str(exc))
    finally:
        _hooks._restore_checked_out_branch(worktree, prev, detached, branch)
    if result.probe is not None:
        setattr(context, _hooks._PROBE_ATTR, result.probe)
    if result.status == "merged":
        _track_local_branch(context, branch)
        setattr(
            context, _hooks._NOTICE_ATTR, _hooks.format_target_merge_notice(result.target_sha, result.changed_paths)
        )
    return result


def _apply_turn_sync(
    context: FlowContext, resolved_target: ReviewTarget | None, target_branch: str, label: str
) -> tuple[ReviewTarget | None, str | None]:
    branch = (resolved_target.head_branch if resolved_target else None) or context.execution_branch or ""
    if context.dry_run or not branch:
        return resolved_target, None
    result = sync_review_head(context, branch, target_branch, SyncBoundary.TURN)
    if result.status == "blocked":
        if str(result.error or "").startswith(MAIN_BASELINE_FAILING):
            context.log(f"[Review Loop] Target sync {label} blocked ({result.error}).")
            return resolved_target, str(result.error)
        reason = format_conflict_block_reason(result, target_branch)
        context.log(f"[Review Loop] Target sync {label} blocked: {reason}.")
        return resolved_target, f"target sync blocked: {reason}"
    if result.status == "merged":
        record_rereview_scope(context, compute_rereview_scope(result))
        if resolved_target is not None and result.head_after:
            resolved_target = dataclasses.replace(resolved_target, head_sha=result.head_after)
        context.log(f"[Review Loop] Target sync {label} merged '{target_branch}' into '{branch}'.")
    return resolved_target, None


def sync_before_review_iteration(
    context: FlowContext, resolved_target: ReviewTarget | None, target_branch: str, iteration: int
) -> tuple[ReviewTarget | None, str | None]:
    """Sync the reviewed branch before reviewer dispatch."""
    return _apply_turn_sync(context, resolved_target, target_branch, f"before iteration {iteration}")


def sync_iteration_mergeability(
    context: FlowContext,
    resolved_target: ReviewTarget | None,
    target_branch: str,
    iteration: int,
    mergeability_probe: Callable[[], dict[str, Any]],
) -> tuple[ReviewTarget | None, dict[str, Any], bool, str | None]:
    """Probe mergeability, run pre-iteration sync, and re-probe when a merge advanced the head."""
    mergeability = mergeability_probe()
    before_sha = resolved_target.head_sha if resolved_target is not None else ""
    updated, sync_error = sync_before_review_iteration(context, resolved_target, target_branch, iteration)
    if sync_error is None and updated and updated.head_sha and updated.head_sha != before_sha:
        mergeability = mergeability_probe()
    return updated, mergeability, bool(mergeability.get("is_dirty")), sync_error


def sync_after_resolver_turn(
    context: FlowContext, resolved_target: ReviewTarget | None, target_branch: str, iteration: int
) -> tuple[ReviewTarget | None, str | None]:
    """Sync the reviewed branch after a resolver turn completes."""
    return _apply_turn_sync(context, resolved_target, target_branch, f"after resolver iteration {iteration}")


def finalize_resolver_turn(context: FlowContext, target: ReviewTarget, iteration: int, outcome: Any) -> Any:
    """Run post-turn target sync on a successful resolver outcome and update ``head_sha``/``pushed``."""
    if outcome.error or context.dry_run:
        return outcome
    if outcome.pushed and hasattr(context, "review_scope_paths"):
        context.review_scope_paths = []
    updated = dataclasses.replace(target, head_sha=outcome.head_sha) if outcome.head_sha else target
    synced, sync_err = sync_after_resolver_turn(
        context, updated, str(context.intent_data.get("target_branch") or "main"), iteration
    )
    if sync_err:
        return dataclasses.replace(outcome, error=sync_err)
    if synced and synced.head_sha and synced.head_sha != outcome.head_sha:
        return dataclasses.replace(outcome, head_sha=synced.head_sha, pushed=True)
    return outcome
