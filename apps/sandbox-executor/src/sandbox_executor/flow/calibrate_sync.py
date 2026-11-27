from __future__ import annotations

from sandbox_executor.flow.calibrate_fetch_retry import _fetch_execution_branch_with_retry
from sandbox_executor.flow.calibrate_rebuild import (
    CalibrationSyncError,
    holds_only_calibration_commits,
    is_ancestor,
    rebuild_calibrated_branch,
    stale_calibrated_refs,
)
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.git_refs import fast_forward_local_branch
from sandbox_executor.flow.host_remote import DEFAULT_REMOTE, HostRemoteError
from sandbox_executor.git import run_git
from sandbox_executor.ref_names import calibration_branch


def _rev(repo_dir: str, ref: str, run=None) -> str:
    res = (run or run_git)(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo_dir, check=False)
    return res.stdout.strip() if res.returncode == 0 and isinstance(res.stdout, str) else ""


def _divergence_message(repo_dir: str, branch: str, local_sha: str, origin_sha: str) -> str:
    local_range = f"refs/remotes/{DEFAULT_REMOTE}/{branch}..refs/heads/{branch}"
    count = run_git(["rev-list", "--count", local_range, "--"], cwd=repo_dir, check=False)
    local_only = count.stdout.strip() if count.returncode == 0 else "some"
    return (
        f"local branch '{branch}' ({local_sha[:12]}) holds {local_only} commit(s) that origin/{branch} "
        f"({origin_sha[:12]}) does not have. Calibrating it would analyse a head the reviewers never approved. "
        f"Recover by pushing the local commits (`git push origin {branch}`) or by resetting the local branch "
        f"deliberately (`git branch -f {branch} origin/{branch}`), then resume Stage 5."
    )


def _fetch_execution_branch(context: FlowContext, branch: str) -> None:
    try:
        _fetch_execution_branch_with_retry(context, branch)
    except HostRemoteError as exc:
        if _rev(context.target_worktree, f"refs/remotes/{DEFAULT_REMOTE}/{branch}") and not context.skip_push:
            raise CalibrationSyncError(f"could not fetch origin/{branch} ({exc}); its ref may be stale.") from exc
        context.log(f"Warning: could not fetch origin/{branch} before calibration ({exc}); sync not confirmed.")


def sync_execution_ref(context: FlowContext) -> str | None:
    branch = context.execution_branch
    if context.dry_run or not branch:
        return None
    repo_dir = context.target_worktree
    _fetch_execution_branch(context, branch)
    local_sha = _rev(repo_dir, f"refs/heads/{branch}")
    origin_sha = _rev(repo_dir, f"refs/remotes/{DEFAULT_REMOTE}/{branch}")
    if not origin_sha:
        context.log(f"Stage 5 sync: origin/{branch} not found; using the local ref.")
        return local_sha or None
    if local_sha == origin_sha:
        return origin_sha
    if local_sha and not is_ancestor(repo_dir, local_sha, origin_sha):
        raise CalibrationSyncError(_divergence_message(repo_dir, branch, local_sha, origin_sha))
    if fast_forward_local_branch(context, branch, origin_sha):
        context.log(f"Stage 5 sync: fast-forwarded '{branch}' to origin head {origin_sha[:12]}.")
    else:
        context.log(f"Stage 5 sync: could not move '{branch}'; calibrating from origin head {origin_sha[:12]}.")
    return origin_sha


