from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import Any, TypedDict, Unpack

from sandbox_executor.calibration.checkout import switch_to_calibrated_branch
from sandbox_executor.calibration.generate import generate_calibration
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
    commit_env = os.environ.copy()
    commit_env["DOCKER_CLI_HINTS"] = "false"
    commit_env.setdefault("GIT_AUTHOR_NAME", "Holon Calibrate Agent")
    commit_env.setdefault("GIT_AUTHOR_EMAIL", "calibrate-agent@holon-agentic-coder.com")
    commit_env.setdefault("GIT_COMMITTER_NAME", "Holon Calibrate Agent")
    commit_env.setdefault("GIT_COMMITTER_EMAIL", "calibrate-agent@holon-agentic-coder.com")
    return commit_env


class CalibrationActionOptions(TypedDict, total=False):
    outcome: str
    error_context: str | None
    log_fn: Callable[[str], None] | None


def record_calibration_action(
    report: CalibrationReport | dict[str, Any],
    target_worktree: str,
    report_rel: str,
    **options: Unpack[CalibrationActionOptions],
) -> dict[str, Any] | None:
    data = report.to_dict() if isinstance(report, CalibrationReport) else dict(report)
    record = calibration_action_row(data, report_rel, options.get("outcome", "success"), options.get("error_context"))
    ledger_path = os.path.join(target_worktree, LEDGER_EXECUTIONS_REL)
    return try_append_action_record(
        ledger_path,
        record,
        label="Stage 5 calibration action ACT-CAL",
        preserve_shape=True,
        log_fn=options.get("log_fn"),
    )


def _write_report(report: CalibrationReport, target_worktree: str) -> str:
    report_rel = calibration_report_for_branch(report.plan_branch) or legacy_calibration_report_rel(report.plan_id)
    report_path = os.path.join(target_worktree, report_rel)
    os.makedirs(os.path.dirname(report_path), exist_ok=True)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report.markdown_content)
    return report_rel


def _format_report(
    report_rel: str, target_worktree: str, skip_commit: bool, log_fn: Callable[[str], None] | None
) -> None:
    if skip_commit:
        try:
            format_calibration_report(report_rel, target_worktree)
        except CalibrationFormatError as e:
            (log_fn or logger.warning)(str(e))
    else:
        format_calibration_report(report_rel, target_worktree)


def _record_action(
    report: CalibrationReport, target_worktree: str, report_rel: str, log_fn: Callable[[str], None] | None
) -> list[str]:
    commit_paths = [report_rel]
    if record_calibration_action(report, target_worktree, report_rel, log_fn=log_fn) is not None:
        commit_paths.extend(ledger_commit_paths(target_worktree, "executions.jsonl"))
    return commit_paths


def _commit_report(report: CalibrationReport, target_worktree: str, report_rel: str, commit_paths: list[str]) -> None:
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


def _emit_output(report: CalibrationReport, report_rel: str, json_output: bool, skip_commit: bool) -> None:
    if json_output:
        print(json.dumps(report.to_dict(), indent=2))
        return
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


def run_calibrate(
    execution_branch: str,
    target_worktree: str = ".",
    json_output: bool = False,
    skip_commit: bool = False,
    log_fn: Callable[[str], None] | None = None,
    **review: Unpack[CalibrateReview],
) -> CalibrationReport:
    accounting = review.get("review_accounting")
    report = generate_calibration(execution_branch, target_worktree=target_worktree, review_accounting=accounting)
    # why: the self-check fails closed before any branch, file or commit is touched
    enforce_report_self_check(report, target_worktree, review.get("approved_head"), log_fn)
    report.committed = not skip_commit
    if skip_commit:
        report.calibrated_branch = None
    else:
        switch_to_calibrated_branch(report, execution_branch, target_worktree)

    report_rel = _write_report(report, target_worktree)
    # why: a report failing the format check must leave no ACT-CAL row, staged file or commit behind
    _format_report(report_rel, target_worktree, skip_commit, log_fn)

    if skip_commit:
        logger.info("Skipping ACT-CAL ledger record for %s: calibration is not being committed", report.plan_id)
    else:
        _commit_report(report, target_worktree, report_rel, _record_action(report, target_worktree, report_rel, log_fn))

    _emit_output(report, report_rel, json_output, skip_commit)
    return report
