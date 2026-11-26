"""Actual execution metrics measured from the executions ledger and the execution patch."""

from __future__ import annotations

import contextlib
import dataclasses
import os
import re
from dataclasses import dataclass
from typing import Any

from sandbox_executor.calibration.branches import extract_branch_components
from sandbox_executor.calibration.physics import load_metrics_physics
from sandbox_executor.calibration.predicted import PredictedMetrics
from sandbox_executor.calibration.refs import read_git_file
from sandbox_executor.calibration_diff import execution_patch_numstat
from sandbox_executor.calibration_ev import evaluate_dual_ev_outcomes, is_matching_record, parse_summary_records
from sandbox_executor.calibration_evaluated_sha import LEDGER_EXECUTIONS_REL
from sandbox_executor.calibration_outcome import is_converged_record, latest_summary_row
from sandbox_executor.calibration_rows import is_execution_row
from sandbox_executor.git import is_metadata_path
from sandbox_executor.ledger import read_merged_text, with_ref_fragments


@dataclass
class ActualMetrics:
    """Actual outcome metrics measured post-execution."""

    p_success: float = 1.0
    entropy: float = 0.5
    impact: float = 50.0
    cost: float = 4.0
    learning_value: float = 3.0
    ev: float = 47.35
    duration_seconds: float | None = None
    tokens: int | None = None
    exit_code: int = 0
    # None = not measured; only a test pass rate recorded on the execution summary row is reported.
    test_pass_rate: float | None = None
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    ev_clean: float = 0.0
    ev_with_bugs: float = 0.0
    p_success_clean: float = 0.0
    p_success_with_bugs: float = 0.0
    impact_clean: float = 0.0
    impact_with_bugs: float = 0.0
    bug_ev_drag: float = 0.0

    def __post_init__(self) -> None:
        if self.ev != 0.0:
            self.ev_clean = self.ev_clean or self.ev
            self.p_success_clean = self.p_success_clean or self.p_success
            self.impact_clean = self.impact_clean or self.impact
        if self.bug_ev_drag == 0.0:
            self.ev_with_bugs = self.ev_with_bugs or self.ev_clean
            self.p_success_with_bugs = self.p_success_with_bugs or self.p_success_clean
            self.impact_with_bugs = self.impact_with_bugs or self.impact_clean

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def parse_actual_metrics(  # noqa: C901, PLR0912, PLR0915
    execution_id: str,
    plan_branch: str,
    predicted: PredictedMetrics,
    execution_branch: str = "",
    target_worktree: str = ".",
) -> tuple[ActualMetrics, dict[str, Any]]:
    """Ingest actual execution telemetry and metrics from ledger and git.

    Args:
        execution_id: Execution identifier (e.g. E-...).
        plan_branch: Plan branch reference (e.g. I-.../P-.../_).
        predicted: Predicted metrics model.
        execution_branch: Execution branch or commit reference.
        target_worktree: Target git worktree directory.

    Returns:
        tuple[ActualMetrics, dict[str, Any]]: Actual metrics object and raw telemetry dict.

    Raises:
        RuntimeError: If git diff calculation encounters non-zero exit code.
    """
    exec_meta: dict[str, Any] = {}
    actual = ActualMetrics()
    target_ref = execution_branch if execution_branch else "HEAD"

    # 1. Search holon-knowledge/ledger/executions.jsonl via git show first, then fallback to disk
    canonical_content = read_git_file(target_ref, LEDGER_EXECUTIONS_REL, target_worktree)
    jsonl_content = with_ref_fragments(canonical_content, target_worktree, target_ref, "executions.jsonl")
    records = parse_summary_records(jsonl_content) if jsonl_content else []
    matching = [r for r in records if is_matching_record(r, execution_id, plan_branch)]
    if not matching and (disk_content := read_merged_text(os.path.join(target_worktree, LEDGER_EXECUTIONS_REL))):
        with contextlib.suppress(Exception):
            disk_records = parse_summary_records(disk_content)
            disk_matching = [r for r in disk_records if is_matching_record(r, execution_id, plan_branch)]
            if disk_matching:
                records = disk_records
                matching = disk_matching

    # Only a Stage 3 execution row sets exit_code and p_success: a Stage 4 or Stage 5 row describes a later stage.
    best_record = latest_summary_row([r for r in matching if is_execution_row(r, execution_id)])

    if best_record is not None:
        exec_meta = dict(best_record)
        # An integrated decomposed parent (its children merged into the plan branch, then the plan branch into the
        # execution branch) is a successful run: its superseding row carries no exit_code.
        converged = is_converged_record(best_record)
        actual.exit_code = 0 if converged else int(best_record.get("exit_code", 1))
        actual.p_success = 1.0 if converged and actual.exit_code == 0 else 0.0
        if "duration" in best_record:
            actual.duration_seconds = float(best_record["duration"])
        if "tokens" in best_record:
            actual.tokens = int(best_record["tokens"])
        raw_rate = best_record.get("test_pass_rate")
        if isinstance(raw_rate, int | float) and not isinstance(raw_rate, bool) and 0.0 <= float(raw_rate) <= 1.0:
            actual.test_pass_rate = float(raw_rate)

    # 2. Compute patch size via git diff from the merge-base with the intent's target branch (fail loudly on non-zero
    # exit); the plan branch and its fallbacks apply only when no target branch resolves
    intent_branch = extract_branch_components(plan_branch)["intent_branch"] if plan_branch else ""
    res, patch_meta = execution_patch_numstat(plan_branch, execution_branch, target_ref, target_worktree, intent_branch)
    exec_meta.update(patch_meta)

    actual.files_changed = 0
    actual.insertions = 0
    actual.deletions = 0
    # Starts empty; becomes None below for shortstat output, which lists no paths, so the touch set is not scored
    exec_meta["changed_paths"] = []
    if res.stdout.strip():
        text = res.stdout.strip()
        if "\t" in text:
            # Parse git diff --numstat output, excluding metadata and planning artifacts
            for line in text.splitlines():
                parts = line.strip().split("\t")
                if len(parts) >= 3:
                    if is_metadata_path(parts[2]):
                        continue
                    exec_meta["changed_paths"].append(parts[2].strip())
                    actual.files_changed += 1
                    if parts[0] != "-":
                        actual.insertions += int(parts[0])
                    if parts[1] != "-":
                        actual.deletions += int(parts[1])
        else:
            # Fallback for shortstat text (e.g. unit test mocks): no paths, so the touch set is not scored
            exec_meta["changed_paths"] = None
            fc_m = re.search(r"(\d+)\s+file", text)
            ins_m = re.search(r"(\d+)\s+insertion", text)
            del_m = re.search(r"(\d+)\s+deletion", text)
            actual.files_changed = int(fc_m.group(1)) if fc_m else 0
            actual.insertions = int(ins_m.group(1)) if ins_m else 0
            actual.deletions = int(del_m.group(1)) if del_m else 0

    # Derive actual impact, cost, learning_value, and entropy
    actual.impact = predicted.impact if actual.p_success > 0.5 else 0.0
    actual.learning_value = predicted.learning_value

    if "cost" in exec_meta:
        actual.cost = float(exec_meta["cost"])
    elif "cost_actual" in exec_meta:
        actual.cost = float(exec_meta["cost_actual"])
    else:
        actual.cost = round(max(0.5, predicted.cost * (0.85 if actual.p_success == 1.0 else 1.1)), 2)

    if actual.files_changed == 0:
        ssa_obs = 0.0
    else:
        ssa_raw = actual.files_changed * 0.2 + (actual.insertions + actual.deletions) * 0.005
        ssa_obs = round(min(10.0, max(0.2, ssa_raw)), 2)
    irr_obs = 0.0
    cl_obs = 0.0
    ser_obs = 0.0
    nov_obs = 0.4
    u_weights = [0.30, 0.25, 0.20, 0.15, 0.10]
    actual.entropy = round(
        u_weights[0] * ssa_obs
        + u_weights[1] * irr_obs
        + u_weights[2] * cl_obs
        + u_weights[3] * ser_obs
        + u_weights[4] * nov_obs,
        2,
    )
    exec_meta["ssa_obs"] = ssa_obs
    exec_meta["summary_records"] = records

    lambda_p, mu_p = load_metrics_physics(target_worktree)
    cost_effort = round(actual.cost + (lambda_p * actual.entropy) - (mu_p * actual.learning_value), 2)
    dual_ev = evaluate_dual_ev_outcomes(records, execution_id, plan_branch, predicted.impact, cost_effort)

    actual.p_success_clean = dual_ev.p_success_clean
    actual.p_success_with_bugs = dual_ev.p_success_with_bugs
    actual.impact_clean = dual_ev.impact_clean
    actual.impact_with_bugs = dual_ev.impact_with_bugs
    actual.ev_clean = dual_ev.ev_clean
    actual.ev_with_bugs = dual_ev.ev_with_bugs
    actual.bug_ev_drag = dual_ev.bug_ev_drag

    actual.p_success = actual.p_success_clean
    actual.impact = actual.impact_clean
    actual.ev = actual.ev_clean

    return actual, exec_meta
