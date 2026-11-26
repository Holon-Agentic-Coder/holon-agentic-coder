"""Stage 5 (Post-Execution Calibration): run the calibration engine on the execution branch and commit the
calibrated artefacts.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.calibration.run import calibration_commit_env, record_calibration_action, run_calibrate
from sandbox_executor.calibration_evaluated_sha import LEDGER_EXECUTIONS_REL
from sandbox_executor.calibration_format import CalibrationFormatError
from sandbox_executor.calibration_self_check import CalibrationSelfCheckError
from sandbox_executor.flow.calibrate_promotion import _commit_stage5_promoted_artifacts
from sandbox_executor.flow.calibrate_retry import CalibrationRetriesExhausted, call_with_retries, redacted_cause
from sandbox_executor.flow.calibrate_stale_warning import resolve_stale
from sandbox_executor.flow.calibrate_sync import (
    CalibrationSyncError,
    discard_empty_calibrated_branch,
    prepare_calibrated_branch,
    publish_calibration,
    restore_starting_branch,
    sync_execution_ref,
)
from sandbox_executor.flow.calibrate_target_sync import (
    _CalibrateRestartRequired,
    _leave_calibrated_branch,
    _retry_safe,
    make_calibrate_retry_sync,
    sync_before_calibration,
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
from sandbox_executor.flow.stages.review import _checkout_execution_branch
from sandbox_executor.git import run_git
from sandbox_executor.knowledge import promote_pending_candidates, reconcile_records
from sandbox_executor.ledger import LedgerRewriteError
from sandbox_executor.ledger.fragments import ledger_commit_paths
from sandbox_executor.plan_paths import calibration_report_for_branch, legacy_calibration_report_rel, split_plan_branch
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.ref_names import calibration_branch

logger = logging.getLogger(__name__)


def _commit_stage5_reconciliation(context: FlowContext, repo_dir: str) -> None:
    """Commit the Stage 5 reconciliation rows onto the /calibrated branch.

    ``reconcile_records`` appends after ``run_calibrate`` has committed the calibration report,
    so the rows are committed in a follow-up commit on the same /calibrated branch, before it is
    pushed and fast-forwarded into the execution branch. Call it only after ``run_calibrate``
    returned: when ``run_calibrate`` raised, Stage 5 fails closed before reconciliation, so this
    is never reached. As a further guard, nothing is committed when HEAD is not the /calibrated
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
        ledger_paths = ledger_commit_paths(repo_dir, "executions.jsonl")
        if not ledger_paths:  # an empty pathspec would make ``git commit --`` commit the whole index
            return
        run_git(["add", *ledger_paths], cwd=repo_dir, check=False)
        commit_res = run_git(
            ["commit", "-m", "chore(calibration): record stage 5 reconciliation", "--", *ledger_paths],
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


def stage5_steps() -> Stage5Steps:
    """The Stage 5 step callables, read from this module's globals at call time so test patches here take effect."""
    return Stage5Steps(
        calibrate=run_calibrate,
        reconcile=reconcile_records,
        promote=promote_pending_candidates,
        commit_reconciliation=_commit_stage5_reconciliation,
        commit_promoted=_commit_stage5_promoted_artifacts,
        warn_ledger_failure=_warn_ledger_failure,
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


def _report_rel(context: FlowContext) -> str:
    """The calibration report path beside the plan file; a branch naming no plan uses the flat legacy location."""
    execution_branch = str(context.execution_branch)
    plan_id = split_plan_branch(execution_branch)[1] or "P-unknown"
    return calibration_report_for_branch(execution_branch) or legacy_calibration_report_rel(plan_id)


def _fail_calibration(context: FlowContext, start_time: str, calibrated_created: bool, exc: Exception) -> StageResult:
    """FAILED for a calibration that raised with no retries left: no report, commit, push, reconciliation or
    promotion; an empty /calibrated branch is discarded, a real run appends the failure ACT-CAL row (left
    uncommitted for a ``--from-stage calibrate`` resume to commit), and the checkpoint stays at CALIBRATE."""
    execution_branch, repo_dir = str(context.execution_branch), context.target_worktree
    calibrated = calibration_branch(execution_branch)
    _leave_calibrated_branch(context)
    discard_empty_calibrated_branch(context, calibrated_created)
    exists = run_git(["rev-parse", "--verify", "--quiet", f"refs/heads/{calibrated}"], cwd=repo_dir, check=False)
    context.calibrated_branch = None
    attempts, causes = 1, [redacted_cause(exc)]
    if isinstance(exc, CalibrationRetriesExhausted):
        attempts, causes = exc.attempts, exc.causes
    if not context.dry_run:
        record_calibration_action(
            {
                "plan_id": split_plan_branch(execution_branch)[1] or "P-unknown",
                "calibrated_branch": calibrated if exists.returncode == 0 else None,
                "execution_branch": execution_branch,
                "plan_branch": context.plan_branch,
            },
            repo_dir,
            _report_rel(context),
            outcome="failure",
            error_context=_bounded_error_context(f"Calibration run failed: {causes[-1]}"),
            log_fn=context.log,
        )
    return _stage_failure(
        context,
        FlowStage.CALIBRATE,
        start_time,
        f"Calibrate stage failed after {attempts} attempt(s): {causes[-1]}; nothing was committed or pushed.",
        calibration_attempts=causes,
    )


def _prepare_execution_branch(context: FlowContext, start_time: str) -> tuple[StageResult | None, str | None, bool]:
    """Check out and sync the execution branch and prepare /calibrated: (failure, start sha, calibrated created)."""
    checkout_err = _checkout_execution_branch(context)
    if checkout_err:
        failure = (
            f"Calibrate stage failed: could not check out execution branch '{context.execution_branch}' "
            f"before calibration: {checkout_err}"
        )
        return _stage_failure(context, FlowStage.CALIBRATE, start_time, failure), None, False
    if sync_failure := sync_before_calibration(context, start_time):
        return sync_failure, None, False
    try:
        start_sha = sync_execution_ref(context)
        calibrated_created = prepare_calibrated_branch(context, start_sha)
    except CalibrationSyncError as e:
        failure = f"Calibrate stage failed: {e}"
        return _stage_failure(context, FlowStage.CALIBRATE, start_time, failure, calibrated_head=None), None, False
    return None, start_sha, calibrated_created


def _fail_closed(
    context: FlowContext,
    start_time: str,
    calibrated_created: bool,
    exc: CalibrationFormatError | CalibrationSelfCheckError,
) -> StageResult:
    """FAILED for a report that failed the Prettier check or its self-check: nothing is committed, pushed or
    fast-forwarded, and the checkpoint stays at CALIBRATE."""
    discard_empty_calibrated_branch(context, calibrated_created)
    if isinstance(exc, CalibrationSelfCheckError):
        return _stage_failure(
            context,
            FlowStage.CALIBRATE,
            start_time,
            f"Calibrate stage failed: the calibration report self-check found {', '.join(exc.conditions)}, "
            "so nothing was committed or pushed.",
            self_check_conditions=list(exc.conditions),
        )
    return _stage_failure(
        context,
        FlowStage.CALIBRATE,
        start_time,
        "Calibrate stage failed: the calibration report did not pass the repository Prettier check, "
        f"so nothing was committed or pushed. {redact_agent_secrets(str(exc))}",
    )


def _review_pr_number(context: FlowContext) -> str | None:
    """The pull request Stage 4 reviewed (its consensus, else its ``pr`` entry), approved or not; None if unknown."""
    review = context.stage_results.get("review")
    payload = review.payload if review is not None and isinstance(review.payload, dict) else {}
    entries = (payload.get("consensus"), payload.get("pr"))
    numbers = (entry.get("pr_number") for entry in entries if isinstance(entry, dict))
    return next((str(number) for number in numbers if number), None)


def _supersede_stale_warning(context: FlowContext, evaluated_sha: str) -> dict[str, Any]:
    """Mark an open stale-calibration warning superseded by the report just published; never posts, never raises."""
    record = resolve_stale(context, _review_pr_number(context), evaluated_sha)
    if record.get("edited"):
        context.log(f"Stage 5: the stale-calibration warning is superseded by the report on {evaluated_sha[:12]}.")
    return record


def _publish_and_supersede(
    context: FlowContext, report_dict: dict[str, Any] | None, start_sha: str | None
) -> tuple[bool, dict[str, Any] | None]:
    """Publish /calibrated, then supersede the stale warning before the final-head CI wait: (pushed, its record)."""
    if not publish_calibration(context):
        return False, None
    evaluated_sha = str((report_dict or {}).get("evaluated_commit_sha") or start_sha or "")
    return True, _supersede_stale_warning(context, evaluated_sha)


def _log_calibration_complete(context: FlowContext, calibrated_head: str | None) -> None:
    if context.calibrated_branch:
        covering = f" covering execution head {calibrated_head[:12]}" if calibrated_head else ""
        msg = f"Stage 5 completed: Calibration analysis generated on branch '{context.calibrated_branch}'{covering}."
        context.log(msg)
    else:
        context.log("Stage 5 completed: Calibration analysis generated in working tree (uncommitted).")


def _calibrate_execution_branch(context: FlowContext, merged_head_ci: dict[str, Any] | None = None) -> StageResult:
    """Execute Stage 5: Post-Execution Calibration on the execution branch and check final-head CI."""
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

    steps, retry_tip = stage5_steps(), [""]

    def _on_head_updated(new_sha: str) -> None:
        nonlocal start_sha
        start_sha = retry_tip[0] = new_sha

    retry = call_with_retries(
        steps.calibrate,
        log_fn=context.log,
        retry_safe=_retry_safe(context, _report_rel(context), retry_tip),
        before_retry=make_calibrate_retry_sync(context, on_head_updated=_on_head_updated),
    )
    steps = dataclasses.replace(steps, calibrate=retry)
    try:
        outcome = run_stage5(context, steps, verdict)
    except LedgerRewriteError:
        raise
    except _CalibrateRestartRequired as e:
        discard_empty_calibrated_branch(context, calibrated_created)
        return e.result
    except (CalibrationFormatError, CalibrationSelfCheckError) as e:
        return _fail_closed(context, start_time, calibrated_created, e)
    except Exception as e:
        return _fail_calibration(context, start_time, calibrated_created, e)
    report_dict = outcome.report_dict
    promoted_candidates = outcome.promoted_candidates

    end_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    pushed, stale_warning = False, None
    # run_stage5 raises on every calibration failure, so reaching here means the report was generated.
    publish_attempted = not context.dry_run and bool(context.calibrated_branch)
    if publish_attempted:
        pushed, stale_warning = _publish_and_supersede(context, report_dict, start_sha)

    final_head_ci = check_final_head_ci(context, repo_dir, context.execution_branch) if pushed else not_pushed_record()
    context.log(format_final_head_ci(final_head_ci))
    reverified = reverify_and_report(context, repo_dir, final_head_ci, pushed)
    # A report that never reached origin does not cover the head: only claim it when published or no push was due.
    published = pushed or context.skip_push or not publish_attempted
    calibrated_head = start_sha if published else None
    _log_calibration_complete(context, calibrated_head)
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
            "stale_warning": stale_warning,
        },
    )
    context.stage_results["calibrate"] = result
    return result
