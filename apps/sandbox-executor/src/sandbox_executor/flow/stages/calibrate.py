"""Stage 5 (Post-Execution Calibration): run the calibration engine on the execution branch and commit the
calibrated artefacts.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.calibration import (
    LEDGER_EXECUTIONS_REL,
    CalibrationFormatError,
    calibration_commit_env,
    format_calibration_report,
    record_calibration_action,
    run_calibrate,
)
from sandbox_executor.flow.calibrate_promotion import _commit_stage5_promoted_artifacts
from sandbox_executor.flow.calibrate_sync import (
    CalibrationSyncError,
    discard_empty_calibrated_branch,
    prepare_calibrated_branch,
    publish_calibration,
    restore_starting_branch,
    sync_execution_ref,
)
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.github.ci_final_head import check_final_head_ci, format_final_head_ci, not_pushed_record
from sandbox_executor.flow.github.ci_reverify import RED, blocked_error, reverify_and_report
from sandbox_executor.flow.ledger_warning import _warn_ledger_failure
from sandbox_executor.flow.review.conflict_gate import _stage5_conflict_gate
from sandbox_executor.flow.review.records import _bounded_error_context
from sandbox_executor.flow.review_restart import stage5_merged_head_gate
from sandbox_executor.flow.stage5 import Stage5Steps, run_stage5
from sandbox_executor.flow.stage_conflicts import check_and_resolve_stage_conflicts
from sandbox_executor.flow.stages.review import _checkout_execution_branch
from sandbox_executor.git import run_git
from sandbox_executor.knowledge import promote_pending_candidates, reconcile_records
from sandbox_executor.ledger import LedgerRewriteError
from sandbox_executor.plan_paths import calibration_report_for_branch, legacy_calibration_report_rel, split_plan_branch
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.ref_names import calibration_branch

logger = logging.getLogger(__name__)


def _commit_stage5_reconciliation(context: FlowContext, repo_dir: str) -> None:
    """Commit the Stage 5 reconciliation rows onto the /calibrated branch.

    ``reconcile_records`` appends after ``run_calibrate`` has committed the calibration report,
    so the rows are committed in a follow-up commit on the same /calibrated branch, before it is
    pushed and fast-forwarded into the execution branch. Call it only after ``run_calibrate``
    returned: on the fallback path (``run_calibrate`` raised) the caller skips it, even when HEAD
    already sits on the /calibrated branch, so the rows stay uncommitted in the working tree with
    the fallback report. As a further guard, nothing is committed when HEAD is not the /calibrated
    branch. Never raises.
    """
    branch = context.calibrated_branch
    if not branch:
        return
    try:
        head = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir, check=False)
        if head.returncode != 0 or head.stdout.strip() != branch:
            context.log(
                f"Stage 5 reconciliation rows left uncommitted in {LEDGER_EXECUTIONS_REL}: "
                f"HEAD is not the calibrated branch '{branch}'."
            )
            return
        run_git(["add", LEDGER_EXECUTIONS_REL], cwd=repo_dir, check=False)
        commit_res = run_git(
            ["commit", "-m", "chore(calibration): record stage 5 reconciliation", "--", LEDGER_EXECUTIONS_REL],
            cwd=repo_dir,
            env=calibration_commit_env(),
            check=False,
        )
        if commit_res.returncode != 0:
            combined_output = (commit_res.stdout + " " + commit_res.stderr).lower()
            if "nothing to commit" not in combined_output:
                detail = redact_agent_secrets(commit_res.stderr.strip())
                msg = f"Warning: committing Stage 5 reconciliation rows failed: {detail}"
                logger.warning(msg)
                context.log(msg)
    except Exception as e:
        msg = f"Warning: committing Stage 5 reconciliation rows failed: {redact_agent_secrets(str(e))}"
        logger.warning(msg)
        context.log(msg)


def stage5_steps(
    on_calibrate_error: Callable[[FlowContext, Exception], dict[str, Any]] | None = None,
) -> Stage5Steps:
    """The Stage 5 step callables, read from this module's globals at call time so test patches here take effect."""
    return Stage5Steps(
        calibrate=run_calibrate,
        reconcile=reconcile_records,
        promote=promote_pending_candidates,
        commit_reconciliation=_commit_stage5_reconciliation,
        commit_promoted=_commit_stage5_promoted_artifacts,
        warn_ledger_failure=_warn_ledger_failure,
        on_calibrate_error=on_calibrate_error,
    )


