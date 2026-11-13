"""Stage 5 rebuild of the flow's own stale /calibrated branch.

A resumed flow can find the /calibrated branch of an earlier run that no longer contains the execution head (the pull
request branch moved after a conflict merge or a resolver push). When every commit the head lacks is a plain Stage 5
calibration commit, the branch is the flow's own output: it is re-pointed at the origin execution head and the
replaced SHAs are logged, so the old commits stay recoverable by SHA. Anything else is foreign work and the caller
fails loudly. The only ref ever forced is the flow's own /calibrated branch, on origin only with a lease pinned to the
SHA inspected here.
"""

from __future__ import annotations

from sandbox_executor.calibration import LEDGER_EXECUTIONS_REL
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.host_remote import DEFAULT_REMOTE
from sandbox_executor.git import run_git
from sandbox_executor.ref_names import calibration_branch

CALIBRATION_SUBJECT_PREFIX = "chore(calibration): "
CALIBRATION_DIR_PREFIXES = ("holon-knowledge/kb/", "holon-knowledge/wisdom/")


class CalibrationSyncError(RuntimeError):
    """The local execution ref cannot be reconciled with origin without losing commits."""


def _git_out(repo_dir: str, args: list[str]) -> str | None:
    res = run_git(args, cwd=repo_dir, check=False)
    return res.stdout if res.returncode == 0 and isinstance(res.stdout, str) else None


def _contains(repo_dir: str, ancestor: str, descendant: str) -> bool:
    return run_git(["merge-base", "--is-ancestor", ancestor, descendant], cwd=repo_dir, check=False).returncode == 0


def is_calibration_path(path: str) -> bool:
    """True for a path Stage 5 commits: a plans/ calibration report, the executions ledger, kb or wisdom files."""
    if path == LEDGER_EXECUTIONS_REL or path.startswith(CALIBRATION_DIR_PREFIXES):
        return True
    return path.startswith("plans/") and path.count("/") == 1 and path.endswith("_calibration.md")


def commits_missing_from_head(repo_dir: str, start_sha: str, existing_sha: str) -> list[str] | None:
    """The commits on ``existing_sha`` that ``start_sha`` lacks; None when git cannot tell."""
    out = _git_out(repo_dir, ["rev-list", f"{start_sha}..{existing_sha}"])
    return None if out is None else out.split()


def is_calibration_commit(repo_dir: str, sha: str) -> bool:
    """True for a non-merge commit with a ``chore(calibration): `` subject touching only calibration paths."""
    parents = _git_out(repo_dir, ["rev-list", "--parents", "-n", "1", sha])
    subject = _git_out(repo_dir, ["log", "-1", "--format=%s", sha])
    paths = _git_out(repo_dir, ["diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha])
    if parents is None or subject is None or paths is None or len(parents.split()) > 2:
        return False
    return subject.startswith(CALIBRATION_SUBJECT_PREFIX) and all(map(is_calibration_path, paths.splitlines()))


def holds_only_calibration_commits(repo_dir: str, start_sha: str, existing_sha: str) -> bool:
    """True when ``existing_sha`` is an ancestor of ``start_sha`` or adds only Stage 5 calibration commits to it."""
    missing = commits_missing_from_head(repo_dir, start_sha, existing_sha)
    return missing is not None and all(is_calibration_commit(repo_dir, sha) for sha in missing)


def stale_calibrated_refs(repo_dir: str, start_sha: str, local_sha: str, remote_sha: str) -> tuple[str, str]:
    """The local and remote /calibrated SHAs that do not contain ``start_sha``; '' for an absent or current ref."""
    stale = [sha if sha and not _contains(repo_dir, start_sha, sha) else "" for sha in (local_sha, remote_sha)]
    return stale[0], stale[1]


def _replace_remote_branch(context: FlowContext, calibrated: str, start_sha: str, lease_sha: str) -> None:
    repo_dir = context.target_worktree
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
        dropped.update(commits_missing_from_head(repo_dir, start_sha, sha) or [])
    return len(dropped)


def rebuild_calibrated_branch(context: FlowContext, start_sha: str, stale_local: str, stale_remote: str) -> None:
    """Re-point the flow's own /calibrated branch at ``start_sha`` and log the SHAs it replaced.

    The local branch is re-pointed when stale or absent. A stale origin branch is replaced with a lease on the SHA
    inspected, except with ``skip_push``, which leaves origin untouched.

    Raises:
        CalibrationSyncError: If the branch is not a /calibrated branch, the lease push fails or the local ref cannot
            be moved.
    """
    repo_dir = context.target_worktree
    calibrated = calibration_branch(context.execution_branch or "")
    if not calibrated.endswith("/calibrated") or calibrated == context.execution_branch:
        raise CalibrationSyncError(f"refusing to rebuild '{calibrated}': it is not a /calibrated branch.")
    dropped = _dropped_count(repo_dir, start_sha, (stale_local, stale_remote))
    if stale_remote and not context.skip_push:
        _replace_remote_branch(context, calibrated, start_sha, stale_remote)
    if stale_local or not _git_out(repo_dir, ["rev-parse", "--verify", "--quiet", f"refs/heads/{calibrated}"]):
        res = run_git(["branch", "-f", "--no-track", calibrated, start_sha], cwd=repo_dir, check=False)
        if res.returncode != 0:
            detail = res.stderr.strip() if isinstance(res.stderr, str) else ""
            raise CalibrationSyncError(f"could not re-point '{calibrated}' at {start_sha[:12]}: {detail}")
    replaced_remote = stale_remote[:12] if stale_remote and not context.skip_push else "nothing"
    context.log(
        f"Stage 5: rebuilt '{calibrated}' from origin head {start_sha[:12]}; replaced local "
        f"{stale_local[:12] or 'nothing'} and origin {replaced_remote} ({dropped} calibration commit(s) not on the "
        "execution head)."
    )
