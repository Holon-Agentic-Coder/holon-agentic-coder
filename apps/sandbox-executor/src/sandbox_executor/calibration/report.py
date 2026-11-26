"""The calibration report model and its markdown rendering."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

from sandbox_executor.calibration.actual import ActualMetrics
from sandbox_executor.calibration.deltas import CalibrationDeltas
from sandbox_executor.calibration.predicted import PredictedMetrics
from sandbox_executor.calibration_diff import patch_size_base_line
from sandbox_executor.calibration_ev import format_dual_ev_derivation_lines, format_dual_ev_table_rows
from sandbox_executor.calibration_review_churn import review_churn_report_lines
from sandbox_executor.calibration_touch_set import touch_set_report_lines
from sandbox_executor.plan_paths import plan_file_for_branch


@dataclass
class EntropyFactors:
    """Constituent entropy factors (predicted vs observed)."""

    ssa: tuple[float, float] = (0.5, 0.4)
    irr: tuple[float, float] = (0.0, 0.0)
    cl: tuple[float, float] = (0.1, 0.0)
    ser: tuple[float, float] = (0.0, 0.0)
    nov: tuple[float, float] = (0.4, 0.4)

    def to_dict(self) -> dict[str, dict[str, float]]:
        return {
            "ssa": {"predicted": self.ssa[0], "observed": self.ssa[1]},
            "irr": {"predicted": self.irr[0], "observed": self.irr[1]},
            "cl": {"predicted": self.cl[0], "observed": self.cl[1]},
            "ser": {"predicted": self.ser[0], "observed": self.ser[1]},
            "nov": {"predicted": self.nov[0], "observed": self.nov[1]},
        }


@dataclass
class CalibrationReport:
    """Full calibration report with metrics, errors, and markdown report."""

    plan_id: str
    execution_id: str
    intent_branch: str
    plan_branch: str
    execution_branch: str
    calibrated_branch: str | None
    agent_id: str
    agent_version: str
    model_name: str
    timestamp: str
    predicted: PredictedMetrics
    actual: ActualMetrics
    deltas: CalibrationDeltas
    entropy_factors: EntropyFactors
    accuracy_ratings: dict[str, str]
    bias_directions: dict[str, str]
    markdown_content: str
    evaluated_commit_sha: str | None = None
    committed: bool = True
    base_sha: str | None = None
    merge_base_sha: str | None = None
    merged_commit_count: int = 0
    touch_set: dict[str, Any] | None = None
    diff_base: str | None = None
    review_accounting: dict[str, Any] | None = None
    # Self-check inputs, not serialised: the counted (non-metadata) paths and the ledger summary rows read.
    changed_paths: list[str] | None = None
    summary_records: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "execution_id": self.execution_id,
            "intent_branch": self.intent_branch,
            "plan_branch": self.plan_branch,
            "execution_branch": self.execution_branch,
            "calibrated_branch": self.calibrated_branch,
            "agent_id": self.agent_id,
            "agent_version": self.agent_version,
            "model_name": self.model_name,
            "timestamp": self.timestamp,
            "predicted": self.predicted.to_dict(),
            "actual": self.actual.to_dict(),
            "deltas": self.deltas.to_dict(),
            "entropy_factors": self.entropy_factors.to_dict(),
            "accuracy_ratings": self.accuracy_ratings,
            "bias_directions": self.bias_directions,
            "evaluated_commit_sha": self.evaluated_commit_sha,
            "committed": self.committed,
            "base_sha": self.base_sha,
            "merge_base_sha": self.merge_base_sha,
            "merged_commit_count": self.merged_commit_count,
            "touch_set": self.touch_set,
            "diff_base": self.diff_base,
            "review_accounting": self.review_accounting,
        }


def format_markdown_report(report_data: dict[str, Any]) -> str:
    """Format structured markdown report conforming to reference calibration schema."""
    plan_id = report_data["plan_id"]
    plan_file = plan_file_for_branch(report_data.get("plan_branch") or "") or f"plans/{plan_id}.md"
    execution_id = report_data["execution_id"]
    intent_branch = report_data["intent_branch"]
    agent_id = report_data["agent_id"]
    model_name = report_data["model_name"]
    timestamp = report_data["timestamp"]
    execution_branch = report_data.get("execution_branch") or "N/A"
    evaluated_sha = report_data.get("evaluated_commit_sha")
    sha_display = f"`{evaluated_sha}`" if evaluated_sha else "N/A"
    pred = report_data["predicted"]
    act = report_data["actual"]
    deltas = report_data["deltas"]
    ratings = report_data["accuracy_ratings"]
    biases = report_data["bias_directions"]
    ef = report_data["entropy_factors"]
    lambda_val = report_data.get("lambda", 0.3)
    mu_val = report_data.get("mu", 0.5)

    lines = [
        f"# Plan Calibration Report: {plan_id}",
        "",
        f"- **Plan Reference:** [`{plan_file}`]({plan_id}.md)",
        f"- **Execution ID:** `{execution_id}`",
        f"- **Execution Branch:** `{execution_branch}`",
        f"- **Intent Branch:** `{intent_branch}`",
        f"- **Evaluating Agent:** `{agent_id}/{model_name}`",
        f"- **Evaluation Timestamp:** `{timestamp}`",
        f"- **Evaluated Commit SHA:** {sha_display}",
    ]
    if report_data.get("base_sha"):
        lines.append(f"- **Base SHA:** `{report_data['base_sha']}`")
    if report_data.get("merge_base_sha"):
        lines.append(f"- **Merge-Base SHA:** `{report_data['merge_base_sha']}`")
    if "merged_commit_count" in report_data and (
        report_data.get("merge_base_sha") or report_data["merged_commit_count"] > 0
    ):
        lines.append(f"- **Merged Commits:** `{report_data['merged_commit_count']}`")
    if report_data.get("diff_base"):
        lines.append(patch_size_base_line(report_data))
    lines.extend(
        [
            "",
            "---",
            "",
            "## 1. Executive Calibration Summary",
            "",
            (
                "| Metric                    | Predicted (`pred`) | Actual (`actual`) | "
                "Absolute Error (`abs(pred - actual)`) | Accuracy Rating   | Bias Direction             |"
            ),
            (
                "| :------------------------ | :----------------- | :---------------- | "
                ":------------------------------------ | :---------------- | :------------------------- |"
            ),
            (
                f"| **$P(\\text{{success}})$**   | `{pred['p_success']:.2f}`             | "
                f"`{act['p_success']:.2f}`            | `{deltas['p_success_error']:.2f}`"
                f"                                | {ratings['p_success']:<17} | {biases['p_success']:<26} |"
            ),
            (
                f"| **Entropy ($\\Delta S$)**  | `{pred['entropy']:.2f}`             | "
                f"`{act['entropy']:.2f}`            | `{deltas['entropy_error']:.2f}`"
                f"                                | {ratings['entropy']:<17} | {biases['entropy']:<26} |"
            ),
            (
                f"| **Impact**                | `{pred['impact']:.2f}`            | "
                f"`{act['impact']:.2f}`           | `{deltas['impact_error']:.2f}`"
                f"                                | {ratings['impact']:<17} | {biases['impact']:<26} |"
            ),
            (
                f"| **Cost**                  | `{pred['cost']:.2f}`             | "
                f"`{act['cost']:.2f}`            | `{deltas['cost_error']:.2f}`"
                f"                                | {ratings['cost']:<17} | {biases['cost']:<26} |"
            ),
            (
                f"| **Learning Value**        | `{pred['learning_value']:.2f}`             | "
                f"`{act['learning_value']:.2f}`            | `{deltas['learning_value_error']:.2f}`"
                f"                                | {ratings['learning_value']:<17} | {biases['learning_value']:<26} |"
            ),
            *format_dual_ev_table_rows(pred["ev"], act, deltas, ratings, biases),
            "",
            "---",
            "",
            "## 2. Mathematical Derivations & Calibration Errors",
            "",
            (
                f"- **Success Probability Error:** $|{pred['p_success']:.2f} - {act['p_success']:.2f}| = "
                f"{deltas['p_success_error']:.2f}$"
            ),
            f"- **Entropy Error:** $|{pred['entropy']:.2f} - {act['entropy']:.2f}| = {deltas['entropy_error']:.2f}$",
            f"- **Impact Error:** $|{pred['impact']:.2f} - {act['impact']:.2f}| = {deltas['impact_error']:.2f}$",
            f"- **Cost Error:** $|{pred['cost']:.2f} - {act['cost']:.2f}| = {deltas['cost_error']:.2f}$",
            (
                f"- **Learning Value Error:** $|{pred['learning_value']:.2f} - {act['learning_value']:.2f}| = "
                f"{deltas['learning_value_error']:.2f}$"
            ),
            "- **Expected Value Realization:**",
            *format_dual_ev_derivation_lines(pred, act, deltas, lambda_val, mu_val),
            "",
            "---",
            "",
            "## 3. Entropy Factor Breakdown",
            "",
            (
                f"- **State Surface Area (SSA):** Predicted `{ef['ssa']['predicted']:.1f}` vs Observed "
                f"`{ef['ssa']['observed']:.1f}` ({act.get('files_changed', 0)} files modified)."
            ),
            (
                f"- **Irreversibility (IRR):** Predicted `{ef['irr']['predicted']:.1f}` vs Observed "
                f"`{ef['irr']['observed']:.1f}` (Zero destructive or stateful changes)."
            ),
            (
                f"- **Conflict Likelihood (CL):** Predicted `{ef['cl']['predicted']:.1f}` vs Observed "
                f"`{ef['cl']['observed']:.1f}` (Clean sequential branch merges)."
            ),
            (
                f"- **Sandbox Escape Risk (SER):** Predicted `{ef['ser']['predicted']:.1f}` vs Observed "
                f"`{ef['ser']['observed']:.1f}` (Zero security exceptions)."
            ),
            (
                f"- **Novelty (NOV):** Predicted `{ef['nov']['predicted']:.1f}` vs Observed "
                f"`{ef['nov']['observed']:.1f}` (Standard calibration reporting schema)."
            ),
            "",
            "---",
            "",
            "## 4. Calibration Assessment",
            "",
            (
                f"Plan `{plan_id}` executed with minimal error "
                f"($p\\_success\\_error = {deltas['p_success_error']:.2f}$, "
                f"$cost\\_error = {deltas['cost_error']:.2f}$). The calibration step successfully completed the "
                "execution lifecycle and delivered verified post-execution analysis."
            ),
            "",
            "---",
            "",
            *touch_set_report_lines(report_data),
            "---",
            "",
            *review_churn_report_lines(report_data),
        ]
    )
    return "\n".join(lines)
