"""Stage 4 approval gates: the diff gate and the pipeline gate are independent, and only their conjunction approves."""

from __future__ import annotations

from pathlib import Path

import pytest
from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.review import approval_gates
from sandbox_executor.flow.review.approval_coverage import CHANGED, SAME_FINGERPRINT, ApprovalCoverage
from sandbox_executor.flow.review.approval_gates import (
    GREEN,
    PENDING,
    RED,
    UNKNOWN,
    DiffGate,
    IterationGates,
    PipelineGate,
    approved,
    diff_gate_holds,
    diff_gate_of,
    gates_summary,
    mark_pipeline_red,
    pipeline_gate_holds,
    pipeline_gate_of,
    pipeline_state_of,
    record_diff_gate,
    record_iteration_gates,
    record_pipeline_gate,
)
from sandbox_executor.review_ensemble import ConsensusVerdict, ReviewerVerdict

from tests.review_fakes import clean_reviewer, objecting_reviewer

HEAD = "a" * 40
OTHER = "b" * 40
SAME_PREFIX = "a" * 7 + "c" * 33
REPO = "/repo"


def _ctx(tmp_path: Path) -> FlowContext:
    return FlowContext(repo_dir=str(tmp_path), agent="a", model="m", token_reduce=False)


@pytest.fixture(autouse=True)
def _no_git(monkeypatch):
    """No coverage and no fingerprint unless a test says otherwise: these tests never reach git."""
    monkeypatch.setattr(approval_gates, "approval_covers", lambda *_a: ApprovalCoverage(False, CHANGED))
    monkeypatch.setattr(approval_gates, "approval_fingerprint_of", lambda *_a: None)


@pytest.mark.parametrize("diff_ok", [True, False])
@pytest.mark.parametrize("state", [GREEN, RED, PENDING, UNKNOWN])
def test_approval_is_the_conjunction_of_both_gates(tmp_path, diff_ok, state):
    ctx = _ctx(tmp_path)
    record_diff_gate(ctx, DiffGate(diff_ok, HEAD))
    record_pipeline_gate(ctx, PipelineGate(HEAD, state))

    assert diff_gate_holds(ctx, REPO, HEAD) is diff_ok
    assert pipeline_gate_holds(ctx, HEAD) is (state == GREEN)
    assert approved(ctx, REPO, HEAD) is (diff_ok and state == GREEN)


def test_recording_one_gate_never_changes_the_other(tmp_path):
    ctx = _ctx(tmp_path)
    record_diff_gate(ctx, DiffGate(True, HEAD, "pf2:x"))
    record_pipeline_gate(ctx, PipelineGate(HEAD, RED))
    assert diff_gate_of(ctx) == DiffGate(True, HEAD, "pf2:x")

    record_diff_gate(ctx, DiffGate(False, OTHER))
    assert pipeline_gate_of(ctx) == PipelineGate(HEAD, RED)


@pytest.mark.parametrize("head", [OTHER, SAME_PREFIX, HEAD[:7], "", None])
def test_a_green_pipeline_on_another_head_never_holds(tmp_path, head):
    ctx = _ctx(tmp_path)
    record_pipeline_gate(ctx, PipelineGate(HEAD, GREEN))
    assert pipeline_gate_holds(ctx, head) is False


