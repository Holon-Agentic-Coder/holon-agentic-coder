"""The shared target sync: one routine every flow boundary calls to keep the flow branch merged with its target.

``sync_with_target`` probes the target (cached on the head and target SHAs), and when the target moved it merges it
with a normal merge commit, resolves the mechanical conflict classes without an agent, hands ``CODE`` paths to the
injected code resolver (the default fails closed by aborting the merge), verifies, commits, pushes through
``host_remote`` and appends exactly one ``target_sync`` row to ``executions.jsonl``. It never rebases and never forces
a push. An unchanged target costs one fetch, two ``rev-parse`` calls and one ``merge-base --is-ancestor`` call and
writes nothing.
"""

from __future__ import annotations

from sandbox_executor.flow.target_probe import probe_target
from sandbox_executor.flow.target_sync_merge import merge_target
from sandbox_executor.flow.target_sync_result import SyncBoundary, SyncRequest, SyncResult


def sync_with_target(request: SyncRequest) -> SyncResult:
    """Sync the flow branch in ``request.worktree`` with ``origin/<request.target_branch>``.

    Returns ``unchanged`` when the target is already merged (no merge, push or row), ``deferred`` when it moved without
    conflicts at a ``TURN`` boundary (merged at the next stage boundary instead), else ``merged`` or ``blocked`` with
    its single ledger row.

    Raises:
        TargetProbeError: When the target cannot be fetched or probed; the caller fails closed.
    """
    probe = probe_target(request.worktree, request.target_branch, request.last_probe)
    if not probe.moved:
        return SyncResult("unchanged", probe.head_sha, probe.head_sha, probe.target_sha, probe=probe)
    if request.boundary is SyncBoundary.TURN and not probe.conflicted_paths:
        return SyncResult("deferred", probe.head_sha, probe.head_sha, probe.target_sha, probe=probe)
    return merge_target(request, probe)
