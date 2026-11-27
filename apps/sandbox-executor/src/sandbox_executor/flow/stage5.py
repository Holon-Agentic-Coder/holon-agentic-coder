from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sandbox_executor.calibration import LEDGER_EXECUTIONS_REL
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.git_refs import _execution_parent_plan_branch, _resolve_branch_ref
from sandbox_executor.flow.github.ci_reverify import approved_head_of
from sandbox_executor.flow.stage5_ledger import recorded_verdicts
from sandbox_executor.flow.stages.touch_set_guard import (
    TOUCH_SET_VIOLATION,
    _resolve_effective_touch_set,
    touch_set_violation,
)
from sandbox_executor.git import changed_paths, run_git
from sandbox_executor.knowledge import ActionIndexDatabase
from sandbox_executor.ledger import LedgerRewriteError
from sandbox_executor.ledger.fragments import iter_fragment_files
from sandbox_executor.review_accounting import accounting_of

ACTION_INDEX_REL = "holon-knowledge/.index/action_index.db"


@dataclass(frozen=True)
class Stage5Steps:
    calibrate: Callable[..., Any]
    reconcile: Callable[..., dict[str, Any]]
    promote: Callable[..., list[dict[str, Any]]]
    commit_reconciliation: Callable[[FlowContext, str], None]
    commit_promoted: Callable[[FlowContext, str, int], None]
    warn_ledger_failure: Callable[[FlowContext, str, str, BaseException], None]


@dataclass(frozen=True)
class _Stage5Readiness:
    ready: bool
    out_of_scope_files: tuple[str, ...] = ()
    reason: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "out_of_scope_files": list(self.out_of_scope_files),
            "reason": self.reason,
        }

    def format_lines(self) -> list[str]:
        if self.ready:
            return ["Stage 5 readiness check passed: all PR diff files are within the plan touch set."]
        if self.detail and not self.out_of_scope_files:
            return [f"Stage 5 readiness check failed ({self.reason or TOUCH_SET_VIOLATION}): {self.detail}"]
        header = (
            f"Stage 5 readiness check failed ({self.reason or TOUCH_SET_VIOLATION}): "
            f"{len(self.out_of_scope_files)} file(s) outside the plan touch set:"
        )
        return [header, *(f"  - {path}" for path in self.out_of_scope_files)]


class _Stage5TouchSetError(RuntimeError):
    def __init__(self, readiness: _Stage5Readiness) -> None:
        self.readiness = readiness
        self.out_of_scope_files = readiness.out_of_scope_files
        self.failure_reason = readiness.reason or TOUCH_SET_VIOLATION
        super().__init__("\n".join(readiness.format_lines()))


@dataclass
class Stage5Outcome:
    report_dict: dict[str, Any] = field(default_factory=dict)
    report_only: bool = False
    reconciliation: dict[str, Any] = field(default_factory=dict)
    promoted_candidates: list[dict[str, Any]] = field(default_factory=list)
    readiness: dict[str, Any] = field(default_factory=lambda: _Stage5Readiness(ready=True).to_dict())

    @property
    def newly_promoted_count(self) -> int:
        return len(self.promoted_candidates)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "mode": "report_only" if self.report_only else "full",
            "reconciliation": dict(self.reconciliation),
            "newly_promoted_count": self.newly_promoted_count,
        }


def _resolve_head_ref(repo_dir: str, execution_branch: str | None) -> str | None:
    if execution_branch:
        resolved = _resolve_branch_ref(repo_dir, execution_branch, prefer_remote=False)
        if resolved:
            return resolved
    head = run_git(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], cwd=repo_dir, check=False)
    return "HEAD" if head.returncode == 0 else None


def _stage5_diff_files(context: FlowContext) -> tuple[list[str], str | None, str | None]:
    repo_dir = context.target_worktree
    if not repo_dir or not os.path.exists(os.path.join(repo_dir, ".git")):
        return [], None, None
    exec_ref = _resolve_head_ref(repo_dir, context.execution_branch)
    plan_branch = (
        str(context.plan_branch)
        if context.plan_branch
        else _execution_parent_plan_branch(str(context.execution_branch or ""))
    )
    base_ref = _resolve_branch_ref(repo_dir, plan_branch, prefer_remote=False) if plan_branch else None
    if not base_ref:
        target_branch = str((context.intent_data or {}).get("target_branch") or "main")
        base_ref = _resolve_branch_ref(repo_dir, target_branch, prefer_remote=False)
    if not base_ref or not exec_ref:
        missing = [name for name, ref in (("base_ref", base_ref), ("exec_ref", exec_ref)) if not ref]
        return [], exec_ref, f"could not resolve {' and '.join(missing)} to inspect the PR diff"
    return changed_paths(repo_dir, base_ref, exec_ref), exec_ref, None


def _check_stage5_readiness(
    context: FlowContext,
    pr_diff_files: Sequence[str] | None = None,
    *,
    head_ref: str | None = None,
) -> _Stage5Readiness:
    diff_error: str | None = None
    if pr_diff_files is None:
        diff_files, resolved_head, diff_error = _stage5_diff_files(context)
    else:
        diff_files, resolved_head = list(pr_diff_files), None
    effective_head = head_ref or resolved_head
    if diff_error:
        if _resolve_effective_touch_set(context, head_ref=effective_head) is not None:
            return _Stage5Readiness(ready=False, reason=TOUCH_SET_VIOLATION, detail=diff_error)
        return _Stage5Readiness(ready=True)
    out_of_scope = touch_set_violation(context, diff_files, head_ref=effective_head)
    if out_of_scope:
        return _Stage5Readiness(ready=False, out_of_scope_files=tuple(out_of_scope), reason=TOUCH_SET_VIOLATION)
    return _Stage5Readiness(ready=True)


