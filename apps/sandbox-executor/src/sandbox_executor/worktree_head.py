from __future__ import annotations

import contextlib
import dataclasses
import enum
import subprocess
from collections.abc import Callable, Iterator

from sandbox_executor.git import run_git
from sandbox_executor.redaction import redact_agent_secrets

GitRunner = Callable[..., subprocess.CompletedProcess[str]]

_SHORT_SHA_LENGTH = 12


class ReattachStatus(enum.Enum):
    ON_BRANCH = "on_branch"
    REATTACHED = "reattached"
    REFUSED = "refused"


@dataclasses.dataclass(frozen=True)
class ReattachOutcome:
    status: ReattachStatus
    message: str


@dataclasses.dataclass(frozen=True)
class HeadState:
    target: str
    branch: str | None
    sha: str
    branch_tip: str | None

    @property
    def on_branch(self) -> bool:
        return self.branch == self.target

    @property
    def detached_at_tip(self) -> bool:
        return self.branch is None and bool(self.sha) and self.sha == self.branch_tip

    @property
    def detached_elsewhere(self) -> bool:
        return self.branch is None and bool(self.sha) and self.sha != self.branch_tip


@dataclasses.dataclass
class HeadGuardReport:
    outcome: ReattachOutcome | None = None

    @property
    def refused(self) -> bool:
        return self.outcome is not None and self.outcome.status is ReattachStatus.REFUSED


def _stdout(res: subprocess.CompletedProcess[str]) -> str:
    return res.stdout.strip() if res.returncode == 0 and isinstance(res.stdout, str) else ""


def _current_branch(repo_dir: str, git: GitRunner) -> str | None:
    return _stdout(git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=repo_dir, check=False)) or None


def read_head_state(repo_dir: str, branch: str, run: GitRunner | None = None) -> HeadState:
    git = run or run_git
    current = _current_branch(repo_dir, git)
    sha = _stdout(git(["rev-parse", "--verify", "HEAD^{commit}"], cwd=repo_dir, check=False))
    tip = _stdout(
        git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}"], cwd=repo_dir, check=False)
    )
    return HeadState(target=branch, branch=current, sha=sha, branch_tip=tip or None)


def _refusal(state: HeadState) -> str | None:
    branch = state.target
    if not state.sha:
        return f"the worktree HEAD is not a readable commit; cannot re-attach '{branch}'"
    if state.branch:
        return f"the worktree is on branch '{state.branch}', not '{branch}'; refusing to switch branches"
    if not state.branch_tip:
        return (
            f"the worktree HEAD is detached at {state.sha[:_SHORT_SHA_LENGTH]} and the local branch '{branch}' "
            "does not exist; refusing to re-attach"
        )
    if state.detached_elsewhere:
        return (
            f"the worktree HEAD is detached at {state.sha[:_SHORT_SHA_LENGTH]}, not at the tip "
            f"{state.branch_tip[:_SHORT_SHA_LENGTH]} of '{branch}'; refusing to re-attach, because that would "
            f"change the checked-out tree. Check out '{branch}' by hand (uncommitted changes are kept by a plain "
            f"'git checkout {branch}') and resume."
        )
    return None


def reattach_branch(repo_dir: str, branch: str, run: GitRunner | None = None) -> ReattachOutcome:
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
        f"re-attached '{branch}' (HEAD was detached at its tip {state.sha[:_SHORT_SHA_LENGTH]}; "
        "uncommitted changes kept)",
    )


def _repair(
    repo_dir: str, branch: str, step_label: str, log: Callable[[str], None], git: GitRunner
) -> ReattachOutcome | None:
    if _current_branch(repo_dir, git) == branch:
        return None
    state = read_head_state(repo_dir, branch, git)
    if not state.sha:
        return None
    where = f"on branch '{state.branch}'" if state.branch else f"detached at {state.sha[:_SHORT_SHA_LENGTH]}"
    log(f"Warning: {step_label} left the worktree HEAD {where} instead of on '{branch}'.")
    outcome = reattach_branch(repo_dir, branch, git)
    log(f"{step_label}: {outcome.message}.")
    return outcome


@contextlib.contextmanager
def head_guard(
    repo_dir: str, branch: str, step_label: str, log: Callable[[str], None], run: GitRunner | None = None
) -> Iterator[HeadGuardReport]:
    git = run or run_git
    report = HeadGuardReport()
    armed = _current_branch(repo_dir, git) == branch
    try:
        yield report
    finally:
        if armed:
            try:
                report.outcome = _repair(repo_dir, branch, step_label, log, git)
            except Exception as exc:
                # why: head_guard is diagnostic/repair only and must never mask the wrapped block's outcome
                log(f"Warning: could not check the worktree HEAD after {step_label}: {exc}")


def restore_branch(
    repo_dir: str, branch: str, step_label: str, log: Callable[[str], None], run: GitRunner | None = None
) -> None:
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
    except Exception as exc:
        # why: branch restoration is best-effort and must not hide the step's own result or exception
        log(f"Warning: could not restore '{branch}' after {step_label}: {exc}")
