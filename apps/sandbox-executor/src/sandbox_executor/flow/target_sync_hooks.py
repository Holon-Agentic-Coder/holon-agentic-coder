"""Flow boundary hooks for ``sync_with_target``: flow start, stage start/end, turn boundaries, and merge notices."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime

from sandbox_executor.flow.base_files import base_branch_name
from sandbox_executor.flow.conflict_resolver_role import conflict_code_resolver
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.git_refs import _resolve_branch_ref, _track_local_branch
from sandbox_executor.flow.target_probe import TargetProbeError
from sandbox_executor.flow.target_sync import sync_with_target
from sandbox_executor.flow.target_sync_result import SyncBoundary, SyncRequest, SyncResult
from sandbox_executor.git import run_git

_PROBE_ATTR = "_last_target_probe"
_NOTICE_ATTR = "_pending_target_merge_notice"
_LEDGER_REL = "holon-knowledge/ledger/executions.jsonl"


def format_target_merge_notice(target_sha: str, changed_paths: tuple[str, ...] | list[str]) -> str:
    """Render the ``## Target branch merged`` markdown notice listing ``target_sha`` and ``changed_paths``."""
    paths = [str(p) for p in changed_paths if p]
    bullets = "\n".join(f"- `{p}`" for p in paths) if paths else "- (no file changes)"
    header = f"## Target branch merged\n\nThe target branch was merged into this flow branch at commit `{target_sha}`."
    return f"{header}\nChanged paths:\n{bullets}"


def consume_target_merge_notice(context: FlowContext | None = None) -> str:
    """Return and clear the pending target-merge notice from ``context``."""
    notice = str(getattr(context, _NOTICE_ATTR, "") or "").strip() if context is not None else ""
    if context is not None:
        setattr(context, _NOTICE_ATTR, "")
    return notice


def _checked_out_branch(worktree: str) -> str:
    res = run_git(["symbolic-ref", "-q", "--short", "HEAD"], cwd=worktree, check=False)
    return (res.stdout or "").strip() if res.returncode == 0 and isinstance(res.stdout, str) else ""


def active_flow_branch(context: FlowContext, stage: FlowStage) -> str:
    """Select the active flow branch for ``stage`` from ``context``."""
    candidates: dict[FlowStage, tuple[str | None, ...]] = {
        FlowStage.INTENT: (context.intent_branch,),
        FlowStage.PLAN: (context.plan_branch, context.intent_branch),
        FlowStage.EXECUTE: (context.execution_branch, context.plan_branch, context.intent_branch),
        FlowStage.REVIEW: (context.execution_branch, context.plan_branch),
        FlowStage.CALIBRATE: (context.calibrated_branch, context.execution_branch),
    }
    return next((branch for branch in candidates.get(stage, ()) if branch), "")


def _execution_id_of(branch: str, stage: FlowStage, boundary: SyncBoundary) -> str:
    segments = [part for part in branch.split("/") if part and part != "_"]
    for prefix in ("E-", "P-", "I-"):
        if found := next((part for part in reversed(segments) if part.startswith(prefix)), ""):
            return found
    return segments[-1] if segments else f"SYNC-{stage.value}-{boundary.value}"


def _can_sync_worktree(worktree: str, target_branch: str) -> bool:
    """True when ``worktree`` is a git repository with a resolvable ``origin/<target_branch>`` ref."""
    cmd = ["rev-parse", "--verify", "--quiet", f"origin/{target_branch}^{{commit}}"]
    res = run_git(cmd, cwd=worktree, check=False) if worktree and os.path.isdir(worktree) else None
    return bool(res and res.returncode == 0 and isinstance(res.stdout, str) and res.stdout.strip())


def _checkout_active_branch(context: FlowContext, branch: str) -> tuple[str, bool, str | None]:
    """Check out ``branch`` at its latest ref in ``context.target_worktree``; return ``(prev, detached, error)``."""
    worktree = context.target_worktree
    ref = _resolve_branch_ref(worktree, branch, prefer_remote=True)
    if ref is None:
        return "", False, None
    current = _checked_out_branch(worktree)
    if current == branch and ref == f"refs/heads/{branch}":
        return current, False, None
    detached = not current
    prev = current or (run_git(["rev-parse", "HEAD"], cwd=worktree, check=False).stdout or "").strip()
    res = run_git(["checkout", "-q", "-B", branch, ref], cwd=worktree, check=False)
    if res.returncode != 0:
        detail = (res.stderr or "").strip() if isinstance(res.stderr, str) else ""
        return prev, detached, detail or f"could not check out '{branch}' from '{ref}'"
    return prev, detached, None


def _restore_checked_out_branch(worktree: str, prev: str, detached: bool, active_branch: str) -> None:
    """Return ``worktree`` to ``prev`` if the sync checked out ``active_branch`` from another ref."""
    if prev and prev != active_branch:
        args = ["checkout", "-q", "--detach", prev] if detached else ["checkout", "-q", prev]
        run_git(args, cwd=worktree, check=False)


