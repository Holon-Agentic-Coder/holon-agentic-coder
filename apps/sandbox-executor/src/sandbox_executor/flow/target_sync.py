from __future__ import annotations

from sandbox_executor.flow.target_probe import TargetProbe, probe_target
from sandbox_executor.flow.target_sync_merge import merge_target
from sandbox_executor.flow.target_sync_result import SyncBoundary, SyncRequest, SyncResult


def _preflight_sync(
    worktree: str,
    target_branch: str,
    boundary: SyncBoundary,
    last_probe: TargetProbe | None = None,
    *,
    head_ref: str = "HEAD",
) -> tuple[TargetProbe, SyncResult | None]:
    probe = probe_target(worktree, target_branch, last_probe, head_ref=head_ref)
    if not probe.moved:
        return probe, SyncResult("unchanged", probe.head_sha, probe.head_sha, probe.target_sha, probe=probe)
    # why: a conflict-free target that moved mid-stage is merged at the next stage boundary instead of every turn
    if boundary is SyncBoundary.TURN and not probe.conflicted_paths:
        return probe, SyncResult("deferred", probe.head_sha, probe.head_sha, probe.target_sha, probe=probe)
    return probe, None


def sync_with_target(request: SyncRequest, *, probe: TargetProbe | None = None) -> SyncResult:
    if probe is None:
        probe, early = _preflight_sync(request.worktree, request.target_branch, request.boundary, request.last_probe)
        if early is not None:
            return early
    return merge_target(request, probe)
