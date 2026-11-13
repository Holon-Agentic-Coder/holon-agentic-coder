"""Stage 5 (Post-Execution Calibration): run the calibration engine on the execution branch and commit the
calibrated artefacts.
"""

from __future__ import annotations

import logging
import os
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
from sandbox_executor.flow.review.records import _bounded_error_context
from sandbox_executor.flow.stage_conflicts import check_and_resolve_stage_conflicts
from sandbox_executor.flow.stages.execute import _warn_ledger_failure
from sandbox_executor.flow.stages.review import _checkout_execution_branch
from sandbox_executor.git import run_git
from sandbox_executor.knowledge import (
    ActionIndexDatabase,
    CredentialRedactor,
    promote_pending_candidates,
    reconcile_records,
)
from sandbox_executor.ledger import LedgerRewriteError
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
                detail = CredentialRedactor.redact_text(commit_res.stderr.strip())
                msg = f"Warning: committing Stage 5 reconciliation rows failed: {detail}"
                logger.warning(msg)
                context.log(msg)
    except Exception as e:
        msg = f"Warning: committing Stage 5 reconciliation rows failed: {CredentialRedactor.redact_text(str(e))}"
        logger.warning(msg)
        context.log(msg)


def _commit_stage5_promoted_artifacts(context: FlowContext, repo_dir: str, promoted_count: int = 0) -> None:
    """Commit the Stage 5 promoted knowledge base and wisdom artifacts onto the /calibrated branch.

    Promoted candidates write JSON files into holon-knowledge/kb and holon-knowledge/wisdom
    after reconciliation. This helper stages and commits them onto the /calibrated branch so they
    are fast-forwarded into the execution branch and appear in the PR diff and main history.
    Call only when calibration succeeds and candidates were promoted. Never raises.
    """
    if promoted_count == 0:
        return
    branch = context.calibrated_branch
    if not branch:
        return
    try:
        head = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir, check=False)
        if head.returncode != 0 or head.stdout.strip() != branch:
            context.log(
                f"Stage 5 promoted knowledge/wisdom artifacts left uncommitted: "
                f"HEAD is not the calibrated branch '{branch}'."
            )
            return
        candidate_dirs = ["holon-knowledge/kb", "holon-knowledge/wisdom"]
        existing_paths = [p for p in candidate_dirs if os.path.exists(os.path.join(repo_dir, p))]
        if not existing_paths:
            return
        run_git(["add", *existing_paths], cwd=repo_dir, check=False)
        status_res = run_git(["status", "--porcelain", "--", *existing_paths], cwd=repo_dir, check=False)
        if status_res.returncode != 0:
            return
        if not status_res.stdout.strip():
            context.log("Stage 5 promoted knowledge/wisdom artifacts unchanged: zero modifications to commit.")
            return
        commit_res = run_git(
            [
                "commit",
                "-m",
                "chore(calibration): record stage 5 promoted knowledge and wisdom",
                "--",
                *existing_paths,
            ],
            cwd=repo_dir,
            env=calibration_commit_env(),
            check=False,
        )
        if commit_res.returncode != 0:
            combined_output = (commit_res.stdout + " " + commit_res.stderr).lower()
            if "nothing to commit" not in combined_output:
                detail = CredentialRedactor.redact_text(commit_res.stderr.strip())
                msg = f"Warning: committing Stage 5 promoted knowledge/wisdom artifacts failed: {detail}"
                logger.warning(msg)
                context.log(msg)
    except Exception as e:
        msg = (
            f"Warning: committing Stage 5 promoted knowledge/wisdom artifacts failed: "
            f"{CredentialRedactor.redact_text(str(e))}"
        )
        logger.warning(msg)
        context.log(msg)


def run_calibrate_stage(context: FlowContext) -> StageResult:
    """Execute Stage 5, then return the host worktree to the branch the flow started on."""
    try:
        return _calibrate_execution_branch(context)
    finally:
        restore_starting_branch(context)


