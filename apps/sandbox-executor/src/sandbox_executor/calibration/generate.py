"""Calibration of one execution branch: identity, refs, metrics, deltas and the rendered report."""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.agent_defaults import LEGACY_DEFAULT_AGENT, LEGACY_DEFAULT_MODEL
from sandbox_executor.calibration.actual import parse_actual_metrics
from sandbox_executor.calibration.branches import extract_branch_components
from sandbox_executor.calibration.deltas import compute_calibration_deltas
from sandbox_executor.calibration.physics import load_metrics_physics
from sandbox_executor.calibration.predicted import parse_predicted_metrics
from sandbox_executor.calibration.refs import resolve_commit_sha, resolve_git_ref
from sandbox_executor.calibration.report import CalibrationReport, EntropyFactors, format_markdown_report
from sandbox_executor.calibration_evaluated_sha import LEDGER_EXECUTIONS_REL, evaluated_code_commit
from sandbox_executor.calibration_execution_owner import unique_plan_branch_of_execution
from sandbox_executor.calibration_touch_set import touch_set_metrics

logger = logging.getLogger(__name__)


def generate_calibration(  # noqa: C901, PLR0915
    execution_branch: str,
    target_worktree: str = ".",
    review_accounting: dict[str, Any] | None = None,
) -> CalibrationReport:
    """Run calibration calculation and return a complete CalibrationReport.

    Args:
        execution_branch: Execution branch, remote tracking ref, or commit SHA.
        target_worktree: Path to the target git worktree directory.
        review_accounting: The flow's review accounting for the "Review Churn" section; None renders "not recorded".

    Returns:
        CalibrationReport: Complete calibration report.
    """
    components = extract_branch_components(execution_branch)
    plan_id = components["plan_id"] or "P-unknown"
    execution_id = components["execution_id"] or "E-unknown"
    intent_branch = components["intent_branch"] or components["execution_branch"]
    plan_branch = components["plan_branch"]

    # If plan_id is missing (e.g. short execution ID), look it up in executions.jsonl
    if (not plan_id or plan_id == "P-unknown") and execution_id and execution_id != "E-unknown":
        resolved_pb = unique_plan_branch_of_execution(
            execution_id, os.path.join(target_worktree, LEDGER_EXECUTIONS_REL)
        )
        if resolved_pb:
            pb_comp = extract_branch_components(resolved_pb)
            plan_id = pb_comp["plan_id"]
            plan_branch = pb_comp["plan_branch"] or resolved_pb
            intent_branch = pb_comp["intent_branch"]

    # Resolve execution reference and plan reference using resolve_git_ref
    try:
        resolved_exec_ref = resolve_git_ref(execution_branch, target_worktree=target_worktree)
    except Exception as e:
        logger.debug("Could not resolve execution_branch '%s': %s", execution_branch, e)
        resolved_exec_ref = execution_branch

    resolved_plan_ref = plan_branch
    if plan_branch:
        try:
            resolved_plan_ref = resolve_git_ref(plan_branch, target_worktree=target_worktree)
        except Exception as e:
            # Keep the name: ledger and dual-EV matching still need it; patch_diff_range degrades the diff base alone.
            logger.debug("Could not resolve plan_branch '%s': %s", plan_branch, e)
            resolved_plan_ref = plan_branch

    # Resolve the evaluated commit: the execution head with Stage 5's own calibration commits discounted
    evaluated_commit_sha: str | None = None
    try:
        head_sha = resolve_commit_sha(resolved_exec_ref, target_worktree=target_worktree)
        evaluated_commit_sha = evaluated_code_commit(head_sha, target_worktree) or head_sha
    except Exception as e:
        logger.debug("Could not resolve commit SHA for '%s': %s", resolved_exec_ref, e)
        if re.fullmatch(r"[0-9a-fA-F]{40}", execution_branch.strip()):
            evaluated_commit_sha = execution_branch.strip()

    # Warn if PR head differs from evaluated commit SHA
    pr_head_ref = os.environ.get("HOLON_PR_HEAD_SHA")
    if pr_head_ref and evaluated_commit_sha and pr_head_ref != evaluated_commit_sha:
        logger.warning(
            "Evaluated commit SHA (%s) diverges from PR head reference (%s).",
            evaluated_commit_sha,
            pr_head_ref,
        )

    # Target calibrated branch
    raw_branch = execution_branch.removesuffix("/_").rstrip("/")
    if raw_branch.startswith("refs/remotes/origin/"):
        raw_branch = raw_branch[len("refs/remotes/origin/") :]
    elif raw_branch.startswith("origin/"):
        raw_branch = raw_branch[len("origin/") :]
    elif raw_branch.startswith("refs/heads/"):
        raw_branch = raw_branch[len("refs/heads/") :]
    calibrated_branch = f"{raw_branch}/calibrated"

    predicted, plan_meta = parse_predicted_metrics(
        plan_branch, target_worktree=target_worktree, execution_id="" if execution_id == "E-unknown" else execution_id
    )
    actual, exec_meta = parse_actual_metrics(
        execution_id, resolved_plan_ref, predicted, execution_branch=resolved_exec_ref, target_worktree=target_worktree
    )

    deltas, ratings, biases = compute_calibration_deltas(predicted, actual)

    agent_id = exec_meta.get("agent") or plan_meta.get("agent") or LEGACY_DEFAULT_AGENT
    agent_version = exec_meta.get("agent_version") or plan_meta.get("agent_version") or "1.1.22"
    model_name = exec_meta.get("model") or plan_meta.get("model") or LEGACY_DEFAULT_MODEL
    timestamp_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    lambda_val, mu_val = load_metrics_physics(target_worktree)

    ssa_fallback = 0.0 if actual.files_changed == 0 else max(0.2, actual.files_changed * 0.1)
    ssa_obs = exec_meta.get("ssa_obs", round(ssa_fallback, 1))
    ef = EntropyFactors(ssa=(0.6, round(ssa_obs, 1)), irr=(0.0, 0.0), cl=(0.1, 0.0), ser=(0.0, 0.0), nov=(0.4, 0.4))

    base_sha = exec_meta.get("base_sha")
    merge_base_sha = exec_meta.get("merge_base_sha")
    merged_commit_count = int(exec_meta.get("merged_commit_count", 0))

    report_dict: dict[str, Any] = {
        "plan_id": plan_id,
        "execution_id": execution_id,
        "intent_branch": intent_branch,
        "plan_branch": plan_branch,
        "execution_branch": execution_branch,
        "calibrated_branch": calibrated_branch,
        "agent_id": agent_id,
        "agent_version": agent_version,
        "model_name": model_name,
        "timestamp": timestamp_str,
        "predicted": predicted.to_dict(),
        "actual": actual.to_dict(),
        "deltas": deltas.to_dict(),
        "entropy_factors": ef.to_dict(),
        "accuracy_ratings": ratings,
        "bias_directions": biases,
        "lambda": lambda_val,
        "mu": mu_val,
        "evaluated_commit_sha": evaluated_commit_sha,
        "committed": True,
        "base_sha": base_sha,
        "merge_base_sha": merge_base_sha,
        "merged_commit_count": merged_commit_count,
        "diff_base": exec_meta.get("diff_base"),
        "ev_clean": actual.ev_clean,
        "ev_with_bugs": actual.ev_with_bugs,
        "p_success_clean": actual.p_success_clean,
        "p_success_with_bugs": actual.p_success_with_bugs,
        "impact_clean": actual.impact_clean,
        "impact_with_bugs": actual.impact_with_bugs,
        "bug_ev_drag": actual.bug_ev_drag,
        "delta_ev_clean": deltas.delta_ev_clean,
        "delta_ev_with_bugs": deltas.delta_ev_with_bugs,
        "touch_set": touch_set_metrics(plan_meta, exec_meta.get("changed_paths"), target_worktree),
        "review_accounting": review_accounting,
    }

    markdown_content = format_markdown_report(report_dict)

    return CalibrationReport(
        plan_id=plan_id,
        execution_id=execution_id,
        intent_branch=intent_branch,
        plan_branch=plan_branch,
        execution_branch=execution_branch,
        calibrated_branch=calibrated_branch,
        agent_id=agent_id,
        agent_version=agent_version,
        model_name=model_name,
        timestamp=timestamp_str,
        predicted=predicted,
        actual=actual,
        deltas=deltas,
        entropy_factors=ef,
        accuracy_ratings=ratings,
        bias_directions=biases,
        markdown_content=markdown_content,
        evaluated_commit_sha=evaluated_commit_sha,
        committed=True,
        base_sha=base_sha,
        merge_base_sha=merge_base_sha,
        merged_commit_count=merged_commit_count,
        touch_set=report_dict["touch_set"],
        diff_base=report_dict["diff_base"],
        review_accounting=review_accounting,
        changed_paths=exec_meta.get("changed_paths"),
        summary_records=exec_meta.get("summary_records") or [],
    )