def run_calibrate_stage(context: FlowContext) -> StageResult:
    """Execute Stage 5 unless the conflict gate or the merged-head CI gate stops it, then restore the starting branch.

    The merged-head CI gate runs after the conflict gate's merge and before any calibration commit is pushed.
    """
    try:
        refused = _stage5_conflict_gate(context)
        if refused is not None:
            return refused
        stopped, merged_head_ci = stage5_merged_head_gate(context)
        return stopped or _calibrate_execution_branch(context, merged_head_ci)
    finally:
        restore_starting_branch(context)


def _fallback_report(context: FlowContext, exc: Exception, calibrated_created: bool) -> dict[str, Any]:
    """Write (and on a real run record) the fallback calibration report after ``run_calibrate`` raised."""
    execution_branch = str(context.execution_branch)
    repo_dir = context.target_worktree
    discard_empty_calibrated_branch(context, calibrated_created)
    context.log(f"Calibration run encountered exception: {redact_agent_secrets(str(exc))}. Generating fallback report.")
    plan_id = split_plan_branch(execution_branch)[1] or "P-unknown"
    # Beside the plan file (plans/{intent}/...); a branch naming no plan falls back to the flat legacy location.
    report_rel = calibration_report_for_branch(execution_branch) or legacy_calibration_report_rel(plan_id)
    report_path = os.path.join(repo_dir, report_rel)
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    fallback_report_written = not os.path.exists(report_path)
    if fallback_report_written:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"# Calibration Report for {plan_id}\n\nFallback calibration generated.\n")
    try:
        format_calibration_report(report_rel, repo_dir)
    except Exception as fmt_exc:
        fmt_detail = redact_agent_secrets(str(fmt_exc))
        context.log(f"Warning: Prettier formatting failed for fallback {report_rel}: {fmt_detail}")
    report_dict = {
        "plan_id": plan_id,
        "calibrated_branch": calibration_branch(execution_branch),
        "markdown_content": f"# Calibration Report for {plan_id}",
    }
    context.calibrated_branch = report_dict["calibrated_branch"]
    if not context.dry_run:
        record_calibration_action(
            {**report_dict, "execution_branch": execution_branch, "plan_branch": context.plan_branch},
            repo_dir,
            report_rel,
            outcome="failure",
            error_context=_bounded_error_context(f"Calibration run failed: {exc}"),
            fallback_report_written=fallback_report_written,
            log_fn=context.log,
        )
    return report_dict


def _prepare_execution_branch(context: FlowContext, start_time: str) -> tuple[StageResult | None, str | None, bool]:
    """Check out and sync the execution branch and prepare /calibrated: (failure, start sha, calibrated created)."""
    checkout_err = _checkout_execution_branch(context)
    if checkout_err:
        failure = (
            f"Calibrate stage failed: could not check out execution branch '{context.execution_branch}' "
            f"before calibration: {checkout_err}"
        )
        return _stage_failure(context, FlowStage.CALIBRATE, start_time, failure), None, False
    try:
        start_sha = sync_execution_ref(context)
        calibrated_created = prepare_calibrated_branch(context, start_sha)
    except CalibrationSyncError as e:
        failure = f"Calibrate stage failed: {e}"
        return _stage_failure(context, FlowStage.CALIBRATE, start_time, failure, calibrated_head=None), None, False
    target_branch = context.intent_data.get("target_branch", "main")
    check_and_resolve_stage_conflicts(
        context.target_worktree, target_branch=target_branch, stage_name="calibrate", branch=context.execution_branch
    )
    return None, start_sha, calibrated_created


