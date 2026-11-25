"""The shared target sync: one routine every flow boundary calls to keep the flow branch merged with its target.

``sync_with_target`` probes the target (cached on the head and target SHAs), and when the target moved it merges it
with a normal merge commit, resolves the mechanical conflict classes without an agent, hands ``CODE`` paths to the
injected code resolver (the default fails closed by aborting the merge), verifies, commits, pushes through
``host_remote`` and appends exactly one ``target_sync`` row to ``executions.jsonl``. It never rebases and never forces
a push. An unchanged target costs one fetch, two ``rev-parse`` calls and one ``merge-base --is-ancestor`` call and
writes nothing.
"""

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
    """Probe ``head_ref`` against ``origin/<target_branch>`` without checkout; return ``(probe, early_result)``."""
    probe = probe_target(worktree, target_branch, last_probe, head_ref=head_ref)
    if not probe.moved:
        return probe, SyncResult("unchanged", probe.head_sha, probe.head_sha, probe.target_sha, probe=probe)
    if boundary is SyncBoundary.TURN and not probe.conflicted_paths:
        return probe, SyncResult("deferred", probe.head_sha, probe.head_sha, probe.target_sha, probe=probe)
    return probe, None


def sync_with_target(request: SyncRequest, *, probe: TargetProbe | None = None) -> SyncResult:
    """Sync the flow branch in ``request.worktree`` with ``origin/<request.target_branch>``.

    Returns ``unchanged`` when the target is already merged (no merge, push or row), ``deferred`` when it moved without
    conflicts at a ``TURN`` boundary (merged at the next stage boundary instead), else ``merged`` or ``blocked`` with
    its single ledger row.

    Raises:
        TargetProbeError: When the target cannot be fetched or probed; the caller fails closed.
    """
    if probe is None:
        probe, early = _preflight_sync(request.worktree, request.target_branch, request.boundary, request.last_probe)
        if early is not None:
            return early
    return merge_target(request, probe)