def _reconciliation(status: str, verdict: str | None, rows_appended: int = 0) -> dict[str, Any]:
    return {"status": status, "verdict": verdict, "rows_appended": rows_appended}


def _reconcile_execution(context: FlowContext, steps: Stage5Steps, verdict: str | None) -> dict[str, Any]:
    repo_dir = context.target_worktree
    executions_file = os.path.join(repo_dir, LEDGER_EXECUTIONS_REL)
    if context.dry_run:
        return _reconciliation("skipped_no_commit", verdict)
    if not os.path.exists(executions_file) and not iter_fragment_files(executions_file):
        return _reconciliation("skipped_no_ledger", verdict)
    if verdict is None:
        context.log("Stage 5 reconciliation and promotion skipped: no execution verdict was found in the ledger.")
        return _reconciliation("skipped_no_verdict", verdict)
    recorded = recorded_verdicts(executions_file, str(context.execution_branch))
    if recorded:
        context.log(f"Stage 5 reconciliation already recorded for '{context.execution_branch}': nothing appended.")
        if verdict not in recorded:
            context.log(
                f"Stage 5 verdict '{verdict}' differs from the recorded reconciliation verdict '{recorded[-1]}' "
                f"for '{context.execution_branch}': the recorded verdict is kept."
            )
        # why: commits rows an earlier failed calibration left uncommitted; a no-op when there are none
        steps.commit_reconciliation(context, repo_dir)
        return _reconciliation("already_recorded", verdict)
    result = steps.reconcile(
        ledger_path=executions_file,
        max_age_days=14,
        target_action_id=context.execution_branch,
        verdict=verdict,
        reason=f"Stage 5 calibration reconciled execution outcome to {verdict}",
        db_path=os.path.join(repo_dir, ACTION_INDEX_REL),
    )
    rows = sum(int(result.get(key, 0)) for key in ("reconciled", "expired")) if isinstance(result, dict) else 0
    steps.commit_reconciliation(context, repo_dir)
    return _reconciliation("recorded", verdict, rows)


def _promote_candidates(context: FlowContext, steps: Stage5Steps) -> list[dict[str, Any]]:
    repo_dir = context.target_worktree
    db_path = os.path.join(repo_dir, ACTION_INDEX_REL)
    if not os.path.exists(db_path):
        return []
    promoted = steps.promote(
        index_db=ActionIndexDatabase(db_path=db_path),
        kb_dir=os.path.join(repo_dir, "holon-knowledge/kb"),
        wb_dir=os.path.join(repo_dir, "holon-knowledge/wisdom"),
        min_trials=2,
        dry_run=context.dry_run,
    )
    new_count = len(promoted)
    context.log(
        f"Stage 5 promoted {new_count} new knowledge/wisdom candidate(s) this run."
        if new_count
        else "Stage 5 promoted no new knowledge/wisdom candidates this run."
    )
    # why: runs even when nothing is new, so artefacts an earlier failed calibration left behind are committed
    if not context.dry_run:
        steps.commit_promoted(context, repo_dir, new_count)
    return promoted


def run_stage5(
    context: FlowContext, steps: Stage5Steps, verdict: str | None, report_only: bool = False
) -> Stage5Outcome:
    readiness = _check_stage5_readiness(context)
    if not readiness.ready:
        for line in readiness.format_lines():
            context.log(line)
        raise _Stage5TouchSetError(readiness)
    outcome = Stage5Outcome(report_only=report_only, readiness=readiness.to_dict())
    # why: calibration exceptions propagate uncaught so the flow's calibrate stage fails closed and no report is faked
    report = steps.calibrate(
        execution_branch=context.execution_branch,
        target_worktree=context.target_worktree,
        json_output=False,
        skip_commit=context.dry_run,
        log_fn=context.log,
        approved_head=approved_head_of(context),
        review_accounting=accounting_of(context),
    )
    context.calibrated_branch = report.calibrated_branch
    outcome.report_dict = report.to_dict()
    if report_only:
        outcome.reconciliation = _reconciliation("skipped_report_only", verdict)
        return outcome
    outcome.reconciliation = _reconciliation("failed", verdict)
    try:
        outcome.reconciliation = _reconcile_execution(context, steps, verdict)
        # why: an execution with no verdict is unknown to the ledger, so it contributes no lessons to promote
        if verdict is not None:
            outcome.promoted_candidates = _promote_candidates(context, steps)
    except LedgerRewriteError:
        raise
    except Exception as e:
        executions_file = os.path.join(context.target_worktree, LEDGER_EXECUTIONS_REL)
        steps.warn_ledger_failure(context, "Stage 5 ledger reconciliation", executions_file, e)
    return outcome


def standalone_context(execution_branch: str, target_worktree: str, no_commit: bool) -> FlowContext:
    return FlowContext(
        target_worktree=target_worktree, execution_branch=execution_branch, dry_run=no_commit, token_reduce=False
    )