def test_the_diff_gate_holds_on_a_covered_head(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    record_diff_gate(ctx, DiffGate(True, HEAD))
    monkeypatch.setattr(approval_gates, "approval_covers", lambda *_a: ApprovalCoverage(True, SAME_FINGERPRINT))
    assert diff_gate_holds(ctx, REPO, OTHER) is True


def test_the_diff_gate_follows_its_own_fingerprint_on_a_moved_head(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    record_diff_gate(ctx, DiffGate(True, HEAD, "pf2:same", "main"))
    seen: list[tuple] = []

    def fingerprint(repo_dir, head, base):
        seen.append((repo_dir, head, base))
        return "pf2:same" if head == OTHER else "pf2:changed"

    monkeypatch.setattr(approval_gates, "approval_fingerprint_of", fingerprint)
    assert diff_gate_holds(ctx, REPO, OTHER) is True
    assert diff_gate_holds(ctx, REPO, SAME_PREFIX) is False
    assert seen[0] == (REPO, OTHER, "main")


def test_an_unapproved_diff_gate_never_holds(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    record_diff_gate(ctx, DiffGate(False, HEAD, "pf2:same"))
    monkeypatch.setattr(approval_gates, "approval_covers", lambda *_a: ApprovalCoverage(True, SAME_FINGERPRINT))
    assert diff_gate_holds(ctx, REPO, HEAD) is False


@pytest.mark.parametrize(
    ("ci_green", "ci_status", "state"),
    [
        (True, {}, GREEN),
        (False, {"pending": 2}, RED),
        (None, {"pending": 1}, PENDING),
        (None, {}, UNKNOWN),
        (None, {"all_passed": True}, GREEN),
        (None, {"all_passed": True, "pending": 1}, PENDING),
        (None, {"all_passed": False}, UNKNOWN),
    ],
)
def test_pipeline_state_of(ci_green, ci_status, state):
    assert pipeline_state_of(ci_green, ci_status) == state


@pytest.mark.parametrize("gates", [None, "x", {"diff": "x", "pipeline": 3}, {"diff": {}, "pipeline": {"state": GREEN}}])
def test_missing_or_malformed_records_read_as_absent(tmp_path, gates):
    ctx = _ctx(tmp_path)
    ctx.review_state = {"gates": gates} if gates is not None else {}
    assert diff_gate_of(ctx) is None and pipeline_gate_of(ctx) is None
    assert approved(ctx, REPO, HEAD) is False
    assert gates_summary(ctx) == {"diff": None, "pipeline": None}


def test_from_dict_tolerates_missing_keys_and_rejects_an_unknown_state():
    assert PipelineGate.from_dict({"head_sha": HEAD, "state": "purple"}).state == UNKNOWN
    assert DiffGate.from_dict({"reviewed_head": HEAD, "approved": "yes"}) == DiffGate(False, HEAD)


def test_flow_context_round_trip_keeps_the_gates(tmp_path):
    ctx = _ctx(tmp_path)
    record_diff_gate(ctx, DiffGate(True, HEAD, "pf2:x", "main", 3, verdicts=("APPROVED",)))
    record_pipeline_gate(ctx, PipelineGate(HEAD, GREEN, ("11",)))
    restored = FlowContext.from_dict(ctx.to_dict())

    assert restored.review_state == ctx.review_state
    assert diff_gate_of(restored) == diff_gate_of(ctx)
    assert approved(restored, REPO, HEAD) is True
    assert FlowContext.from_dict({k: v for k, v in ctx.to_dict().items() if k != "review_state"}).review_state == {}


def test_mark_pipeline_red_keeps_the_diff_gate(tmp_path):
    ctx = _ctx(tmp_path)
    record_diff_gate(ctx, DiffGate(True, HEAD))
    record_pipeline_gate(ctx, PipelineGate(HEAD, GREEN))
    mark_pipeline_red(ctx, OTHER, [{"run_id": "12", "conclusion": "failure"}, {"run_id": None}])

    assert pipeline_gate_of(ctx) == PipelineGate(OTHER, RED, ("12",), source="merged_head")
    assert diff_gate_holds(ctx, REPO, HEAD) is True
    assert approved(ctx, REPO, OTHER) is False


def _facts(reviewers, ci_green, ci_status=None, blockers=()):
    return IterationGates(
        HEAD, 2, reviewers, len(reviewers), True, list(blockers), ci_green, ci_status or {}, "main", lambda h: "pf2:f"
    )


@pytest.mark.parametrize("verdict", [ReviewerVerdict.CHANGES_REQUESTED, ReviewerVerdict.APPROVED])
def test_an_open_critical_finding_never_approves_the_diff(tmp_path, verdict):
    ctx = _ctx(tmp_path)
    critical = objecting_reviewer(2, "critical", verdict)
    record_iteration_gates(ctx, _facts([clean_reviewer(1), critical], True), ConsensusVerdict.CHANGES_REQUESTED)

    assert diff_gate_of(ctx).approved is False and diff_gate_of(ctx).fingerprint is None
    assert pipeline_gate_of(ctx).state == GREEN
    assert approved(ctx, REPO, HEAD) is False


def test_a_blocker_never_approves_the_diff(tmp_path):
    ctx = _ctx(tmp_path)
    record_iteration_gates(ctx, _facts([clean_reviewer(1)], True, blockers=["carried"]), None)
    assert diff_gate_of(ctx).approved is False


def test_clean_reviewers_with_red_ci_approve_the_diff_only(tmp_path):
    ctx = _ctx(tmp_path)
    link = "https://github.com/o/r/actions/runs/77/job/1"
    ci_status = {"checks": [{"bucket": "fail", "link": link}, {"bucket": "pass", "link": "x"}]}
    record_iteration_gates(ctx, _facts([clean_reviewer(1)], False, ci_status), ConsensusVerdict.CHANGES_REQUESTED)

    assert diff_gate_of(ctx) == DiffGate(True, HEAD, "pf2:f", "main", 2, "full", ("APPROVED",))
    assert pipeline_gate_of(ctx) == PipelineGate(HEAD, RED, ("77",))
    assert approved(ctx, REPO, HEAD) is False


def test_drift_between_the_gates_and_the_verdict_is_logged(tmp_path):
    ctx = _ctx(tmp_path)
    record_iteration_gates(ctx, _facts([clean_reviewer(1)], True), ConsensusVerdict.CHANGES_REQUESTED)
    assert any("Gate drift at iteration 2" in line for line in ctx.logs)


def test_a_scoped_re_review_records_a_scoped_diff_gate(tmp_path):
    ctx = _ctx(tmp_path)
    facts = IterationGates(HEAD, 3, [clean_reviewer(1)], 1, True, [], True, {}, "main", None, mode="scoped")
    record_iteration_gates(ctx, facts, ConsensusVerdict.APPROVED)
    assert diff_gate_of(ctx).mode == "scoped"


def test_a_red_state_without_a_failed_check_records_no_run_ids(tmp_path):
    ctx = _ctx(tmp_path)
    ci_status = {"checks": [{"bucket": "pass", "link": "https://github.com/o/r/actions/runs/7/job/1"}]}
    record_iteration_gates(ctx, _facts([clean_reviewer(1)], False, ci_status), ConsensusVerdict.CHANGES_REQUESTED)
    assert pipeline_gate_of(ctx).run_ids == ()


def test_an_objecting_reviewer_still_records_a_green_pipeline(tmp_path):
    ctx = _ctx(tmp_path)
    critical = objecting_reviewer(2, "critical", ReviewerVerdict.CHANGES_REQUESTED)
    facts = _facts([clean_reviewer(1), critical], None, {"all_passed": True, "pending": 0})
    record_iteration_gates(ctx, facts, ConsensusVerdict.CHANGES_REQUESTED)
    assert pipeline_gate_of(ctx).state == GREEN
