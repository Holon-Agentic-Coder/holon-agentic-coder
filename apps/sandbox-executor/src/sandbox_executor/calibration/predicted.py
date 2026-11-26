"""Predicted plan metrics: the plans.jsonl row of the plan, else its plan file, else the defaults."""

from __future__ import annotations

import contextlib
import dataclasses
import os
import re
from dataclasses import dataclass
from typing import Any

from sandbox_executor.calibration_predicted import (
    cross_check_selected_row,
    metrics_from_row,
    read_plans_rows,
    select_plans_row,
)
from sandbox_executor.plan_paths import plan_file_candidates, split_plan_branch


@dataclass
class PredictedMetrics:
    """Predicted metrics estimated during plan generation."""

    p_success: float = 0.90
    entropy: float = 2.0
    impact: float = 50.0
    cost: float = 5.0
    learning_value: float = 3.0
    ev: float = 35.0

    def to_dict(self) -> dict[str, float]:
        return dataclasses.asdict(self)

    @classmethod
    def from_plan(
        cls, plan_branch: str, target_worktree: str = ".", *, execution_id: str = ""
    ) -> tuple[PredictedMetrics, dict[str, Any]]:
        """The plan's plans.jsonl row, else its plan file, else the defaults: ``(metrics, plan metadata)``."""
        return (
            _parse_predicted_from_plans_ledger(plan_branch, target_worktree, execution_id)
            or _parse_predicted_from_plan_markdown(plan_branch, target_worktree)
            or (cls(), {})
        )


def _parse_plan_markdown_content(content: str, predicted: PredictedMetrics) -> None:
    row_pat = re.compile(r"\|\s*([a-zA-Z_0-9]+)\s*\|\s*([0-9\.\-]+)\s*\|")
    in_overall = found_overall = False
    for line in content.splitlines():
        if "## Overall Plan Metrics" in line:
            in_overall = found_overall = True
        elif in_overall and line.startswith("## "):
            break
        elif (in_overall or not found_overall) and (m := row_pat.search(line)):
            name = m.group(1).replace("_pred", "").strip()
            if hasattr(predicted, name):
                with contextlib.suppress(ValueError):
                    setattr(predicted, name, float(m.group(2).strip()))


def _parse_predicted_from_plans_ledger(
    plan_branch: str, target_worktree: str, execution_id: str = ""
) -> tuple[PredictedMetrics, dict[str, Any]] | None:
    """The plan's highest-revision plans.jsonl row, keyed on intent and plan id (never the id alone), cross-checked."""
    plans_jsonl = os.path.join(target_worktree, "holon-knowledge", "ledger", "plans.jsonl")
    record = select_plans_row(read_plans_rows(plans_jsonl), plan_branch)
    if record is None:
        return None
    cross_check_selected_row(record, plan_branch, target_worktree, execution_id)
    return PredictedMetrics(**metrics_from_row(record)), record


def _parse_predicted_from_plan_markdown(
    plan_branch: str, target_worktree: str
) -> tuple[PredictedMetrics, dict[str, Any]] | None:
    """The plan file of ``plan_branch``: the ``plans/{intent}/`` layout first, then the flat legacy location."""
    parent, plan_id = split_plan_branch(plan_branch)
    if not plan_id:
        return None
    candidates = [os.path.join(target_worktree, rel) for rel in plan_file_candidates(parent, plan_id)]
    for candidate in candidates:
        if not os.path.exists(candidate):
            continue
        with contextlib.suppress(Exception), open(candidate, encoding="utf-8") as f:
            content = f.read()
            predicted = PredictedMetrics()
            _parse_plan_markdown_content(content, predicted)
            return predicted, {"plan_file": os.path.relpath(candidate, target_worktree)}
    return None


def parse_predicted_metrics(
    plan_branch: str, target_worktree: str = ".", *, execution_id: str = ""
) -> tuple[PredictedMetrics, dict[str, Any]]:
    """Parse predicted metrics of the plan on ``plan_branch`` (or below it) from its plans.jsonl row or plan file."""
    return PredictedMetrics.from_plan(plan_branch, target_worktree, execution_id=execution_id)
