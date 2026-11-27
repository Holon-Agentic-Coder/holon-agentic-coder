from __future__ import annotations

from sandbox_executor.calibration_evaluated_sha import (
    CALIBRATION_DIR_PREFIXES as CALIBRATION_DIR_PREFIXES,
)
from sandbox_executor.calibration_evaluated_sha import (
    CALIBRATION_SUBJECT_PREFIX as CALIBRATION_SUBJECT_PREFIX,
)
from sandbox_executor.calibration_evaluated_sha import is_calibration_commit as is_calibration_commit
from sandbox_executor.calibration_evaluated_sha import is_calibration_path as is_calibration_path
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.host_remote import DEFAULT_REMOTE
from sandbox_executor.git import run_git
from sandbox_executor.ref_names import calibration_branch


class CalibrationSyncError(RuntimeError):
    pass


def _git_out(repo_dir: str, args: list[str]) -> str | None:
    res = run_git(args, cwd=repo_dir, check=False)
    return res.stdout if res.returncode == 0 and isinstance(res.stdout, str) else None


def is_ancestor(repo_dir: str, ancestor: str, descendant: str) -> bool:
    return run_git(["merge-base", "--is-ancestor", ancestor, descendant], cwd=repo_dir, check=False).returncode == 0


def _commits_missing_from_head(repo_dir: str, start_sha: str, existing_sha: str) -> list[str] | None:
    out = _git_out(repo_dir, ["rev-list", f"{start_sha}..{existing_sha}", "--"])
    return None if out is None else out.split()


def holds_only_calibration_commits(repo_dir: str, start_sha: str, existing_sha: str) -> bool:
    missing = _commits_missing_from_head(repo_dir, start_sha, existing_sha)
    return missing is not None and all(is_calibration_commit(repo_dir, sha) for sha in missing)


def stale_calibrated_refs(repo_dir: str, start_sha: str, local_sha: str, remote_sha: str) -> tuple[str, str]:
    stale = [sha if sha and not is_ancestor(repo_dir, start_sha, sha) else "" for sha in (local_sha, remote_sha)]
    return stale[0], stale[1]


def _replace_remote_branch(context: FlowContext, calibrated: str, start_sha: str, lease_sha: str) -> None:
    repo_dir = context.target_worktree
    # why: the lease pins the SHA inspected, so a concurrent push to origin is never overwritten
    lease = f"--force-with-lease=refs/heads/{calibrated}:{lease_sha}"
    res = run_git(
        ["push", lease, "--", DEFAULT_REMOTE, f"{start_sha}:refs/heads/{calibrated}"],
        cwd=repo_dir,
        check=False,
        timeout=300,
    )
    if res.returncode != 0:
        detail = res.stderr.strip() if isinstance(res.stderr, str) else ""
        raise CalibrationSyncError(
            f"could not replace {DEFAULT_REMOTE}/{calibrated} (lease {lease_sha[:12]}) with the execution head "
            f"{start_sha[:12]}: {detail or f'git push exited with code {res.returncode}'}"
        )
    run_git(["update-ref", f"refs/remotes/{DEFAULT_REMOTE}/{calibrated}", start_sha], cwd=repo_dir, check=False)


def _dropped_count(repo_dir: str, start_sha: str, stale: tuple[str, str]) -> int:
    dropped: set[str] = set()
    for sha in filter(None, stale):
        dropped.update(_commits_missing_from_head(repo_dir, start_sha, sha) or [])
    return len(dropped)


def rebuild_calibrated_branch(context: FlowContext, target_sha: str, stale_local: str, stale_remote: str) -> None:
    repo_dir = context.target_worktree
    calibrated = calibration_branch(context.execution_branch or "")
    if not calibrated.endswith("/calibrated") or calibrated == context.execution_branch:
        raise CalibrationSyncError(f"refusing to rebuild '{calibrated}': it is not a /calibrated branch.")
    dropped = _dropped_count(repo_dir, target_sha, (stale_local, stale_remote))
    if stale_remote and not context.skip_push:
        _replace_remote_branch(context, calibrated, target_sha, stale_remote)
    if stale_local or not _git_out(repo_dir, ["rev-parse", "--verify", "--quiet", f"refs/heads/{calibrated}"]):
        res = run_git(["branch", "-f", "--no-track", calibrated, target_sha], cwd=repo_dir, check=False)
        if res.returncode != 0:
            detail = res.stderr.strip() if isinstance(res.stderr, str) else ""
            raise CalibrationSyncError(f"could not re-point '{calibrated}' at {target_sha[:12]}: {detail}")
    # why: the replaced SHAs are logged so the dropped calibration commits stay recoverable by SHA
    replaced_remote = stale_remote[:12] if stale_remote and not context.skip_push else "nothing"
    context.log(
        f"Stage 5: re-pointed '{calibrated}' at {target_sha[:12]}; replaced local "
        f"{stale_local[:12] or 'nothing'} and origin {replaced_remote} ({dropped} calibration commit(s) not on the "
        "execution head)."
    )
