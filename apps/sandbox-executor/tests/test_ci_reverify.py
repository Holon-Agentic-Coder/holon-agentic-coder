"""Stage 5's verdict on the head it published and the head the approval covered, from fake records only."""

from __future__ import annotations

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.github.ci_reverify import (
    approved_head_of,
    blocked_error,
    reverify_final_head,
)

APPROVED = "a" * 40
FINAL = "b" * 40
RED_RECORD = {
    "state": "failed",
    "failed_checks": ["macos-unit"],
    "failed_details": [
        {
            "name": "macos-unit",
            "failed_steps": ["CI Timing Gate (Check <= 5 min)"],
            "duration_seconds": 360.0,
            "timing_gate": True,
        }
    ],
}


def test_red_head_names_each_failing_check_and_is_not_merge_ready() -> None:
    verdict = reverify_final_head(RED_RECORD, APPROVED, FINAL)
    assert verdict["verdict"] == "red"
    assert "macos-unit (CI Timing Gate, 6.0 min)" in verdict["report"]
    assert "Not ready to merge" in verdict["report"]
    assert "ready to merge." not in verdict["report"].replace("Not ready to merge.", "")


def test_head_that_differs_from_the_approved_one_is_reported() -> None:
    verdict = reverify_final_head({"state": "success"}, APPROVED, FINAL)
    assert verdict["verdict"] == "green"
    assert verdict["head_moved"] is True
    assert "head moved after approval" in verdict["report"]


def test_same_head_with_an_abbreviated_approved_sha_has_not_moved() -> None:
    assert reverify_final_head({"state": "success"}, APPROVED[:7], APPROVED)["head_moved"] is False


def test_unverified_is_never_ready_to_merge() -> None:
    verdict = reverify_final_head({"state": "unknown", "reason": "pending"}, APPROVED, FINAL)
    assert verdict["verdict"] == "unverified"
    assert "not verified" in verdict["report"]
    assert "Not merge-ready" in verdict["report"]


def test_skipped_record_is_skipped() -> None:
    assert reverify_final_head({"state": "skipped"}, None, None)["verdict"] == "skipped"


def test_blocked_error_names_the_approved_head() -> None:
    error = blocked_error(reverify_final_head(RED_RECORD, APPROVED, FINAL))
    assert "macos-unit" in error
    assert f"covered {APPROVED[:7]}" in error
    assert "no longer stands" in error


def test_approved_head_comes_from_an_approved_review_only() -> None:
    context = FlowContext(repo_dir="/tmp", execution_branch="I-1-x/P-2-y/E-3-z/_", agent="a", model="m")
    assert approved_head_of(context) is None
    review = StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.HALTED_FOR_HUMAN,
        payload={"consensus": {"approved": True, "target_commit": APPROVED}},
    )
    context.stage_results["review"] = review
    assert approved_head_of(context) == APPROVED
    review.payload["consensus"]["approved"] = False
    assert approved_head_of(context) is None
