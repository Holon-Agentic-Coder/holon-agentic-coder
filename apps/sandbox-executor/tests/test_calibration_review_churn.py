"""Tests for the calibration report's "Review Churn" section."""

from __future__ import annotations

import re
import subprocess
import sys

import pytest
from sandbox_executor.calibration import (
    ActualMetrics,
    EntropyFactors,
    PredictedMetrics,
    compute_calibration_deltas,
    format_markdown_report,
)
from sandbox_executor.calibration_review_churn import review_churn_report_lines

ACCOUNTING = {
    "full_reviews": 2,
    "scoped_reviews": 3,
    "pipeline_runs": 4,
    "pipeline_reruns": 1,
    "approval_carry_overs": 1,
    "non_defect_churn": {"target_merge": 1, "bookkeeping": 2, "ci_noise": 1},
}


def test_counts_render_as_bullets():
    lines = review_churn_report_lines({"review_accounting": ACCOUNTING})
    assert lines == [
        "## 6. Review Churn",
        "",
        "- **Full reviews:** `2`",
        "- **Scoped reviews:** `3`",
        "- **Approval carry-overs:** `1`",
        "- **Pipeline runs:** `4`",
        "- **Pipeline reruns:** `1`",
        "- **Non-defect churn (target merge):** `1`",
        "- **Non-defect churn (bookkeeping):** `2`",
        "- **Non-defect churn (CI noise):** `1`",
        "",
    ]
    assert not any("|" in line for line in lines)


@pytest.mark.parametrize(
    "report_data",
    [
        {},
        {"review_accounting": None},
        {"review_accounting": {"full_reviews": 1}},
        {"review_accounting": {**ACCOUNTING, "non_defect_churn": None}},
    ],
)
def test_a_missing_or_legacy_accounting_is_not_recorded_never_zero(report_data):
    lines = review_churn_report_lines(report_data)
    assert lines == ["## 6. Review Churn", "", "- **Review churn:** not recorded", ""]
    assert not re.search(r"\b0\b", "\n".join(lines))


def test_partial_accounting_is_marked():
    lines = review_churn_report_lines({"review_accounting": {**ACCOUNTING, "partial": True}})
    assert lines[2] == "- **Review churn:** partial: counting began after a resume"
    assert "- **Full reviews:** `2`" in lines


def _report_dict(accounting=None):
    pred = PredictedMetrics(p_success=0.9, entropy=1.0, impact=10.0, cost=2.0, learning_value=1.0, ev=6.7)
    act = ActualMetrics(p_success=1.0, entropy=1.0, impact=10.0, cost=2.0, learning_value=1.0, ev=7.7)
    deltas, ratings, biases = compute_calibration_deltas(pred, act)
    report = {
        "plan_id": "P-1",
        "execution_id": "E-1",
        "intent_branch": "I-1-x/_",
        "agent_id": "claude-agent",
        "model_name": "claude-opus-5-5",
        "timestamp": "2026-11-26T09:00:00.000Z",
        "predicted": pred.to_dict(),
        "actual": act.to_dict(),
        "deltas": deltas.to_dict(),
        "entropy_factors": EntropyFactors().to_dict(),
        "accuracy_ratings": ratings,
        "bias_directions": biases,
    }
    if accounting is not None:
        report["review_accounting"] = accounting
    return report


def test_markdown_report_without_accounting_ends_with_not_recorded():
    """No accounting (a CLI run or a legacy flow) renders "not recorded" as the final section."""
    md = format_markdown_report(_report_dict())
    assert "- **Review churn:** not recorded" in md
    assert md.endswith("not recorded\n")


def test_markdown_report_review_churn_follows_touch_set_accuracy():
    accounting = {
        "full_reviews": 1,
        "scoped_reviews": 2,
        "pipeline_runs": 3,
        "pipeline_reruns": 1,
        "approval_carry_overs": 0,
        "non_defect_churn": {"target_merge": 0, "bookkeeping": 1, "ci_noise": 1},
    }

    md = format_markdown_report(_report_dict(accounting))

    assert md.index("## 5. Touch Set Accuracy") < md.index("## 6. Review Churn")
    assert "- **Scoped reviews:** `2`" in md
    assert "- **Non-defect churn (CI noise):** `1`" in md
    assert "not recorded" not in md


def test_the_calibration_report_imports_no_flow_module():
    """``holon calibrate`` reads the accounting record without loading the flow package (no import cycle)."""
    code = (
        "import sys, sandbox_executor.calibration; "
        "print(any(m.startswith('sandbox_executor.flow') for m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False"