def _blocked_stage_failure(
    context: FlowContext, stage: FlowStage, boundary: SyncBoundary, branch: str, result: SyncResult
) -> StageResult:
    paths = ", ".join(result.conflicted_paths or result.code_paths)
    where = f" in {paths}" if paths else ""
    base = result.error or "target sync blocked"
    detail = f"{base} ({result.test_result})" if result.test_result else base
    msg = (
        f"{stage.value.capitalize()} stage failed at {boundary.value}: target sync of '{branch}' with "
        f"'origin/{base_branch_name(context)}' was blocked{where}: {detail}"
    )
    return _stage_failure(
        context,
        stage,
        datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        msg,
        sync_boundary=boundary.value,
        conflicted_paths=list(result.conflicted_paths or result.code_paths),
        sync_error=result.error,
    )


def _after_boundary_merge(context: FlowContext, stage: FlowStage, branch: str, result: SyncResult) -> None:
    _track_local_branch(context, branch)
    setattr(context, _NOTICE_ATTR, format_target_merge_notice(result.target_sha, result.changed_paths))
    if stage is FlowStage.REVIEW and getattr(context, "review_scope_paths", None):
        from sandbox_executor.flow.review.sync_points import compute_rereview_scope

        context.review_scope_paths = sorted(set(context.review_scope_paths) | set(compute_rereview_scope(result)))


def _run_boundary_sync(
    context: FlowContext, stage: FlowStage, boundary: SyncBoundary
) -> tuple[SyncResult, StageResult | None]:
    """Run ``sync_with_target`` at ``(stage, boundary)``; return ``(sync_result, failure_stage_result_or_None)``."""
    worktree, target_branch = context.target_worktree, base_branch_name(context)
    branch = active_flow_branch(context, stage)
    if context.dry_run or not branch or not _can_sync_worktree(worktree, target_branch):
        return SyncResult("unchanged", "", "", ""), None
    if _resolve_branch_ref(worktree, branch, prefer_remote=True) is None:
        return SyncResult("unchanged", "", "", ""), None
    prev, detached, checkout_err = _checkout_active_branch(context, branch)
    if checkout_err is not None:
        blocked = SyncResult("blocked", "", "", "", error=checkout_err)
        return blocked, _blocked_stage_failure(context, stage, boundary, branch, blocked)
    request = SyncRequest(
        worktree=worktree,
        branch=branch,
        target_branch=target_branch,
        stage=stage.value,
        boundary=boundary,
        ledger_path=os.path.join(worktree, _LEDGER_REL),
        execution_id=_execution_id_of(branch, stage, boundary),
        last_probe=getattr(context, _PROBE_ATTR, None),
        code_resolver=conflict_code_resolver(context),
    )
    try:
        result = sync_with_target(request)
    except TargetProbeError as exc:
        blocked = SyncResult("blocked", "", "", "", error=str(exc))
        return blocked, _blocked_stage_failure(context, stage, boundary, branch, blocked)
    finally:
        _restore_checked_out_branch(worktree, prev, detached, branch)
    if result.probe is not None:
        setattr(context, _PROBE_ATTR, result.probe)
    if result.status == "merged":
        _after_boundary_merge(context, stage, branch, result)
    failure = _blocked_stage_failure(context, stage, boundary, branch, result) if result.status == "blocked" else None
    return result, failure


def at_flow_start(context: FlowContext, first_stage: FlowStage = FlowStage.INTENT) -> StageResult | None:
    """Run ``sync_with_target`` at ``FLOW_START``; return a failed :class:`StageResult` if blocked, else ``None``."""
    return _run_boundary_sync(context, first_stage, SyncBoundary.FLOW_START)[1]


def at_stage_boundary(context: FlowContext, stage: FlowStage, boundary: SyncBoundary) -> StageResult | None:
    """Run ``sync_with_target`` at ``STAGE_START`` or ``STAGE_END``; return a failed :class:`StageResult` if blocked."""
    return _run_boundary_sync(context, stage, boundary)[1]


def at_turn_boundary(context: FlowContext, stage: FlowStage, turn_index: int = 0) -> StageResult | None:
    """Run ``sync_with_target`` at a ``TURN`` boundary; return a failed :class:`StageResult` if blocked."""
    del turn_index
    return _run_boundary_sync(context, stage, SyncBoundary.TURN)[1]


def make_turn_boundary_hook(
    worktree: str, branch: str, target_branch: str, stage: FlowStage
) -> Callable[[int], str | None]:
    """Build the ``on_turn_boundary`` callback for :func:`supervise_authoring_turn` in ``stage``."""
    ctx = FlowContext(
        target_worktree=worktree,
        intent_data={"target_branch": target_branch},
        plan_branch=branch if stage == FlowStage.PLAN else None,
        execution_branch=branch if stage == FlowStage.EXECUTE else None,
        token_reduce=False,
    )

    def _on_turn(turn_index: int) -> str | None:
        at_turn_boundary(ctx, stage, turn_index)
        return consume_target_merge_notice(ctx) or None

    return _on_turn