def _calibrate_execution_branch(context: FlowContext, merged_head_ci: dict[str, Any] | None = None) -> StageResult:
    """Execute Stage 5: Post-Execution Calibration.

    Runs the shared Stage 5 core (``flow.stage5.run_stage5``) on the execution branch: the calibration report, the
    execution reconciliation and the knowledge/wisdom promotion, each committed on the /calibrated branch. Then
    publishes the /calibrated branch and checks the final head's CI. ``merged_head_ci`` is the record of the CI Stage 5
    relied on before calibrating (see :func:`sandbox_executor.flow.review_restart.stage5_merged_head_gate`).
    Operates unattended: applies conservative calibrated updates automatically.
    """
    start_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log("Starting Stage 5: Post-Execution Calibration")

    if not context.execution_branch:
        err = "Cannot run Calibrate stage without an active execution_branch in FlowContext."
        context.log(err)
        return StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.FAILED, start_time=start_time, error=err)

    repo_dir = context.target_worktree
    calibrated_created = False
    start_sha: str | None = None
    if not context.dry_run:
        failure, start_sha, calibrated_created = _prepare_execution_branch(context, start_time)
        if failure is not None:
            return failure

    exec_status = context.stage_results.get("execute")
    verdict = "success" if exec_status and exec_status.status == StageStatus.SUCCESS else "failure"

    def on_calibrate_error(ctx: FlowContext, exc: Exception) -> dict[str, Any]:
        return _fallback_report(ctx, exc, calibrated_created)

    try:
        outcome = run_stage5(context, stage5_steps(on_calibrate_error), verdict)
    except LedgerRewriteError:
        raise
    except CalibrationFormatError as e:
        # Fail closed: the report and ledger stay uncommitted and nothing is pushed or fast-forwarded.
        discard_empty_calibrated_branch(context, calibrated_created)
        return _stage_failure(
            context,
            FlowStage.CALIBRATE,
            start_time,
            "Calibrate stage failed: the calibration report did not pass the repository Prettier check, "
            f"so nothing was committed or pushed. {redact_agent_secrets(str(e))}",
        )
    report_dict = outcome.report_dict
    calibration_succeeded = outcome.calibration_succeeded
    promoted_candidates = outcome.promoted_candidates

    end_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    pushed = False
    publish_attempted = not context.dry_run and calibration_succeeded and bool(context.calibrated_branch)
    # A failed calibration commits nothing, so it neither pushes nor fast-forwards the /calibrated branch.
    if not context.dry_run and calibration_succeeded and context.calibrated_branch:
        pushed = publish_calibration(context)

    final_head_ci = check_final_head_ci(context, repo_dir, context.execution_branch) if pushed else not_pushed_record()
    context.log(format_final_head_ci(final_head_ci))
    reverified = reverify_and_report(context, repo_dir, final_head_ci, pushed)
    # A report that never reached origin does not cover the head: only claim it when published or no push was due.
    published = pushed or context.skip_push or not publish_attempted
    calibrated_head = start_sha if calibration_succeeded and published else None
    if not context.dry_run and not calibration_succeeded:
        context.log(
            "Stage 5 completed with a calibration failure: nothing was committed, pushed or fast-forwarded "
            f"for '{context.calibrated_branch}'."
        )
    elif context.calibrated_branch:
        covering = f" covering execution head {calibrated_head[:12]}" if calibrated_head else ""
        context.log(
            f"Stage 5 completed: Calibration analysis generated on branch '{context.calibrated_branch}'{covering}."
        )
    else:
        context.log("Stage 5 completed: Calibration analysis generated in working tree (uncommitted).")
    blocked = reverified["verdict"] == RED  # deliberate: pending/unmeasured stays SUCCESS, logged in ci_verdict
    result = StageResult(
        stage=FlowStage.CALIBRATE,
        status=StageStatus.FAILED if blocked else StageStatus.SUCCESS,
        start_time=start_time,
        end_time=end_time,
        error=blocked_error(reverified) if blocked else None,
        payload={
            "calibrated_branch": context.calibrated_branch,
            "calibrated_head": calibrated_head,
            "calibration_report": report_dict,
            "promoted_candidates": promoted_candidates,
            "stage5_reconciliation": outcome.reconciliation,
            "merged_head_ci": merged_head_ci,
            "final_head_ci": final_head_ci,
            "ci_verdict": reverified,
        },
    )
    context.stage_results["calibrate"] = result
    return result
