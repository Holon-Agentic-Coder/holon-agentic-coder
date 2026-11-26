"""Tests for the calibration report's "Review Churn" section."""

from __future__ import annotations

import re

import pytest
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
