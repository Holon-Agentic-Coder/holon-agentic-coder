"""The calibration I/O shell: generate, self-check, switch to the calibrated branch, write, record and commit."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import Any, Unpack

from sandbox_executor.calibration.generate import generate_calibration
from sandbox_executor.calibration.refs import resolve_git_ref
from sandbox_executor.calibration.report import CalibrationReport
from sandbox_executor.calibration_evaluated_sha import LEDGER_EXECUTIONS_REL
from sandbox_executor.calibration_format import (
    CalibrationCommitError,
    CalibrationFormatError,
    format_calibration_report,
)
from sandbox_executor.calibration_record import calibration_action_row
from sandbox_executor.calibration_self_check import CalibrateReview, enforce_report_self_check
from sandbox_executor.git import run_git
from sandbox_executor.ledger import try_append_action_record
from sandbox_executor.ledger.fragments import ledger_commit_paths
from sandbox_executor.plan_paths import calibration_report_for_branch, legacy_calibration_report_rel
from sandbox_executor.redaction import redact_agent_secrets

logger = logging.getLogger(__name__)


def calibration_commit_env() -> dict[str, str]:
    """Return the git environment used for Stage 5 calibration commits.

    Defaults the author and committer identity to the Holon Calibrate Agent unless the
    caller's environment already sets them.
    """
    commit_env = os.environ.copy()
    commit_env["DOCKER_CLI_HINTS"] = "false"
    commit_env.setdefault("GIT_AUTHOR_NAME", "Holon Calibrate Agent")
    commit_env.setdefault("GIT_AUTHOR_EMAIL", "calibrate-agent@holon-agentic-coder.com")
    commit_env.setdefault("GIT_COMMITTER_NAME", "Holon Calibrate Agent")
    commit_env.setdefault("GIT_COMMITTER_EMAIL", "calibrate-agent@holon-agentic-coder.com")
    return commit_env


def record_calibration_action(  # noqa: PLR0913
    report: CalibrationReport | dict[str, Any],
    target_worktree: str,
    report_rel: str,
    *,
    outcome: str = "success",
    error_context: str | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """Append the Stage 5 ACT-CAL action record to the target repository's executions.jsonl.

    The record is credential-redacted (including ``error_context``), validated and written
    verbatim under LedgerLock. It carries the plan and
    execution branches, the predicted and actual metrics, the calibration deltas and the
    calibration report path. A failed append is logged at WARNING level (and mirrored to
    ``log_fn``) and never raises.

    Args:
        report: Calibration report, or a dictionary in ``CalibrationReport.to_dict()`` form.
        target_worktree: Target git worktree directory.
        report_rel: Repository-relative path of the calibration report.
        outcome: Action outcome, ``"success"`` or ``"failure"``. A failure row is written by a
            Stage 5 run that failed closed: nothing was committed (a report rejected at commit time
            may remain in the working tree) and the row stays uncommitted until a
            ``--from-stage calibrate`` resume commits it.
        error_context: Optional failure detail (the redacted cause of a failure row).
        log_fn: Optional operator-facing log callable (for example ``FlowContext.log``).

    Returns:
        The written record, or None if the append failed.
    """
    data = report.to_dict() if isinstance(report, CalibrationReport) else dict(report)
    record = calibration_action_row(data, report_rel, outcome, error_context)
    ledger_path = os.path.join(target_worktree, LEDGER_EXECUTIONS_REL)
    return try_append_action_record(
        ledger_path, record, label="Stage 5 calibration action ACT-CAL", preserve_shape=True, log_fn=log_fn
    )


def run_calibrate(  # noqa: C901, PLR0912, PLR0915
    execution_branch: str,
    target_worktree: str = ".",
    json_output: bool = False,
    skip_commit: bool = False,
    log_fn: Callable[[str], None] | None = None,
    **review: Unpack[CalibrateReview],
) -> CalibrationReport:
    """Execute holon calibrate workflow: checkout calibrated branch, generate & commit report.

    Args:
        execution_branch: Target execution branch or commit reference.
        target_worktree: Path to the target git worktree directory.
        json_output: Whether to print report JSON to stdout.
        skip_commit: If True, writes report to working tree without creating a branch or commit.
        log_fn: Optional operator-facing log callable (for example ``FlowContext.log``) that
            receives a failed ACT-CAL ledger append warning. When None, the warning goes to the
            module logger only.
        **review: ``CalibrateReview`` keywords; ``approved_head`` feeds the report self-check and
            ``review_accounting`` the report's "Review Churn" section.

    Returns:
        CalibrationReport: Generated calibration report.

    Raises:
        RuntimeError: If git branch checkout or diff operations fail.
        CalibrationCommitError: If git rejects the calibration report commit (hook, signing or
            other non-"nothing to commit" failure). The report and the ACT-CAL row are left
            uncommitted in the working tree.
        CalibrationFormatError: If the report fails the repository Prettier check (or the check
            cannot run) when ``skip_commit`` is False. Raised before the ACT-CAL ledger append and
            any ``git add`` or ``git commit``, so nothing is recorded, committed or pushed.
        CalibrationSelfCheckError: If the report fails its self-check (``calibration_self_check``).
            Raised before any branch checkout, report write, ledger append or commit.
    """
    accounting = review.get("review_accounting")
    report = generate_calibration(execution_branch, target_worktree=target_worktree, review_accounting=accounting)
    # Fail closed before any branch, file or commit is touched
    enforce_report_self_check(report, target_worktree, review.get("approved_head"), log_fn)
    report.committed = not skip_commit
    if skip_commit:
        report.calibrated_branch = None

    # 1. Switch or create /calibrated branch (only if not skip_commit)
    if not skip_commit:
        calibrated_branch = report.calibrated_branch
        local_check = run_git(
            ["rev-parse", "--verify", f"refs/heads/{calibrated_branch}"], cwd=target_worktree, check=False
        )
        remote_check = run_git(
            ["rev-parse", "--verify", f"refs/remotes/origin/{calibrated_branch}"], cwd=target_worktree, check=False
        )
        local_exists = local_check.returncode == 0
        remote_exists = remote_check.returncode == 0

        if local_exists:
            res_co = run_git(["checkout", calibrated_branch], cwd=target_worktree, check=False, timeout=30)
            if res_co.returncode != 0:
                raise RuntimeError(
                    f"Failed to checkout existing calibrated branch '{calibrated_branch}': {res_co.stderr.strip()}"
                )
            if remote_exists:
                res_mg = run_git(
                    ["merge", "--ff-only", f"refs/remotes/origin/{calibrated_branch}"],
                    cwd=target_worktree,
                    check=False,
                    timeout=30,
                )
                if res_mg.returncode != 0:
                    logger.warning("Could not fast-forward calibrated branch to remote: %s", res_mg.stderr.strip())
        elif remote_exists:
            res_co = run_git(
                ["checkout", "-b", calibrated_branch, f"refs/remotes/origin/{calibrated_branch}"],
                cwd=target_worktree,
                check=False,
                timeout=30,
            )
            if res_co.returncode != 0:
                raise RuntimeError(
                    f"Failed to checkout remote calibrated branch '{calibrated_branch}': {res_co.stderr.strip()}"
                )
        else:
            start_point = (
                report.evaluated_commit_sha
                or resolve_git_ref(execution_branch, target_worktree=target_worktree)
                or execution_branch
            )
            res_co = run_git(
                ["checkout", "-b", calibrated_branch, start_point], cwd=target_worktree, check=False, timeout=30
            )
            if res_co.returncode != 0:
                err_msg = res_co.stderr.strip()
                raise RuntimeError(
                    f"Failed to create calibrated branch '{calibrated_branch}' from '{start_point}': {err_msg}"
                )

    # 2. Write the calibration markdown report beside the plan file: plans/{intent}/{plan_id}_calibration.md
    report_rel = calibration_report_for_branch(report.plan_branch) or legacy_calibration_report_rel(report.plan_id)
    report_path = os.path.join(target_worktree, report_rel)
    os.makedirs(os.path.dirname(report_path), exist_ok=True)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report.markdown_content)

    # Format and check the report after its final write, before the ACT-CAL append, git add or git commit
    if skip_commit:
        try:
            format_calibration_report(report_rel, target_worktree)
        except CalibrationFormatError as e:
            (log_fn or logger.warning)(str(e))
    else:
        format_calibration_report(report_rel, target_worktree)

    # 3. Record the Stage 5 ACT-CAL action and commit it with the report on the calibrated branch
    commit_paths = [report_rel]
    if skip_commit:
        logger.info("Skipping ACT-CAL ledger record for %s: calibration is not being committed", report.plan_id)
    elif record_calibration_action(report, target_worktree, report_rel, log_fn=log_fn) is not None:
        commit_paths.extend(ledger_commit_paths(target_worktree, "executions.jsonl"))

    if not skip_commit:
        try:
            run_git(["add", *commit_paths], cwd=target_worktree, check=False)
            commit_env = calibration_commit_env()

            commit_msg = f"chore(calibration): add calibration report for {report.plan_id}"
            commit_res = run_git(
                ["commit", "-m", commit_msg, "--", *commit_paths], cwd=target_worktree, env=commit_env, check=False
            )
        except Exception as e:
            detail = redact_agent_secrets(str(e))
            logger.warning("Git commit of calibration report %s failed: %s", report_rel, detail)
            raise CalibrationCommitError(f"Git commit of calibration report {report_rel} failed: {detail}") from e
        if commit_res.returncode != 0:
            combined_output = (commit_res.stdout + " " + commit_res.stderr).lower()
            if "nothing to commit" in combined_output:
                logger.info("Nothing to commit for calibration report on %s", report.calibrated_branch)
            else:
                detail = redact_agent_secrets(commit_res.stderr.strip() or commit_res.stdout.strip())
                logger.warning("Git commit of calibration report %s failed: %s", report_rel, detail)
                raise CalibrationCommitError(
                    f"Git commit of calibration report {report_rel} failed (exit {commit_res.returncode}): {detail}"
                )

    if json_output:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        if skip_commit:
            print(f"Calibration report generated in working tree at {report_rel} (uncommitted)")
        else:
            print(f"Calibration report generated and committed at {report_rel}")
            print(f"Calibrated branch: {report.calibrated_branch}")
        ev_msg = (
            f"Predicted EV: {report.predicted.ev:.2f} | Actual EV: {report.actual.ev:.2f} "
            f"(ΔEV: {report.deltas.delta_ev:+.2f})"
        )
        print(ev_msg)

    return report
