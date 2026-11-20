"""HEAD state of the shared flow worktree: on the branch, detached at its tip, or detached elsewhere.

The host flow engine and the ``review-loop`` role container share one worktree, and coding-agent turns run with shell
access inside it. This module owns the one safe repair: a HEAD detached exactly at ``refs/heads/<branch>`` is
re-attached with ``git symbolic-ref``, which rewrites only ``HEAD`` and leaves the index and the working tree (the
uncommitted review ledger rows included) untouched. Any other detached HEAD is refused with a message naming where HEAD
and the branch tip are. Nothing here forces, resets, stashes or discards a change.
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import subprocess
from collections.abc import Callable, Iterator

from sandbox_executor.git import run_git
from sandbox_executor.redaction import redact_agent_secrets

#: A git runner with ``run_git``'s calling convention (arguments without the leading ``git``).
GitRunner = Callable[..., subprocess.CompletedProcess[str]]


class ReattachStatus(enum.Enum):
    """What ``reattach_branch`` found or did."""

    ON_BRANCH = "on_branch"
    REATTACHED = "reattached"
    REFUSED = "refused"


@dataclasses.dataclass(frozen=True)
class ReattachOutcome:
    """The status of a re-attach attempt and a one-line, redacted message describing it."""

    status: ReattachStatus
    message: str


@dataclasses.dataclass(frozen=True)
class HeadState:
    """Where HEAD is relative to ``target``: the checked-out branch (None when detached), the HEAD commit and the
    commit ``refs/heads/<target>`` points at (None when the local branch is missing)."""

    target: str
    branch: str | None
    sha: str
    branch_tip: str | None

    @property
    def on_branch(self) -> bool:
        """True when ``target`` is checked out."""
        return self.branch == self.target

    @property
    def detached_at_tip(self) -> bool:
        """True when HEAD is detached exactly at the tip of the local ``target`` branch."""
        return self.branch is None and bool(self.sha) and self.sha == self.branch_tip

    @property
    def detached_elsewhere(self) -> bool:
        """True when HEAD is detached at a commit other than the local ``target`` tip."""
        return self.branch is None and bool(self.sha) and self.sha != self.branch_tip


@dataclasses.dataclass
class HeadGuardReport:
    """Filled in by ``head_guard`` when its block exits; ``outcome`` stays None when nothing needed repair."""

    outcome: ReattachOutcome | None = None

    @property
    def refused(self) -> bool:
        """True when the block left HEAD somewhere the guard would not re-attach."""
        return self.outcome is not None and self.outcome.status is ReattachStatus.REFUSED


def _stdout(res: subprocess.CompletedProcess[str]) -> str:
    return res.stdout.strip() if res.returncode == 0 and isinstance(res.stdout, str) else ""


def _current_branch(repo_dir: str, git: GitRunner) -> str | None:
    return _stdout(git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=repo_dir, check=False)) or None


def read_head_state(repo_dir: str, branch: str, run: GitRunner | None = None) -> HeadState:
    """Read HEAD, the checked-out branch and the tip of ``refs/heads/<branch>`` in ``repo_dir``. Read-only."""
    git = run or run_git
    current = _current_branch(repo_dir, git)
    sha = _stdout(git(["rev-parse", "--verify", "HEAD^{commit}"], cwd=repo_dir, check=False))
    tip = _stdout(
        git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}"], cwd=repo_dir, check=False)
    )
    return HeadState(target=branch, branch=current, sha=sha, branch_tip=tip or None)


def _refusal(state: HeadState) -> str | None:
    """Why HEAD cannot be re-attached to ``state.target`` without changing the checked-out tree; None when it can."""
    branch = state.target
    if not state.sha:
        return f"the worktree HEAD is not a readable commit; cannot re-attach '{branch}'"
    if state.branch:
        return f"the worktree is on branch '{state.branch}', not '{branch}'; refusing to switch branches"
    if not state.branch_tip:
        return (
            f"the worktree HEAD is detached at {state.sha[:12]} and the local branch '{branch}' does not exist; "
            "refusing to re-attach"
        )
    if state.detached_elsewhere:
        return (
            f"the worktree HEAD is detached at {state.sha[:12]}, not at the tip {state.branch_tip[:12]} of "
            f"'{branch}'; refusing to re-attach, because that would change the checked-out tree. Check out "
            f"'{branch}' by hand (uncommitted changes are kept by a plain 'git checkout {branch}') and resume."
        )
    return None


def reattach_branch(repo_dir: str, branch: str, run: GitRunner | None = None) -> ReattachOutcome:
    """Put HEAD back on ``branch`` when it is detached exactly at the branch tip.

    Side effects: at most one ``git symbolic-ref HEAD refs/heads/<branch>``, which rewrites only ``HEAD``; the index
    and the working tree are never touched. Any other state is refused, never repaired.
    """
    git = run or run_git
    state = read_head_state(repo_dir, branch, git)
    if state.on_branch:
        return ReattachOutcome(ReattachStatus.ON_BRANCH, f"the worktree is on '{branch}'")
    refusal = _refusal(state)
    if refusal:
        return ReattachOutcome(ReattachStatus.REFUSED, refusal)
    ref = f"refs/heads/{branch}"
    res = git(["symbolic-ref", "-m", f"holon: re-attach {branch} at its tip", "HEAD", ref], cwd=repo_dir, check=False)
    if res.returncode != 0:
        detail = (res.stderr.strip() if isinstance(res.stderr, str) else "") or f"exit code {res.returncode}"
        return ReattachOutcome(
            ReattachStatus.REFUSED, f"could not re-attach '{branch}': {redact_agent_secrets(detail)}"
        )
    return ReattachOutcome(
        ReattachStatus.REATTACHED,
        f"re-attached '{branch}' (HEAD was detached at its tip {state.sha[:12]}; uncommitted changes kept)",
    )


def _repair(
    repo_dir: str, branch: str, step_label: str, log: Callable[[str], None], git: GitRunner
) -> ReattachOutcome | None:
    """Name the step that left HEAD off ``branch`` and re-attach it; None when HEAD is on it or unreadable."""
    if _current_branch(repo_dir, git) == branch:
        return None
    state = read_head_state(repo_dir, branch, git)
    if not state.sha:
        return None
    where = f"on branch '{state.branch}'" if state.branch else f"detached at {state.sha[:12]}"
    log(f"Warning: {step_label} left the worktree HEAD {where} instead of on '{branch}'.")
    outcome = reattach_branch(repo_dir, branch, git)
    log(f"{step_label}: {outcome.message}.")
    return outcome


@contextlib.contextmanager
def head_guard(
    repo_dir: str, branch: str, step_label: str, log: Callable[[str], None], run: GitRunner | None = None
) -> Iterator[HeadGuardReport]:
    """Detect and repair a block (an agent turn) that moved HEAD off ``branch``.

    The guard is armed only when ``branch`` is checked out before the block. On exit, a HEAD the block left elsewhere
    is logged with ``step_label`` and re-attached when it is detached at the branch tip; otherwise the refusal is
    logged and recorded in the yielded report. The guard never raises and never replaces the block's own exception.
    """
    git = run or run_git
    report = HeadGuardReport()
    armed = _current_branch(repo_dir, git) == branch
    try:
        yield report
    finally:
        if armed:
            try:
                report.outcome = _repair(repo_dir, branch, step_label, log, git)
            except Exception as exc:  # the guard is diagnostics and repair; it never masks the block's outcome
                log(f"Warning: could not check the worktree HEAD after {step_label}: {exc}")


def restore_branch(
    repo_dir: str, branch: str, step_label: str, log: Callable[[str], None], run: GitRunner | None = None
) -> None:
    """Hand the worktree back on ``branch`` after ``step_label``, whatever state the step left it in.

    A HEAD detached at the tip is re-attached; any other readable state falls back to a plain, non-forcing
    ``git checkout <branch>``, which carries non-conflicting changes and refuses conflicting ones (logged, never
    raised). An unreadable HEAD or a missing local branch is left alone.
    """
    git = run or run_git
    try:
        outcome = _repair(repo_dir, branch, step_label, log, git)
        if outcome is None or outcome.status is not ReattachStatus.REFUSED:
            return
        if not read_head_state(repo_dir, branch, git).branch_tip:
            return
        res = git(["checkout", branch], cwd=repo_dir, check=False)
        if res.returncode == 0:
            log(f"{step_label}: checked out '{branch}' (uncommitted changes carried over).")
            return
        detail = res.stderr.strip() if isinstance(res.stderr, str) else ""
        log(f"Warning: could not check out '{branch}' after {step_label}: {redact_agent_secrets(detail)}")
    except Exception as exc:  # restoring is best effort and must not hide the step's own result or exception
        log(f"Warning: could not restore '{branch}' after {step_label}: {exc}")
