from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import NamedTuple

from sandbox_executor.calibration.actual import ActualMetrics
from sandbox_executor.calibration.predicted import PredictedMetrics


@dataclass
class CalibrationDeltas:
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


class _RatingThreshold(NamedTuple):
    metric: str
    error_key: str
    high_limit: float | None
    high_label: str


_RATING_THRESHOLDS: tuple[_RatingThreshold, ...] = (
    _RatingThreshold("p_success", "p_success", 0.05, "High (≤ 0.05)"),
    _RatingThreshold("entropy", "entropy", 1.0, "High (≤ 1.0)"),
    _RatingThreshold("impact", "impact", 5.0, "High"),
    _RatingThreshold("cost", "cost", 1.0, "High (≤ 1.0)"),
    _RatingThreshold("learning_value", "learning_value", None, "High"),
    _RatingThreshold("ev", "ev_clean", 5.0, "High"),
    _RatingThreshold("ev_clean", "ev_clean", 5.0, "High"),
    _RatingThreshold("ev_with_bugs", "ev_with_bugs", 5.0, "High"),
)


def _absolute_errors(predicted: PredictedMetrics, actual: ActualMetrics) -> dict[str, float]:
    return {
        "p_success": round(abs(predicted.p_success - actual.p_success), 4),
        "entropy": round(abs(predicted.entropy - actual.entropy), 2),
        "impact": round(abs(predicted.impact - actual.impact), 2),
        "cost": round(abs(predicted.cost - actual.cost), 2),
        "learning_value": round(abs(predicted.learning_value - actual.learning_value), 2),
        "ev_clean": round(abs(predicted.ev - actual.ev_clean), 2),
        "ev_with_bugs": round(abs(predicted.ev - actual.ev_with_bugs), 2),
    }


def _rate(error: float, high_limit: float | None, high_label: str) -> str:
    if error == 0:
        return "Exact"
    return high_label if high_limit is None or error <= high_limit else "Moderate"


def _accuracy_ratings(errors: dict[str, float]) -> dict[str, str]:
    return {metric: _rate(errors[key], limit, label) for metric, key, limit, label in _RATING_THRESHOLDS}


def _bias_directions(
    predicted: PredictedMetrics, actual: ActualMetrics, errors: dict[str, float], deltas: CalibrationDeltas
) -> dict[str, str]:
    return {
        "p_success": (
            "Perfectly Calibrated"
            if errors["p_success"] == 0
            else ("Slight Underconfidence" if predicted.p_success < actual.p_success else "Overconfident")
        ),
        "entropy": (
            "Perfectly Calibrated"
            if errors["entropy"] == 0
            else ("Overestimated Risk" if predicted.entropy > actual.entropy else "Underestimated Risk")
        ),
        "impact": "Perfectly Calibrated" if errors["impact"] == 0 else "Accurate Impact",
        "cost": (
            "Highly Accurate"
            if errors["cost"] <= 1.0
            else ("Conservative Cost Estimate" if predicted.cost > actual.cost else "Underestimated Cost")
        ),
        "learning_value": "Perfectly Calibrated" if errors["learning_value"] == 0 else "Accurate Learning Value",
        "ev": ("Conservative Underestimate" if deltas.delta_ev_clean >= 0 else "Optimistic Overestimate"),
        "ev_clean": ("Conservative Underestimate" if deltas.delta_ev_clean >= 0 else "Optimistic Overestimate"),
        "ev_with_bugs": ("Conservative Underestimate" if deltas.delta_ev_with_bugs >= 0 else "Optimistic Overestimate"),
    }


def compute_calibration_deltas(
    predicted: PredictedMetrics,
    actual: ActualMetrics,
) -> tuple[CalibrationDeltas, dict[str, str], dict[str, str]]:
    errors = _absolute_errors(predicted, actual)
    delta_ev_clean = round(actual.ev_clean - predicted.ev, 2)
    deltas = CalibrationDeltas(
        p_success_error=errors["p_success"],
        entropy_error=errors["entropy"],
        impact_error=errors["impact"],
        cost_error=errors["cost"],
        learning_value_error=errors["learning_value"],
        ev_error=errors["ev_clean"],
        delta_ev=delta_ev_clean,
        delta_ev_clean=delta_ev_clean,
        delta_ev_with_bugs=round(actual.ev_with_bugs - predicted.ev, 2),
        bug_ev_drag=actual.bug_ev_drag,
    )
    return deltas, _accuracy_ratings(errors), _bias_directions(predicted, actual, errors, deltas)