def prepare_calibrated_branch(context: FlowContext, start_sha: str | None) -> bool:
    branch = context.execution_branch
    if not branch or not start_sha or context.dry_run:
        return False
    repo_dir = context.target_worktree
    calibrated = calibration_branch(branch)
    local_sha = _rev(repo_dir, f"refs/heads/{calibrated}")
    remote_sha = _rev(repo_dir, f"refs/remotes/{DEFAULT_REMOTE}/{calibrated}")
    if not local_sha and not remote_sha:
        # why: run_calibrate checks out an existing local branch, so creating it here fixes its start point
        res = run_git(["branch", "--no-track", calibrated, start_sha], cwd=repo_dir, check=False)
        if res.returncode != 0:
            context.log(f"Warning: could not create '{calibrated}' at {start_sha[:12]}.")
        return res.returncode == 0
    stale = stale_calibrated_refs(repo_dir, start_sha, local_sha, remote_sha)
    if not any(stale):
        return False
    if not all(holds_only_calibration_commits(repo_dir, start_sha, sha) for sha in stale if sha):
        raise CalibrationSyncError(
            f"existing '{calibrated}' does not contain the execution head {start_sha[:12]}, so it cannot "
            f"fast-forward onto '{branch}'. Delete it (`git branch -D {calibrated}`) and resume to rebuild."
        )
    # why: a current origin /calibrated may hold calibration commits past the head; keep them so the push fast-forwards
    rebuild_calibrated_branch(context, remote_sha if remote_sha and not stale[1] else start_sha, *stale)
    return True


def discard_empty_calibrated_branch(context: FlowContext, created: bool) -> None:
    branch = context.execution_branch
    if not branch or not created:
        return
    repo_dir = context.target_worktree
    calibrated = calibration_branch(branch)
    tip = _rev(repo_dir, f"refs/heads/{calibrated}")
    head = run_git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=repo_dir, check=False)
    if tip and tip == _rev(repo_dir, f"refs/heads/{branch}") and head.stdout.strip() != calibrated:
        run_git(["branch", "-D", calibrated], cwd=repo_dir, check=False)


def record_starting_branch(context: FlowContext, run=None) -> None:
    # why: run is the caller's git runner, so a stage whose tests replace it never spawns a real git process here
    if context.dry_run or context.starting_branch:
        return
    repo_dir = context.target_worktree
    git = run or run_git
    head = git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=repo_dir, check=False)
    if head.returncode == 0 and head.stdout.strip():
        context.starting_branch = head.stdout.strip()
        return
    sha = _rev(repo_dir, "HEAD", git)
    if sha and context.execution_branch and sha == _rev(repo_dir, f"refs/heads/{context.execution_branch}", git):
        # why: the worktree is detached at the branch tip, so exit must never re-detach it
        context.starting_branch = context.execution_branch
    elif sha:
        context.starting_branch = sha
        context.starting_detached = True


def restore_starting_branch(context: FlowContext) -> None:
    target = context.starting_branch
    if context.dry_run or not target:
        return
    repo_dir = context.target_worktree
    status = run_git(["status", "--porcelain"], cwd=repo_dir, check=False)
    if status.returncode == 0 and status.stdout.strip():
        context.log("Warning: uncommitted changes remain in the worktree after Stage 5; they are carried over.")
    if target == context.execution_branch:
        context.log(f"Stage 5: the flow started on '{target}'; nothing to restore.")
        return
    args = ["checkout", "--detach", target] if context.starting_detached else ["checkout", target]
    res = run_git(args, cwd=repo_dir, check=False)
    if res.returncode != 0:
        detail = res.stderr.strip() if isinstance(res.stderr, str) else ""
        context.log(f"Warning: could not restore starting branch '{target}': {detail}")
        return
    context.log(f"Stage 5: restored the worktree to '{target[:12] if context.starting_detached else target}'.")


def publish_calibration(context: FlowContext) -> bool:
    repo_dir = context.target_worktree
    calibrated = context.calibrated_branch
    branch = context.execution_branch
    if not calibrated:
        return False
    if not context.skip_push:
        run_git(["push", "-u", DEFAULT_REMOTE, calibrated], cwd=repo_dir, check=False)
    if not branch:
        return False
    merged = run_git(["checkout", branch], cwd=repo_dir, check=False)
    if merged.returncode == 0:
        merged = run_git(["merge", "--ff-only", f"refs/heads/{calibrated}"], cwd=repo_dir, check=False)
    if merged.returncode != 0:
        detail = merged.stderr.strip() if isinstance(merged.stderr, str) else ""
        context.log(f"Warning: '{branch}' not updated to '{calibrated}' (checkout/ff-only failed): {detail}")
        return False
    pushed = not context.skip_push and run_git(["push", DEFAULT_REMOTE, branch], cwd=repo_dir, check=False)
    return bool(pushed) and pushed.returncode == 0
