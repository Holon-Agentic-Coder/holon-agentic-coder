from __future__ import annotations

import time
from typing import Any

from sandbox_executor.ledger import build_stage_disambiguator, generate_action_id
from sandbox_executor.ledger.order import utc_now_z


def build_calibration_id(plan_id: str, parent_ref: str, epoch: int | None = None) -> str:
    ts = int(time.time()) if epoch is None else epoch
    disambiguator = build_stage_disambiguator(parent_ref)
    return f"CAL-{plan_id}-{ts}-{disambiguator}"


def calibration_action_row(
    data: dict[str, Any], report_rel: str, outcome: str, error_context: str | None
) -> dict[str, Any]:
    plan_id = str(data.get("plan_id") or "P-unknown")
    execution_branch = data.get("execution_branch")
    disambiguator_ref = str(
        data.get("calibrated_branch")
        or execution_branch
        or data.get("plan_branch")
        or data.get("intent_branch")
        or report_rel
        or plan_id
    )
    if outcome == "success":
        action = f"Calibration report generated for plan {plan_id}"
    else:
        action = f"Calibration failed for plan {plan_id}; nothing committed, row left uncommitted"
    return {
        "action_id": generate_action_id("ACT-CAL"),
        "execution_id": build_calibration_id(plan_id, disambiguator_ref),
        "action": action,
        "category": "doc",
        "target": data.get("calibrated_branch") or execution_branch or plan_id,
        "outcome": outcome,
        "entry_type": "action",
        "action_origin": "calibration",
        "labels": ["calibration", "stage-5", plan_id],
        "error_context": error_context,
        "plan_branch": data.get("plan_branch"),
        "execution_branch": execution_branch,
        "evaluated_commit_sha": data.get("evaluated_commit_sha"),
        "predicted_metrics": data.get("predicted"),
        "actual_metrics": data.get("actual"),
        "calibration_deltas": data.get("deltas"),
        "calibration_report_path": report_rel,
        "created_at": utc_now_z(),
        "touch_set_accuracy": data.get("touch_set"),
    }
