"""Pure calibration deltas: absolute errors, accuracy ratings and bias directions of predicted versus actual."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from sandbox_executor.calibration.actual import ActualMetrics
from sandbox_executor.calibration.predicted import PredictedMetrics


@dataclass
class CalibrationDeltas:
    """Absolute errors and value deltas between predicted and actual metrics."""

    p_success_error: float
    entropy_error: float
    impact_error: float
    cost_error: float
    learning_value_error: float
    ev_error: float
    delta_ev: float
    delta_ev_clean: float = 0.0
    delta_ev_with_bugs: float = 0.0
    bug_ev_drag: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return dataclasses.asdict(self)


def compute_calibration_deltas(
    predicted: PredictedMetrics,
    actual: ActualMetrics,
) -> tuple[CalibrationDeltas, dict[str, str], dict[str, str]]:
    """Compute mathematical calibration deltas, accuracy ratings, and bias directions."""
    p_err = round(abs(predicted.p_success - actual.p_success), 4)
    ent_err = round(abs(predicted.entropy - actual.entropy), 2)
    imp_err = round(abs(predicted.impact - actual.impact), 2)
    cost_err = round(abs(predicted.cost - actual.cost), 2)
    lv_err = round(abs(predicted.learning_value - actual.learning_value), 2)
    ev_clean_err = round(abs(predicted.ev - actual.ev_clean), 2)
    ev_bugs_err = round(abs(predicted.ev - actual.ev_with_bugs), 2)
    delta_ev_clean = round(actual.ev_clean - predicted.ev, 2)
    delta_ev_with_bugs = round(actual.ev_with_bugs - predicted.ev, 2)

    deltas = CalibrationDeltas(
        p_success_error=p_err,
        entropy_error=ent_err,
        impact_error=imp_err,
        cost_error=cost_err,
        learning_value_error=lv_err,
        ev_error=ev_clean_err,
        delta_ev=delta_ev_clean,
        delta_ev_clean=delta_ev_clean,
        delta_ev_with_bugs=delta_ev_with_bugs,
        bug_ev_drag=actual.bug_ev_drag,
    )

    accuracy_ratings = {
        "p_success": "Exact" if p_err == 0 else ("High (≤ 0.05)" if p_err <= 0.05 else "Moderate"),
        "entropy": "Exact" if ent_err == 0 else ("High (≤ 1.0)" if ent_err <= 1.0 else "Moderate"),
        "impact": "Exact" if imp_err == 0 else ("High" if imp_err <= 5.0 else "Moderate"),
        "cost": "Exact" if cost_err == 0 else ("High (≤ 1.0)" if cost_err <= 1.0 else "Moderate"),
        "learning_value": "Exact" if lv_err == 0 else "High",
        "ev": "Exact" if ev_clean_err == 0 else ("High" if ev_clean_err <= 5.0 else "Moderate"),
        "ev_clean": "Exact" if ev_clean_err == 0 else ("High" if ev_clean_err <= 5.0 else "Moderate"),
        "ev_with_bugs": "Exact" if ev_bugs_err == 0 else ("High" if ev_bugs_err <= 5.0 else "Moderate"),
    }

    bias_directions = {
        "p_success": (
            "Perfectly Calibrated"
            if p_err == 0
            else ("Slight Underconfidence" if predicted.p_success < actual.p_success else "Overconfident")
        ),
        "entropy": (
            "Perfectly Calibrated"
            if ent_err == 0
            else ("Overestimated Risk" if predicted.entropy > actual.entropy else "Underestimated Risk")
        ),
        "impact": "Perfectly Calibrated" if imp_err == 0 else "Accurate Impact",
        "cost": (
            "Highly Accurate"
            if cost_err <= 1.0
            else ("Conservative Cost Estimate" if predicted.cost > actual.cost else "Underestimated Cost")
        ),
        "learning_value": "Perfectly Calibrated" if lv_err == 0 else "Accurate Learning Value",
        "ev": ("Conservative Underestimate" if delta_ev_clean >= 0 else "Optimistic Overestimate"),
        "ev_clean": ("Conservative Underestimate" if delta_ev_clean >= 0 else "Optimistic Overestimate"),
        "ev_with_bugs": ("Conservative Underestimate" if delta_ev_with_bugs >= 0 else "Optimistic Overestimate"),
    }

    return deltas, accuracy_ratings, bias_directions
