"""The single Stage 5 core shared by the flow's calibrate stage and ``holon calibrate``.

Stage 5 runs three steps in order, each committed on the execution's /calibrated branch: the calibration report
(``run_calibrate``), the execution reconciliation in ``executions.jsonl`` (``reconcile_records``) and the
knowledge/wisdom promotion (``promote_pending_candidates``). The callers pass the step callables in through
``Stage5Steps`` (built by ``flow.stages.calibrate.stage5_steps``), so both entry points run the same objects.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sandbox_executor.calibration import LEDGER_EXECUTIONS_REL
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.ci_reverify import approved_head_of
from sandbox_executor.flow.stage5_ledger import recorded_verdicts
from sandbox_executor.knowledge import ActionIndexDatabase
from sandbox_executor.ledger import LedgerRewriteError
from sandbox_executor.ledger.fragments import iter_fragment_files
from sandbox_executor.review_accounting import accounting_of

ACTION_INDEX_REL = "holon-knowledge/.index/action_index.db"


@dataclass(frozen=True)
class Stage5Steps:
    """The step callables of Stage 5, with the signatures of the functions they stand for."""

    calibrate: Callable[..., Any]
    reconcile: Callable[..., dict[str, Any]]
    promote: Callable[..., list[dict[str, Any]]]
    commit_reconciliation: Callable[[FlowContext, str], None]
    commit_promoted: Callable[[FlowContext, str, int], None]
    warn_ledger_failure: Callable[[FlowContext, str, str, BaseException], None]


@dataclass
class Stage5Outcome:
    """What one Stage 5 run did: the report, the reconciliation outcome and the newly promoted candidates."""

    report_dict: dict[str, Any] = field(default_factory=dict)
    report_only: bool = False
    reconciliation: dict[str, Any] = field(default_factory=dict)
    promoted_candidates: list[dict[str, Any]] = field(default_factory=list)

    @property
    def newly_promoted_count(self) -> int:
        """Number of knowledge/wisdom candidates this run newly promoted."""
        return len(self.promoted_candidates)

    def to_json_dict(self) -> dict[str, Any]:
        """The ``stage5`` object of ``holon calibrate --json``."""
        return {
            "mode": "report_only" if self.report_only else "full",
            "reconciliation": dict(self.reconciliation),
            "newly_promoted_count": self.newly_promoted_count,
        }


def _reconciliation(status: str, verdict: str | None, rows_appended: int = 0) -> dict[str, Any]:
    return {"status": status, "verdict": verdict, "rows_appended": rows_appended}


def _reconcile_execution(context: FlowContext, steps: Stage5Steps, verdict: str | None) -> dict[str, Any]:
    """Append the execution's reconciliation rows once and commit them."""
    repo_dir = context.target_worktree
    executions_file = os.path.join(repo_dir, LEDGER_EXECUTIONS_REL)
    # Dry runs append nothing, so the reconciliation events are only written on a real run.
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
        # Commits rows an earlier failed calibration left uncommitted; a no-op when there are none.
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
    """Promote qualified knowledge/wisdom candidates and commit the artefacts (outside a dry run)."""
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
    # Runs even when nothing is new, so artefacts an earlier failed calibration left behind are committed.
    if not context.dry_run:
        steps.commit_promoted(context, repo_dir, new_count)
    return promoted


def run_stage5(
    context: FlowContext, steps: Stage5Steps, verdict: str | None, report_only: bool = False
) -> Stage5Outcome:
    """Run Stage 5: calibrate, then reconcile and commit, then promote and commit.

    Every calibration exception propagates: no report is substituted and nothing is reconciled, promoted or
    committed (the flow's calibrate stage fails closed; ``holon calibrate`` fails loudly).
    ``report_only`` stops after the calibration report; a None ``verdict`` (CLI only) skips reconciliation and
    promotion.
    """
    outcome = Stage5Outcome(report_only=report_only)
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
        # An execution with no verdict is unknown to the ledger: it contributes no lessons, so nothing is promoted.
        if verdict is not None:
            outcome.promoted_candidates = _promote_candidates(context, steps)
    except LedgerRewriteError:
        raise
    except Exception as e:
        executions_file = os.path.join(context.target_worktree, LEDGER_EXECUTIONS_REL)
        steps.warn_ledger_failure(context, "Stage 5 ledger reconciliation", executions_file, e)
    return outcome


def standalone_context(execution_branch: str, target_worktree: str, no_commit: bool) -> FlowContext:
    """The minimal ``FlowContext`` ``holon calibrate`` passes to ``run_stage5``."""
    return FlowContext(
        target_worktree=target_worktree, execution_branch=execution_branch, dry_run=no_commit, token_reduce=False
    )
