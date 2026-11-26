"""The calibration report's "Review Churn" section: the flow's review accounting, or "not recorded".

The counts come from ``context.review_state["accounting"]`` (:mod:`sandbox_executor.review_accounting`),
passed by Stage 5 as ``review_accounting``. A ``holon calibrate`` CLI run, a legacy flow or a malformed record has none
and renders "not recorded", never zeros. Bullets, not a pipe table, so Prettier has no column padding to rewrite.
"""

from __future__ import annotations

from typing import Any

from sandbox_executor.review_accounting import (
    NON_DEFECT_CHURN,
    NOT_RECORDED,
    PARTIAL,
    PARTIAL_NOTE,
    accounting_of,
)

COUNTER_LABELS = (
    ("full_reviews", "Full reviews"),
    ("scoped_reviews", "Scoped reviews"),
    ("approval_carry_overs", "Approval carry-overs"),
    ("pipeline_runs", "Pipeline runs"),
    ("pipeline_reruns", "Pipeline reruns"),
)
CHURN_LABELS = (("target_merge", "target merge"), ("bookkeeping", "bookkeeping"), ("ci_noise", "CI noise"))


def review_churn_report_lines(report_data: dict[str, Any]) -> list[str]:
    """Render "## 6. Review Churn" from ``report_data["review_accounting"]``, ending with a blank line."""
    accounting = accounting_of(report_data)
    lines = ["## 6. Review Churn", ""]
    if accounting is None:
        return [*lines, f"- **Review churn:** {NOT_RECORDED}", ""]
    churn = accounting[NON_DEFECT_CHURN]
    partial = [f"- **Review churn:** {PARTIAL_NOTE}"] if accounting.get(PARTIAL) else []
    return [
        *lines,
        *partial,
        *(f"- **{label}:** `{accounting[key]}`" for key, label in COUNTER_LABELS),
        *(f"- **Non-defect churn ({label}):** `{churn[key]}`" for key, label in CHURN_LABELS),
        "",
    ]
