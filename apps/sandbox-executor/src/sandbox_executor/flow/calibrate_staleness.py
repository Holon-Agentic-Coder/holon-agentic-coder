"""Whether a calibration report still covers the pull-request head: staleness is judged on the PR's own change.

A report evaluates one commit. The head that follows it is not stale when it differs from that commit only by
bookkeeping commits (Stage 5's own calibration commits and ledger-only commits such as the review loop's ledger rows)
and by merges of the target branch that leave the PR-own change fingerprint unchanged (the fingerprint approvals are
bound to, see ``flow.review.approval_fingerprint``). Commits are walked first-parent only, so the target's own commits
that a merge brings in are never counted; the merge commit itself is, and the fingerprint settles it.

Invariant: unknown is stale. No target branch, a fingerprint that cannot be computed (an unknown target ref, an empty
PR-own change, a git failure), an evaluated commit that is not an ancestor of the head and an unreadable range all
read as a move. "Unmoved" needs either only bookkeeping commits or two known, equal fingerprints.
"""

from __future__ import annotations

from dataclasses import dataclass

from sandbox_executor.calibration_evaluated_sha import is_calibration_commit, is_ledger_only_commit
from sandbox_executor.flow.calibrate_rebuild import is_ancestor
from sandbox_executor.flow.review.approval_fingerprint import approval_fingerprint_of, fingerprints_match
from sandbox_executor.git import run_git

ONLY_BOOKKEEPING = "only calibration and ledger commits landed after the evaluated commit"


@dataclass(frozen=True)
class HeadMoveCheck:
    """Whether the pull-request head moved past the head the calibration report evaluated, and why."""

    moved: bool
    evaluated_sha: str = ""
    current_head: str = ""
    reason: str = ""


def _same_commit(first: str, second: str) -> bool:
    """Whether two SHAs (either may be abbreviated) name the same commit."""
    return bool(first and second) and (first.startswith(second) or second.startswith(first))


def _first_parent_commits(repo_dir: str, evaluated_sha: str, current_head: str) -> list[str] | None:
    """The commits of ``evaluated..head`` on the head's first-parent chain; None when git cannot walk the range."""
    span = f"{evaluated_sha}..{current_head}"
    listed = run_git(["rev-list", "--first-parent", span, "--"], cwd=repo_dir, check=False)
    if listed.returncode != 0 or not isinstance(listed.stdout, str):
        return None
    return listed.stdout.split()


def _is_bookkeeping_commit(repo_dir: str, sha: str) -> bool:
    """A commit that never changes what a report covers: a Stage 5 calibration commit or a ledger-only commit."""
    return is_calibration_commit(repo_dir, sha) or is_ledger_only_commit(repo_dir, sha)


def _own_change_is_unchanged(
    repo_dir: str, evaluated_sha: str, current_head: str, target_branch: str | None
) -> bool | None:
    """Whether the PR-own fingerprints of the two commits are equal; None when either cannot be computed."""
    evaluated = approval_fingerprint_of(repo_dir, evaluated_sha, target_branch)
    current = approval_fingerprint_of(repo_dir, current_head, target_branch)
    if not evaluated or not current:
        return None
    return fingerprints_match(evaluated, current)


def _own_change_reason(unchanged: bool | None, own_commits: int, listed_commits: int) -> str:
    """The reason for the fingerprint verdict; a move counts only the first-parent commits that are not bookkeeping."""
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
    """Whether ``current_head`` carries a PR-own change that the report on ``evaluated_sha`` does not cover.

    Bookkeeping commits on the first-parent chain are discounted; any other commit (a merge included) is settled by
    comparing the PR-own fingerprint of the two commits against ``origin/<target_branch>``. An evaluated SHA that is
    not an ancestor of the head (a force push or rewritten history), a range git cannot walk and a fingerprint that
    cannot be computed (no ``target_branch`` included) count as a move (conservative), with the reason naming the gap.
    """

    def verdict(moved: bool, reason: str) -> HeadMoveCheck:
        return HeadMoveCheck(moved, evaluated_sha, current_head, reason)

    if not evaluated_sha or not current_head:
        return verdict(False, "the evaluated SHA or the current head is unknown")
    if _same_commit(evaluated_sha, current_head):
        return verdict(False, "the head is the evaluated commit")
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
