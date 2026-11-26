"""Actual execution metrics measured from the executions ledger and the execution patch.

Each metric lands in its own small extractor; ``ACTUAL_METRIC_EXTRACTORS`` runs them in order, so a new metric is one
new function and one new table row.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sandbox_executor.calibration.actual_evidence import (
    ExecutionEvidence,
    gather_execution_evidence,
    parse_numstat,
    parse_shortstat,
)
from sandbox_executor.calibration.physics import load_metrics_physics
from sandbox_executor.calibration.predicted import PredictedMetrics
from sandbox_executor.calibration_ev import evaluate_dual_ev_outcomes
from sandbox_executor.calibration_outcome import is_converged_record


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

    @classmethod
    def from_execution(
        cls,
        execution_id: str,
        plan_branch: str,
        predicted: PredictedMetrics,
        execution_branch: str = "",
        target_worktree: str = ".",
    ) -> tuple[ActualMetrics, dict[str, Any]]:
        """Measure one execution: gather its evidence, then run ``ACTUAL_METRIC_EXTRACTORS`` in order.

        Returns ``(actual metrics, raw telemetry)``; raises ``RuntimeError`` if the git diff exits non-zero.
        """
        evidence = gather_execution_evidence(execution_id, plan_branch, predicted, execution_branch, target_worktree)
        actual = cls()
        exec_meta: dict[str, Any] = {}
        for _metric, extract in ACTUAL_METRIC_EXTRACTORS:
            extract(actual, exec_meta, evidence)
        return actual, exec_meta


def _extract_run_outcome(actual: ActualMetrics, meta: dict[str, Any], evidence: ExecutionEvidence) -> None:
    """Copy the Stage 3 summary row into the telemetry; set ``exit_code`` and ``p_success`` from it."""
    record = evidence.best_record
    if record is None:
        return
    meta.update(record)
    # An integrated decomposed parent (its children merged into the plan branch, then the plan branch into the
    # execution branch) is a successful run: its superseding row carries no exit_code.
    converged = is_converged_record(record)
    actual.exit_code = 0 if converged else int(record.get("exit_code", 1))
    actual.p_success = 1.0 if converged and actual.exit_code == 0 else 0.0


def _extract_duration(actual: ActualMetrics, _meta: dict[str, Any], evidence: ExecutionEvidence) -> None:
    """The run duration recorded on the summary row."""
    if evidence.best_record is not None and "duration" in evidence.best_record:
        actual.duration_seconds = float(evidence.best_record["duration"])


def _extract_tokens(actual: ActualMetrics, _meta: dict[str, Any], evidence: ExecutionEvidence) -> None:
    """The token count recorded on the summary row."""
    if evidence.best_record is not None and "tokens" in evidence.best_record:
        actual.tokens = int(evidence.best_record["tokens"])


def _extract_test_pass_rate(actual: ActualMetrics, _meta: dict[str, Any], evidence: ExecutionEvidence) -> None:
    """The measured test pass rate on the summary row: a number (not a bool) in ``0..1``, else not measured."""
    raw_rate = (evidence.best_record or {}).get("test_pass_rate")
    if isinstance(raw_rate, int | float) and not isinstance(raw_rate, bool) and 0.0 <= float(raw_rate) <= 1.0:
        actual.test_pass_rate = float(raw_rate)


def _extract_patch_size(actual: ActualMetrics, meta: dict[str, Any], evidence: ExecutionEvidence) -> None:
    """Files changed, insertions and deletions of the execution patch, and the changed (non-metadata) paths."""
    meta.update(evidence.patch_meta)
    actual.files_changed = 0
    actual.insertions = 0
    actual.deletions = 0
    # Starts empty; becomes None below for shortstat output, which lists no paths, so the touch set is not scored
    meta["changed_paths"] = []
    text = evidence.numstat_stdout.strip()
    if not text:
        return
    if "\t" in text:
        meta["changed_paths"], actual.files_changed, actual.insertions, actual.deletions = parse_numstat(text)
    else:
        meta["changed_paths"] = None
        actual.files_changed, actual.insertions, actual.deletions = parse_shortstat(text)


def _extract_value(actual: ActualMetrics, _meta: dict[str, Any], evidence: ExecutionEvidence) -> None:
    """Impact (the predicted impact of a successful run, else 0) and the predicted learning value."""
    actual.impact = evidence.predicted.impact if actual.p_success > 0.5 else 0.0
    actual.learning_value = evidence.predicted.learning_value


def _extract_cost(actual: ActualMetrics, meta: dict[str, Any], evidence: ExecutionEvidence) -> None:
    """The recorded ``cost``, else ``cost_actual``, else the predicted cost scaled by the outcome."""
    if "cost" in meta:
        actual.cost = float(meta["cost"])
    elif "cost_actual" in meta:
        actual.cost = float(meta["cost_actual"])
    else:
        actual.cost = round(max(0.5, evidence.predicted.cost * (0.85 if actual.p_success == 1.0 else 1.1)), 2)


def _extract_entropy(actual: ActualMetrics, meta: dict[str, Any], evidence: ExecutionEvidence) -> None:
    """Observed entropy: the weighted factor sum, with the state surface area taken from the patch size."""
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
    meta["ssa_obs"] = ssa_obs
    meta["summary_records"] = evidence.records


def _extract_dual_ev(actual: ActualMetrics, _meta: dict[str, Any], evidence: ExecutionEvidence) -> None:
    """The clean and with-bugs EV outcomes; the clean outcome becomes ``p_success``, ``impact`` and ``ev``."""
    lambda_p, mu_p = load_metrics_physics(evidence.target_worktree)
    cost_effort = round(actual.cost + (lambda_p * actual.entropy) - (mu_p * actual.learning_value), 2)
    dual_ev = evaluate_dual_ev_outcomes(
        evidence.records, evidence.execution_id, evidence.plan_branch, evidence.predicted.impact, cost_effort
    )

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


MetricExtractor = Callable[[ActualMetrics, dict[str, Any], ExecutionEvidence], None]

# Order matters: the run outcome copies the summary row into the telemetry before the patch metadata is merged over
# it, and cost reads the telemetry after both; dual EV reads cost, entropy and learning value last.
ACTUAL_METRIC_EXTRACTORS: tuple[tuple[str, MetricExtractor], ...] = (
    ("run_outcome", _extract_run_outcome),
    ("duration", _extract_duration),
    ("tokens", _extract_tokens),
    ("test_pass_rate", _extract_test_pass_rate),
    ("patch_size", _extract_patch_size),
    ("value", _extract_value),
    ("cost", _extract_cost),
    ("entropy", _extract_entropy),
    ("dual_ev", _extract_dual_ev),
)


def parse_actual_metrics(
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
    return ActualMetrics.from_execution(execution_id, plan_branch, predicted, execution_branch, target_worktree)
