from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from sandbox_executor.calibration import read_git_file
from sandbox_executor.calibration_evaluated_sha import is_calibration_commit, is_ledger_only_commit
from sandbox_executor.flow.calibrate_rebuild import is_ancestor
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.host_remote import DEFAULT_REMOTE, HostRemoteError, fetch_base
from sandbox_executor.flow.review.approval_fingerprint import approval_fingerprint_of, fingerprints_match
from sandbox_executor.git import run_git

logger = logging.getLogger(__name__)

ONLY_BOOKKEEPING = "only calibration and ledger commits landed after the evaluated commit"


@dataclass(frozen=True)
class HeadMoveCheck:
    moved: bool
    evaluated_sha: str = ""
    current_head: str = ""
    reason: str = ""


def _same_commit(first: str, second: str) -> bool:
    return bool(first and second) and (first.startswith(second) or second.startswith(first))


def _fetch_tolerantly(repo_dir: str, branch: str) -> None:
    try:
        fetch_base(repo_dir, branch, DEFAULT_REMOTE)
    except HostRemoteError as exc:
        logger.debug("Could not fetch origin/%s before the staleness check: %s", branch, exc)


def _origin_head(context: FlowContext) -> str:
    branch, repo_dir = context.execution_branch or "", context.target_worktree
    _fetch_tolerantly(repo_dir, branch)
    for ref in (f"refs/remotes/{DEFAULT_REMOTE}/{branch}", f"refs/heads/{branch}"):
        res = run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo_dir, check=False)
        if res.returncode == 0 and isinstance(res.stdout, str) and res.stdout.strip():
            return res.stdout.strip()
    return ""


def _report_content(context: FlowContext, report_rel: str) -> str | None:
    branch = context.execution_branch or ""
    for ref in (f"refs/remotes/{DEFAULT_REMOTE}/{branch}", f"refs/heads/{branch}"):
        content = read_git_file(ref, report_rel, target_worktree=context.target_worktree)
        if content:
            return content
    path = os.path.join(context.target_worktree, report_rel)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    return None


def _first_parent_commits(repo_dir: str, evaluated_sha: str, current_head: str) -> list[str] | None:
    span = f"{evaluated_sha}..{current_head}"
    # why: first-parent only, so the target's own commits a merge brings in are never counted as a PR change
    listed = run_git(["rev-list", "--first-parent", span, "--"], cwd=repo_dir, check=False)
    if listed.returncode != 0 or not isinstance(listed.stdout, str):
        return None
    return listed.stdout.split()


def _is_bookkeeping_commit(repo_dir: str, sha: str) -> bool:
    return is_calibration_commit(repo_dir, sha) or is_ledger_only_commit(repo_dir, sha)


def _own_change_is_unchanged(
    repo_dir: str, evaluated_sha: str, current_head: str, target_branch: str | None
) -> bool | None:
    evaluated = approval_fingerprint_of(repo_dir, evaluated_sha, target_branch)
    current = approval_fingerprint_of(repo_dir, current_head, target_branch)
    if not evaluated or not current:
        return None
    return fingerprints_match(evaluated, current)


def _own_change_reason(unchanged: bool | None, own_commits: int, listed_commits: int) -> str:
    if unchanged:
        discounted = f"{listed_commits} commit(s) after the evaluated commit discounted"
        return f"the pull request's own change is unchanged ({discounted})"
    if unchanged is None:
        return (
            f"{own_commits} commit(s) landed after the evaluated commit and the pull request's own change could not "
            "be compared"
        )
    return f"{own_commits} commit(s) after the evaluated commit changed the pull request's own change"


def head_move_since_calibration(
    repo_dir: str, evaluated_sha: str, current_head: str, target_branch: str | None = None
) -> HeadMoveCheck:

    def verdict(moved: bool, reason: str) -> HeadMoveCheck:
        return HeadMoveCheck(moved, evaluated_sha, current_head, reason)

    if not evaluated_sha or not current_head:
        return verdict(False, "the evaluated SHA or the current head is unknown")
    if _same_commit(evaluated_sha, current_head):
        return verdict(False, "the head is the evaluated commit")
    # why: unknown is stale; a rewritten history, an unreadable range or a missing fingerprint all read as a move
    if not is_ancestor(repo_dir, evaluated_sha, current_head):
        return verdict(True, "the evaluated commit is not an ancestor of the head")
    commits = _first_parent_commits(repo_dir, evaluated_sha, current_head)
    if commits is None:
        return verdict(True, "the commits past the evaluated commit are unreadable")
    own = [sha for sha in commits if not _is_bookkeeping_commit(repo_dir, sha)]
    if not own:
        return verdict(False, ONLY_BOOKKEEPING)
    unchanged = _own_change_is_unchanged(repo_dir, evaluated_sha, current_head, target_branch)
    return verdict(not unchanged, _own_change_reason(unchanged, len(own), len(commits)))
