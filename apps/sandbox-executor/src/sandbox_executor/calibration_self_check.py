"""Fail-closed self-check of a calibration report before Stage 5 commits it.

A report that contradicts its own inputs would silently skew every later plan's EV, so ``run_calibrate`` checks it
before any branch, file or commit is touched and raises ``CalibrationSelfCheckError`` naming each violated condition:

- ``drag_without_failure``: bug drag above 0 with no unresolved, non-infrastructure execution failure row.
- ``counted_target_only_file``: a counted path was changed on the target branch since the plan branch, and the PR's
  side (its own commits or the edits of its merges) leaves it identical to the merge base.
- ``evaluated_ledger_only``: the evaluated SHA is one of Stage 5's own calibration (report or ledger-only) commits.
- ``evaluated_not_approved``: the review approved a head the evaluated SHA neither is nor reaches through
  metadata-only commits (ledger rows recorded after the approval) or merges (the conflict gate's merge of the target).
- ``fallback_stub``: the report holds the stub text older Stage 5 runs wrote for a failed calibration.

``self_check_report`` is pure; ``report_self_check_inputs`` gathers its git facts.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypedDict

from sandbox_executor.calibration_diff import DIFF_BASE_TARGET_MERGE_BASE
from sandbox_executor.calibration_ev import dual_ev_rows
from sandbox_executor.calibration_evaluated_sha import is_calibration_commit
from sandbox_executor.calibration_outcome import has_unresolved_failure
from sandbox_executor.git import is_metadata_path, run_git
from sandbox_executor.git_revisions import qualify_branch_revision

if TYPE_CHECKING:
    from sandbox_executor.calibration import CalibrationReport

logger = logging.getLogger(__name__)

# The body older Stage 5 runs wrote as a fallback report when ``run_calibrate`` raised (for example on PR #249). No code
# path writes it any more; the condition guards against re-accepting such a stub already committed on a branch.
FALLBACK_REPORT_MARKER = "Fallback calibration generated."


class CalibrateReview(TypedDict, total=False):
    """What Stage 4 established about the execution head, passed to ``run_calibrate`` by keyword.

    ``approved_head`` is the commit the review approval covered (``approved_head_of``); None (the CLI) skips the
    self-check's approved-head condition. ``review_accounting`` is the flow's review accounting
    (:mod:`sandbox_executor.review_accounting`), rendered in the "Review Churn" section; None (the CLI, a legacy flow)
    renders "not recorded".
    """

    approved_head: str | None
    review_accounting: dict[str, Any] | None


class CalibrationSelfCheckError(RuntimeError):
    """The calibration report failed its self-check; ``conditions`` names each violated condition."""

    def __init__(self, conditions: list[str]) -> None:
        self.conditions = tuple(conditions)
        self.condition = self.conditions[0] if self.conditions else ""
        super().__init__(f"calibration report self-check failed: {', '.join(self.conditions)}")


@dataclass(frozen=True)
class SelfCheckInputs:
    """The report facts and git facts the self-check compares."""

    bug_ev_drag: float
    records: list[dict[str, Any]]
    execution_id: str
    plan_branch: str
    markdown: str
    counted_paths: tuple[str, ...] = ()
    target_only_paths: frozenset[str] = field(default_factory=frozenset)
    evaluated_is_calibration_commit: bool = False
    approved_head: str | None = None
    evaluated_sha: str | None = None
    evaluated_reaches_approved: bool = False


def same_commit(first: str | None, second: str | None) -> bool:
    """Whether two SHAs (either may be abbreviated) name the same commit (as ``ci_reverify._same_commit``)."""
    a, b = str(first or "").lower(), str(second or "").lower()
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def _has_unresolved_execution_failure(inputs: SelfCheckInputs) -> bool:
    # The same rows evaluate_dual_ev_outcomes scores, so a drag it reports always has its failure row here
    return has_unresolved_failure(*dual_ev_rows(inputs.records, inputs.execution_id, inputs.plan_branch))


def self_check_report(inputs: SelfCheckInputs) -> list[str]:
    """The names of the violated conditions, in the module docstring's order; empty for a consistent report."""
    violations: list[str] = []
    if inputs.bug_ev_drag > 0 and not _has_unresolved_execution_failure(inputs):
        violations.append("drag_without_failure")
    if inputs.target_only_paths.intersection(inputs.counted_paths):
        violations.append("counted_target_only_file")
    if inputs.evaluated_is_calibration_commit:
        violations.append("evaluated_ledger_only")
    approved, evaluated = inputs.approved_head, inputs.evaluated_sha
    if approved and not same_commit(approved, evaluated) and not inputs.evaluated_reaches_approved:
        violations.append("evaluated_not_approved")
    if FALLBACK_REPORT_MARKER in inputs.markdown:
        violations.append("fallback_stub")
    return violations