def _calibrate_execution_branch(context: FlowContext) -> StageResult:  # noqa: C901, PLR0912, PLR0915
    """Execute Stage 5: Post-Execution Calibration.

    Runs post-execution calibration analysis on the execution branch and creates
    the calibration markdown report on the /calibrated branch.
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
        checkout_err = _checkout_execution_branch(context)
        if checkout_err:
            return _stage_failure(
                context,
                FlowStage.CALIBRATE,
                start_time,
                f"Calibrate stage failed: could not check out execution branch '{context.execution_branch}' "
                f"before calibration: {checkout_err}",
            )
        try:
            start_sha = sync_execution_ref(context)
            calibrated_created = prepare_calibrated_branch(context, start_sha)
        except CalibrationSyncError as e:
            failure = f"Calibrate stage failed: {e}"
            return _stage_failure(context, FlowStage.CALIBRATE, start_time, failure, calibrated_head=None)
        target_branch = context.intent_data.get("target_branch", "main")
        check_and_resolve_stage_conflicts(
            repo_dir, target_branch=target_branch, stage_name="calibrate", branch=context.execution_branch
        )

    calibrated_branch = calibration_branch(context.execution_branch)
    report_dict: dict[str, Any] = {}
    calibration_succeeded = False

    try:
        report = run_calibrate(
            execution_branch=context.execution_branch,
            target_worktree=repo_dir,
            json_output=False,
            skip_commit=context.dry_run,
            log_fn=context.log,
        )
        context.calibrated_branch = report.calibrated_branch
        report_dict = report.to_dict()
        calibration_succeeded = True
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
            f"so nothing was committed or pushed. {e}",
        )
    except Exception as e:
        discard_empty_calibrated_branch(context, calibrated_created)
        context.log(
            f"Calibration run encountered exception: {CredentialRedactor.redact_text(str(e))}. "
            "Generating fallback report."
        )
        clean = context.execution_branch.strip().rstrip("/_").rstrip("/")
        parts = clean.split("/")
        plan_id = "P-unknown"
        for part in parts:
            if part.startswith("P-"):
                plan_id = part
                break
        plans_dir = os.path.join(repo_dir, "plans")
        os.makedirs(plans_dir, exist_ok=True)
        report_rel = f"plans/{plan_id}_calibration.md"
        report_path = os.path.join(repo_dir, report_rel)
        fallback_report_written = not os.path.exists(report_path)
        if fallback_report_written:
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(f"# Calibration Report for {plan_id}\n\nFallback calibration generated.\n")
        try:
            format_calibration_report(report_rel, repo_dir)
        except Exception as fmt_exc:
            fmt_detail = CredentialRedactor.redact_text(str(fmt_exc))
            context.log(f"Warning: Prettier formatting failed for fallback {report_rel}: {fmt_detail}")
        report_dict = {
            "plan_id": plan_id,
            "calibrated_branch": calibrated_branch,
            "markdown_content": f"# Calibration Report for {plan_id}",
        }
        context.calibrated_branch = calibrated_branch
        if not context.dry_run:
            record_calibration_action(
                {**report_dict, "execution_branch": context.execution_branch, "plan_branch": context.plan_branch},
                repo_dir,
                report_rel,
                outcome="failure",
                error_context=_bounded_error_context(f"Calibration run failed: {e}"),
                fallback_report_written=fallback_report_written,
                log_fn=context.log,
            )

    # Reconcile pending action outcomes and promote knowledge candidates
    promoted_candidates: list[dict[str, Any]] = []
    try:
        executions_file = os.path.join(repo_dir, "holon-knowledge/ledger/executions.jsonl")
        db_path = os.path.join(repo_dir, "holon-knowledge/.index/action_index.db")
        exec_status = context.stage_results.get("execute")
        outcome = "success" if exec_status and exec_status.status == StageStatus.SUCCESS else "failure"
        # Dry runs append nothing, so the reconciliation events are only written on a real run.
        if os.path.exists(executions_file) and not context.dry_run:
            reconcile_records(
                ledger_path=executions_file,
                max_age_days=14,
                target_action_id=context.execution_branch,
                verdict=outcome,
                reason=f"Stage 5 calibration reconciled execution outcome to {outcome}",
                db_path=db_path,
            )
            if calibration_succeeded:
                _commit_stage5_reconciliation(context, repo_dir)
            else:
                context.log(
                    f"Stage 5 failure ACT-CAL and reconciliation rows left uncommitted in "
                    f"{LEDGER_EXECUTIONS_REL}: calibration failed."
                )
        if os.path.exists(db_path):
            index_db = ActionIndexDatabase(db_path=db_path)
            promoted_candidates = promote_pending_candidates(
                index_db=index_db,
                kb_dir=os.path.join(repo_dir, "holon-knowledge/kb"),
                wb_dir=os.path.join(repo_dir, "holon-knowledge/wisdom"),
                min_trials=2,
                dry_run=context.dry_run,
            )
            if promoted_candidates:
                context.log(f"Stage 5 promoted {len(promoted_candidates)} knowledge/wisdom candidate(s).")
                if calibration_succeeded and not context.dry_run:
                    _commit_stage5_promoted_artifacts(context, repo_dir, len(promoted_candidates))
                elif not calibration_succeeded:
                    context.log("Stage 5 promoted knowledge/wisdom artifacts left uncommitted: calibration failed.")
    except LedgerRewriteError:
        raise
    except Exception as e:
        _warn_ledger_failure(context, "Stage 5 ledger reconciliation", executions_file, e)

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
            "final_head_ci": final_head_ci,
            "ci_verdict": reverified,
        },
    )
    context.stage_results["calibrate"] = result
    return result
