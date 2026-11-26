"""Switch the worktree to an execution's ``/calibrated`` branch: append to it, track it, or create it."""

from __future__ import annotations

import logging

from sandbox_executor.calibration.refs import resolve_git_ref
from sandbox_executor.calibration.report import CalibrationReport
from sandbox_executor.git import run_git

logger = logging.getLogger(__name__)


def _append_to_local(calibrated_branch: str, remote_exists: bool, target_worktree: str) -> None:
    """Check out the existing local branch and fast-forward it to its remote copy when one exists."""
    res_co = run_git(["checkout", calibrated_branch], cwd=target_worktree, check=False, timeout=30)
    if res_co.returncode != 0:
        raise RuntimeError(
            f"Failed to checkout existing calibrated branch '{calibrated_branch}': {res_co.stderr.strip()}"
        )
    if remote_exists:
        res_mg = run_git(
            ["merge", "--ff-only", f"refs/remotes/origin/{calibrated_branch}"],
            cwd=target_worktree,
            check=False,
            timeout=30,
        )
        if res_mg.returncode != 0:
            logger.warning("Could not fast-forward calibrated branch to remote: %s", res_mg.stderr.strip())


def _track_remote(calibrated_branch: str, target_worktree: str) -> None:
    """Create the local branch from its remote-only copy."""
    res_co = run_git(
        ["checkout", "-b", calibrated_branch, f"refs/remotes/origin/{calibrated_branch}"],
        cwd=target_worktree,
        check=False,
        timeout=30,
    )
    if res_co.returncode != 0:
        raise RuntimeError(
            f"Failed to checkout remote calibrated branch '{calibrated_branch}': {res_co.stderr.strip()}"
        )


def _create_from_evaluated(report: CalibrationReport, execution_branch: str, target_worktree: str) -> None:
    """Create the branch at the evaluated commit, else at the resolved execution ref, else at the branch name."""
    calibrated_branch = report.calibrated_branch
    start_point = (
        report.evaluated_commit_sha
        or resolve_git_ref(execution_branch, target_worktree=target_worktree)
        or execution_branch
    )
    res_co = run_git(["checkout", "-b", calibrated_branch, start_point], cwd=target_worktree, check=False, timeout=30)
    if res_co.returncode != 0:
        err_msg = res_co.stderr.strip()
        raise RuntimeError(f"Failed to create calibrated branch '{calibrated_branch}' from '{start_point}': {err_msg}")


def switch_to_calibrated_branch(report: CalibrationReport, execution_branch: str, target_worktree: str) -> None:
    """Switch to ``report.calibrated_branch``: append to the local branch, track the remote one, or create it.

    Both existence probes (local, then remote) run first. Raises ``RuntimeError`` if the checkout fails.
    """
    calibrated_branch = report.calibrated_branch
    local_check = run_git(
        ["rev-parse", "--verify", f"refs/heads/{calibrated_branch}"], cwd=target_worktree, check=False
    )
    remote_check = run_git(
        ["rev-parse", "--verify", f"refs/remotes/origin/{calibrated_branch}"], cwd=target_worktree, check=False
    )
    local_exists = local_check.returncode == 0
    remote_exists = remote_check.returncode == 0

    if local_exists:
        _append_to_local(calibrated_branch, remote_exists, target_worktree)
    elif remote_exists:
        _track_remote(calibrated_branch, target_worktree)
    else:
        _create_from_evaluated(report, execution_branch, target_worktree)
