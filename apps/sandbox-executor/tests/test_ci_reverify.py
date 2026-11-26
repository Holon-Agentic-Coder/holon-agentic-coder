"""Stage 5's verdict on the head it published and the head the approval covered, from fake records only."""

from __future__ import annotations

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.github.ci_reverify import approved_head_of, blocked_error, reverify_final_head
from sandbox_executor.flow.review import approval_coverage
from sandbox_executor.flow.review.approval_coverage import (
    CHANGED,
    SAME_FINGERPRINT,
    SAME_HEAD,
    UNKNOWN_CHANGE,
    approval_covers,
    approved_fingerprint_of,
)
from sandbox_executor.flow.review.approval_fingerprint import approval_record

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


def test_timing_rerun_note_is_copied_and_named_exactly_when_attempted() -> None:
    clause = "after one automatic rerun of the timing gate"
    plain = reverify_final_head(RED_RECORD, APPROVED, FINAL)
    assert plain["timing_rerun"] is None
    assert clause not in plain["report"]

    rerun_note = {"attempted": True, "run_ids": ["42"], "outcome": "failed"}
    retried = reverify_final_head({**RED_RECORD, "timing_rerun": rerun_note}, APPROVED, FINAL)
    assert retried["timing_rerun"] == rerun_note
    assert clause in retried["report"]
    assert "Not ready to merge" in retried["report"]

    green = reverify_final_head({"state": "success", "timing_rerun": {"attempted": True}}, APPROVED, APPROVED)
    assert green["verdict"] == "green"
    assert clause in green["report"]

    undispatched = reverify_final_head({**RED_RECORD, "timing_rerun": {"attempted": False}}, APPROVED, FINAL)
    assert undispatched["report"] == plain["report"]


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


def test_a_green_moved_head_names_the_fingerprint_carry_over() -> None:
    verdict = reverify_final_head({"state": "success"}, APPROVED, FINAL, carried=True)
    assert verdict["verdict"] == "green" and verdict["approval_carried_over"] is True
    assert verdict["report"].endswith("; the head moved after approval, which carried over by PR-own fingerprint.")


def test_a_carry_over_never_changes_a_red_or_unverified_verdict() -> None:
    red = reverify_final_head(RED_RECORD, APPROVED, FINAL, carried=True)
    assert red["verdict"] == "red" and red["report"] == reverify_final_head(RED_RECORD, APPROVED, FINAL)["report"]
    assert "carried" not in red["report"]
    assert f"The approval covered {APPROVED[:7]}" in blocked_error(red) and "no longer stands" in blocked_error(red)
    unverified = reverify_final_head({"state": "pending", "reason": "budget"}, APPROVED, FINAL, carried=True)
    assert unverified["verdict"] == "unverified" and "carried" not in unverified["report"]


def test_a_carry_over_is_not_named_when_the_head_did_not_move() -> None:
    verdict = reverify_final_head({"state": "success"}, APPROVED, APPROVED, carried=True)
    assert verdict["approval_carried_over"] is False
    assert verdict["report"].endswith(".") and "carried" not in verdict["report"]


def _approved_context(**consensus: object) -> FlowContext:
    context = FlowContext(repo_dir="/tmp", execution_branch="I-1-x/P-2-y/E-3-z/_", agent="a", model="m")
    payload = {"consensus": {"approved": True, "target_commit": APPROVED, **consensus}}
    context.stage_results["review"] = StageResult(stage=FlowStage.REVIEW, status=StageStatus.APPROVED, payload=payload)
    return context


def test_approval_covers_the_same_head_without_computing_a_fingerprint(monkeypatch) -> None:
    monkeypatch.setattr(approval_coverage, "approval_fingerprint_of", _refuse)
    coverage = approval_covers(_approved_context(approval_fingerprint="pf1:x"), "/repo", APPROVED[:10])
    assert (coverage.covers, coverage.how) == (True, SAME_HEAD)


def test_approval_covers_the_same_fingerprint_and_flags_a_changed_one(monkeypatch) -> None:
    fingerprints = {FINAL: "pf2:same", "c" * 40: "pf2:other"}
    monkeypatch.setattr(approval_coverage, "approval_fingerprint_of", lambda _r, head, _b: fingerprints.get(head))
    context = _approved_context(approval_fingerprint="pf2:same", base_branch="main")

    assert approval_covers(context, "/repo", FINAL) == (True, SAME_FINGERPRINT, "pf2:same", "pf2:same")
    assert approval_covers(context, "/repo", "c" * 40) == (False, CHANGED, "pf2:same", "pf2:other")
    assert approval_covers(context, "/repo", "d" * 40).how == UNKNOWN_CHANGE  # Unreadable: today's behaviour.


def test_an_older_version_fingerprint_is_unknown_not_changed(monkeypatch) -> None:
    monkeypatch.setattr(approval_coverage, "approval_fingerprint_of", _refuse)
    context = _approved_context(approval_fingerprint="pf1:" + "a" * 40, base_branch="main")
    assert approval_covers(context, "/repo", FINAL) == (False, UNKNOWN_CHANGE, "pf1:" + "a" * 40, None)


def test_a_legacy_approval_is_unknown_and_computes_nothing(monkeypatch) -> None:
    monkeypatch.setattr(approval_coverage, "approval_fingerprint_of", _refuse)
    context = _approved_context()
    assert approved_fingerprint_of(context) is None
    assert approval_covers(context, "/repo", FINAL) == (False, UNKNOWN_CHANGE, None, None)


def test_the_recorded_base_reaches_the_coverage_check_through_the_payload(monkeypatch) -> None:
    bases: list[str | None] = []
    fingerprint_of = lambda _r, _h, base: bases.append(base) or "pf2:x"  # noqa: E731
    monkeypatch.setattr(approval_coverage, "approval_fingerprint_of", fingerprint_of)
    consensus_data = {"target_commit": APPROVED, "approval_fingerprint": "pf2:x", "base_branch": "release/1.2"}
    context = _approved_context(**approval_record(consensus_data))
    context.intent_data["target_branch"] = "main"

    assert approval_covers(context, "/repo", FINAL).how == SAME_FINGERPRINT
    assert bases == ["release/1.2"]


def _refuse(*_args: object) -> None:
    raise AssertionError("no fingerprint should be computed")
