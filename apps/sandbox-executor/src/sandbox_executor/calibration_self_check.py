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

# why: older Stage 5 runs committed this fallback stub (for example PR #249); the guard stops re-accepting it.
FALLBACK_REPORT_MARKER = "Fallback calibration generated."


class CalibrateReview(TypedDict, total=False):
    approved_head: str | None
    review_accounting: dict[str, Any] | None


class CalibrationSelfCheckError(RuntimeError):
    def __init__(self, conditions: list[str]) -> None:
        self.conditions = tuple(conditions)
        self.condition = self.conditions[0] if self.conditions else ""
        super().__init__(f"calibration report self-check failed: {', '.join(self.conditions)}")


@dataclass(frozen=True)
class SelfCheckInputs:
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
    a, b = str(first or "").lower(), str(second or "").lower()
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def _has_unresolved_execution_failure(inputs: SelfCheckInputs) -> bool:
    # why: the same rows evaluate_dual_ev_outcomes scores, so every reported drag has its failure row here.
    return has_unresolved_failure(*dual_ev_rows(inputs.records, inputs.execution_id, inputs.plan_branch))


# why: a report that contradicts its own inputs would silently skew every later plan's EV, so it fails closed.
def self_check_report(inputs: SelfCheckInputs) -> list[str]:
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
    on_target = _git_lines(repo_dir, ["diff", "--name-only", f"{plan_rev}...{merge_base_sha}", "--"])
    # why: the PR's own side includes edits inside its merge commits, matching the three-dot diff the counts come from.
    own = _git_lines(repo_dir, ["diff", "--name-only", f"{merge_base_sha}...{head_sha}", "--"])
    if on_target is None or own is None:
        return frozenset()
    return frozenset(on_target) - frozenset(own)


def reaches_through_metadata(repo_dir: str, approved: str, evaluated: str) -> bool:
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
    violations = self_check_report(report_self_check_inputs(report, repo_dir, approved_head))
    for condition in violations:
        (log or logger.warning)(f"Calibration report self-check violated: {condition}")
    if violations:
        raise CalibrationSelfCheckError(violations)
