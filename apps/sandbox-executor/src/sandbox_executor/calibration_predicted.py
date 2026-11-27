from __future__ import annotations

import logging
import os
import re
from typing import Any, NamedTuple

from sandbox_executor.calibration_ev import is_matching_record, parse_summary_records
from sandbox_executor.calibration_outcome import latest_summary_row, summary_row_order_key
from sandbox_executor.ledger import read_merged_rows, read_merged_text
from sandbox_executor.plan_paths import is_row_of_plan, plan_file_candidates, recorded_plan_file, split_plan_branch

logger = logging.getLogger(__name__)

PREDICTED_FIELDS = ("p_success", "entropy", "impact", "cost", "learning_value", "ev")
MISMATCH_TOLERANCE = 0.01
OVERALL_HEADING = "## Overall Plan Metrics"
EXECUTIONS_JSONL_REL = os.path.join("holon-knowledge", "ledger", "executions.jsonl")

_METRIC_ROW = re.compile(r"\|\s*([a-zA-Z_0-9]+)\s*\|\s*([0-9\.\-]+)\s*\|")


class PredictedMismatch(NamedTuple):
    source: str
    field: str
    selected: float
    other: float


def plans_row_revision(row: dict[str, Any]) -> int:
    try:
        return int(row.get("ledger_revision") or 1)
    except (TypeError, ValueError):
        return 1


def read_plans_rows(plans_jsonl_path: str) -> list[dict[str, Any]]:
    return read_merged_rows(plans_jsonl_path)


def select_plans_row(rows: list[dict[str, Any]], plan_branch: str) -> dict[str, Any] | None:
    matching = [(index, row) for index, row in enumerate(rows) if is_row_of_plan(row, plan_branch)]
    if not matching:
        return None
    _, selected = max(
        matching, key=lambda item: (plans_row_revision(item[1]), summary_row_order_key(item[1])[0], item[0])
    )
    return selected


def _float_fields(source: dict[str, Any], strip_suffix: str = "") -> dict[str, float]:
    metrics: dict[str, float] = {}
    for name, value in source.items():
        field = name.removesuffix(strip_suffix) if strip_suffix else name
        if field not in PREDICTED_FIELDS or isinstance(value, bool):
            continue
        try:
            metrics[field] = float(value)
        except (TypeError, ValueError):
            continue
    return metrics


def metrics_from_row(row: dict[str, Any]) -> dict[str, float]:
    return _float_fields(row)


def overall_plan_metrics(content: str) -> dict[str, float]:
    rows: dict[str, str] = {}
    in_overall = False
    for line in content.splitlines():
        if line.startswith(OVERALL_HEADING):
            in_overall = True
        elif in_overall and line.startswith("## "):
            break
        elif in_overall and (match := _METRIC_ROW.search(line)):
            rows[match.group(1).strip()] = match.group(2).strip()
    return _float_fields(rows, strip_suffix="_pred")


def plan_file_overall_metrics(
    row: dict[str, Any], plan_branch: str, target_worktree: str
) -> tuple[str, dict[str, float]]:
    parent, plan_id = split_plan_branch(plan_branch)
    candidates = [recorded_plan_file(row.get("plan_file"), plan_branch), *plan_file_candidates(parent, plan_id)]
    for rel in dict.fromkeys(c for c in candidates if c):
        path = os.path.join(target_worktree, rel)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                return rel, overall_plan_metrics(f.read())
        except OSError:
            continue
    return "", {}


def execution_predicted_metrics(executions_path: str, execution_id: str, plan_branch: str) -> dict[str, float]:
    content = read_merged_text(executions_path) if execution_id else None
    if content is None:
        return {}
    records = parse_summary_records(content)
    carrying = [
        record
        for record in records
        if isinstance(record.get("predicted_metrics"), dict)
        and str(record.get("execution_id", "")) == execution_id
        and is_matching_record(record, execution_id, plan_branch)
    ]
    latest = latest_summary_row(carrying)
    return _float_fields(latest["predicted_metrics"]) if latest else {}


# why: the warning is advisory; calibration still uses the selected plans row, and ledgers are only ever read here.
def cross_check_predicted(
    selected: dict[str, Any], sources: list[tuple[str, dict[str, float]]]
) -> list[PredictedMismatch]:
    selected_metrics = metrics_from_row(selected)
    revision = plans_row_revision(selected)
    mismatches: list[PredictedMismatch] = []
    for label, other in sources:
        found = [
            PredictedMismatch(label, field, selected_metrics[field], other[field])
            for field in PREDICTED_FIELDS
            if field in selected_metrics
            and field in other
            and abs(selected_metrics[field] - other[field]) > MISMATCH_TOLERANCE
        ]
        if found:
            details = ", ".join(f"{m.field}={m.selected:g} vs {m.field}={m.other:g}" for m in found)
            logger.warning(
                "Plan %s: plans.jsonl row (ledger_revision %d) disagrees with %s: %s; calibration uses the plans.jsonl "
                "row; append a plans correction row (ledger_revision >= 2) to fix it",
                selected.get("plan_id", "?"),
                revision,
                label,
                details,
            )
        mismatches.extend(found)
    return mismatches


def cross_check_selected_row(
    selected: dict[str, Any], plan_branch: str, target_worktree: str, execution_id: str = ""
) -> list[PredictedMismatch]:
    try:
        plan_file, plan_metrics = plan_file_overall_metrics(selected, plan_branch, target_worktree)
        executions_path = os.path.join(target_worktree, EXECUTIONS_JSONL_REL)
        sources = [
            (f"plan file {plan_file} Overall Plan Metrics", plan_metrics),
            (
                f"execution {execution_id} predicted_metrics",
                execution_predicted_metrics(executions_path, execution_id, plan_branch),
            ),
        ]
        return cross_check_predicted(selected, [(label, metrics) for label, metrics in sources if metrics])
    except Exception as e:
        logger.debug("Predicted-metric cross-check skipped: %s", e)
        return []