def _git_lines(repo_dir: str, args: list[str]) -> list[str] | None:
    res = run_git(args, cwd=repo_dir, check=False, timeout=30)
    if res.returncode != 0 or not isinstance(res.stdout, str):
        return None
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def target_only_paths(repo_dir: str, plan_rev: str, merge_base_sha: str, head_sha: str) -> frozenset[str]:
    """Paths the target branch changed since the plan branch (``plan...merge-base``) that the PR's side leaves
    identical to the merge base (``merge-base...head``); empty when git cannot tell.

    The PR's own paths include edits made inside merge commits, such as a conflict merge adapting a file the target
    added, so they match the three-dot diff the counted paths come from.
    """
    on_target = _git_lines(repo_dir, ["diff", "--name-only", f"{plan_rev}...{merge_base_sha}", "--"])
    own = _git_lines(repo_dir, ["diff", "--name-only", f"{merge_base_sha}...{head_sha}", "--"])
    if on_target is None or own is None:
        return frozenset()
    return frozenset(on_target) - frozenset(own)


def reaches_through_metadata(repo_dir: str, approved: str, evaluated: str) -> bool:
    """True when ``evaluated`` descends from ``approved`` through merges or metadata-only commits only."""
    if run_git(["merge-base", "--is-ancestor", approved, evaluated], cwd=repo_dir, check=False).returncode != 0:
        return False
    commits = _git_lines(repo_dir, ["rev-list", "--first-parent", "--parents", f"{approved}..{evaluated}", "--"])
    if commits is None:
        return False
    for line in commits:
        sha, *parents = line.split()
        paths = _git_lines(repo_dir, ["diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha, "--"])
        if len(parents) < 2 and (paths is None or not all(map(is_metadata_path, paths))):
            return False
    return True


def report_self_check_inputs(report: CalibrationReport, repo_dir: str, approved_head: str | None) -> SelfCheckInputs:
    """Gather the self-check inputs of ``report`` from the report and read-only git queries in ``repo_dir``."""
    evaluated = report.evaluated_commit_sha
    plan_rev = qualify_branch_revision(repo_dir, report.plan_branch) if report.plan_branch else None
    target_only: frozenset[str] = frozenset()
    if report.diff_base == DIFF_BASE_TARGET_MERGE_BASE and plan_rev and report.merge_base_sha and evaluated:
        target_only = target_only_paths(repo_dir, plan_rev, report.merge_base_sha, evaluated)
    return SelfCheckInputs(
        bug_ev_drag=report.actual.bug_ev_drag,
        records=report.summary_records,
        execution_id=report.execution_id,
        plan_branch=report.plan_branch,
        markdown=report.markdown_content,
        counted_paths=tuple(report.changed_paths or ()),
        target_only_paths=target_only,
        evaluated_is_calibration_commit=bool(evaluated) and is_calibration_commit(repo_dir, str(evaluated)),
        approved_head=approved_head,
        evaluated_sha=evaluated,
        evaluated_reaches_approved=bool(approved_head and evaluated)
        and reaches_through_metadata(repo_dir, str(approved_head), str(evaluated)),
    )


def enforce_report_self_check(
    report: CalibrationReport, repo_dir: str, approved_head: str | None, log: Callable[[str], None] | None = None
) -> None:
    """Run the self-check on ``report``; log each violated condition and raise ``CalibrationSelfCheckError``."""
    violations = self_check_report(report_self_check_inputs(report, repo_dir, approved_head))
    for condition in violations:
        (log or logger.warning)(f"Calibration report self-check violated: {condition}")
    if violations:
        raise CalibrationSelfCheckError(violations)
