from __future__ import annotations

import time
from typing import Any

from sandbox_executor.ledger import generate_action_id
from sandbox_executor.ledger.order import utc_now_z


def calibration_action_row(
    data: dict[str, Any], report_rel: str, outcome: str, error_context: str | None
) -> dict[str, Any]:
    plan_id = str(data.get("plan_id") or "P-unknown")
    execution_branch = data.get("execution_branch")
    if outcome == "success":
        action = f"Calibration report generated for plan {plan_id}"
    else:
        action = f"Calibration failed for plan {plan_id}; nothing committed, row left uncommitted"
    return {
        "action_id": generate_action_id("ACT-CAL"),
        "execution_id": f"CAL-{plan_id}-{int(time.time())}",
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
