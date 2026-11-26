"""The commit a calibration report evaluates: the execution head with Stage 5's own commits discounted.

Stage 5 commits the report, the ACT-CAL and reconciliation rows and the promoted knowledge on the /calibrated branch,
which then fast-forwards the execution branch. Re-running ``holon calibrate`` on ``/calibrated`` or ``_`` must not
evaluate one of those commits: the report covers the head the review approved. A Stage 5 calibration commit is a
non-merge commit whose subject starts with ``chore(calibration): `` and that touches only calibration paths (a
``plans/`` calibration report, the executions ledger, ``holon-knowledge/kb/`` or ``holon-knowledge/wisdom/``). Ledger
rows the review loop commits before Stage 5 (``chore(review): ...``) are part of the approved head and are kept; they
are bookkeeping all the same (``is_ledger_only_commit``), so they never make a report stale.
"""

from __future__ import annotations

from sandbox_executor.git import run_git
from sandbox_executor.ledger.fragments import pending_rel_dir
from sandbox_executor.plan_paths import is_calibration_report_path

LEDGER_EXECUTIONS_REL = "holon-knowledge/ledger/executions.jsonl"
CALIBRATION_SUBJECT_PREFIX = "chore(calibration): "
CALIBRATION_DIR_PREFIXES = ("holon-knowledge/kb/", "holon-knowledge/wisdom/")
LEDGER_DIR_PREFIX = "holon-knowledge/ledger/"
# Bound on the first-parent walk: Stage 5 adds at most a handful of commits per run.
MAX_EVALUATED_WALK = 50


def _git_out(repo_dir: str, args: list[str]) -> str | None:
    res = run_git(args, cwd=repo_dir, check=False)
    return res.stdout if res.returncode == 0 and isinstance(res.stdout, str) else None


def is_calibration_path(path: str) -> bool:
    """True for a path Stage 5 commits: a plans/ calibration report, the executions ledger, kb or wisdom files."""
    return (
        path == LEDGER_EXECUTIONS_REL
        or path.startswith((*CALIBRATION_DIR_PREFIXES, f"{pending_rel_dir('executions.jsonl')}/"))
        or is_calibration_report_path(path)
    )


def _non_merge_commit_paths(repo_dir: str, sha: str) -> list[str] | None:
    """The paths a non-merge commit changes (a root commit: every path); None for a merge or a git failure."""
    parents = _git_out(repo_dir, ["rev-list", "--parents", "-n", "1", sha, "--"])
    paths = _git_out(repo_dir, ["diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha, "--"])
    if parents is None or paths is None or len(parents.split()) > 2:
        return None
    return paths.splitlines()


def is_calibration_commit(repo_dir: str, sha: str) -> bool:
    """True for a non-merge commit with a ``chore(calibration): `` subject touching only calibration paths."""
    subject = _git_out(repo_dir, ["log", "-1", "--format=%s", sha, "--"])
    paths = _non_merge_commit_paths(repo_dir, sha)
    if subject is None or paths is None:
        return False
    return subject.startswith(CALIBRATION_SUBJECT_PREFIX) and all(map(is_calibration_path, paths))


def is_ledger_only_commit(repo_dir: str, sha: str) -> bool:
    """True for a non-merge commit that changes at least one path, every one under ``holon-knowledge/ledger/``.

    Path-based: the review loop's ledger-row commits and the engine's ledger commits both qualify, whatever their
    subject. An empty commit, a merge and a git failure are False.
    """
    paths = _non_merge_commit_paths(repo_dir, sha)
    return bool(paths) and all(path.startswith(LEDGER_DIR_PREFIX) for path in paths)


def evaluated_code_commit(head_ref: str, repo_dir: str) -> str | None:
    """The first commit on the first-parent walk from ``head_ref`` that is not a Stage 5 calibration commit.

    A merge of the target branch counts as a code commit (the review approved that head). When every walked commit
    is a calibration commit the head itself is returned; None when git cannot list the history.
    """
    listed = _git_out(repo_dir, ["rev-list", "--first-parent", f"--max-count={MAX_EVALUATED_WALK}", head_ref, "--"])
    shas = listed.split() if listed else []
    if not shas:
        return None
    return next((sha for sha in shas if not is_calibration_commit(repo_dir, sha)), shas[0])
