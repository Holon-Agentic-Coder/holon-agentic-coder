"""Withdrawing a consensus after CI failed on the merged head: append-only ledger row, one comment per head, reset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.github.ci_reverify import approved_head_of
from sandbox_executor.flow.review.approval_gates import (
    DiffGate,
    PipelineGate,
    approved,
    diff_gate_holds,
    diff_gate_of,
    pipeline_gate_of,
    record_diff_gate,
    record_pipeline_gate,
)
from sandbox_executor.flow.review.consensus_withdrawal import (
    CAP_STEP,
    WithdrawalSeams,
    failed_runs_of,
    render_withdrawal,
    withdraw_consensus,
    withdrawal_marker,
    withdrawn_consensus_of,
)
from sandbox_executor.flow.review.loop import _run_engine_review_loop
from sandbox_executor.flow.review.records import record_review_action

BRANCH = "I-1-x/P-1-A/E-1-A/_"
APPROVED = "a" * 40
MERGED = "b" * 40
SECRET = "ghp_" + "y" * 36


def _ledger(tmp_path: Path) -> Path:
    return tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"


def _unscoped_fragment(tmp_path: Path) -> Path:
    """Where the branchless review rows land: ``tmp_path`` is not a git checkout, so no intent root is derived."""
    return tmp_path / "holon-knowledge" / "ledger" / "pending" / "executions" / "_unscoped.jsonl"


def _ctx(tmp_path: Path, **kwargs: Any) -> FlowContext:
    _ledger(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    ctx = FlowContext(repo_dir=str(tmp_path), execution_branch=BRANCH, agent="a", model="m", **kwargs)
    ctx.stage_results["review"] = StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.APPROVED,
        payload={
            "consensus": {"approved": True, "target_commit": APPROVED, "pr_number": 7},
            "posting": {"dual_posted": True},
        },
    )
    ctx.review_iterations_done = 4
    ctx.carried_review_findings = [{"id": "F-1"}]
    return ctx


def _record(reason: str = "") -> dict[str, Any]:
    return {
        "state": "failed",
        "head_sha": MERGED,
        "runs": [
            {"run_id": "11", "check_run_id": 1, "name": "unit", "conclusion": "success"},
            {"run_id": "12", "check_run_id": 2, "name": f"lint {SECRET}", "conclusion": "failure"},
        ],
        "reruns": [],
        "reason": reason,
    }


class Comments:
    def __init__(self) -> None:
        self.bodies: list[str] = []
        self.targets: list[str] = []

    def seams(self) -> WithdrawalSeams:
        return WithdrawalSeams(lister=lambda _pr: list(self.bodies), poster=self.post)

    def post(self, target: str, body: str) -> bool:
        self.targets.append(target)
        self.bodies.append(body)
        return True


def _seed_ledger(ctx: FlowContext, tmp_path: Path) -> bytes:
    approval = {
        "action_id": "ACT-POST-1",
        "execution_id": "REV-1",
        "action": "Consensus approved",
        "category": "code",
        "target": BRANCH,
        "outcome": "success",
        "entry_type": "action",
        "action_origin": "pr_review",
        "created_at": "2026-11-23T00:00:00+00:00",
    }
    assert record_review_action(ctx, str(_ledger(tmp_path)), approval) is not None
    return _unscoped_fragment(tmp_path).read_bytes()


def test_one_row_is_appended_and_earlier_rows_stay_a_byte_prefix(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    before = _seed_ledger(ctx, tmp_path)
    withdrawal = withdraw_consensus(ctx, _record(), Comments().seams())
    after = _unscoped_fragment(tmp_path).read_bytes()
    assert after.startswith(before)
    rows = [json.loads(line) for line in after[len(before) :].decode().splitlines() if line.strip()]
    assert len(rows) == 1
    row = rows[0]
    assert row["action_id"] == withdrawal["action_id"]
    assert row["action_id"].startswith("ACT-WITHDRAW")
    assert row["labels"] == ["review", "consensus-withdrawn", "merged-head-ci"]
    assert row["action_origin"] == "pr_review" and row["outcome"] == "failure"
    assert (row["approved_head"], row["merged_head"]) == (APPROVED, MERGED)
    assert [run["run_id"] for run in row["failed_runs"]] == ["12"]
    assert SECRET not in after.decode()
    assert MERGED in row["error_context"]


def test_the_comment_is_posted_once_per_merged_head(tmp_path: Path) -> None:
    comments = Comments()
    first = withdraw_consensus(_ctx(tmp_path), _record(), comments.seams())
    second = withdraw_consensus(_ctx(tmp_path), _record(), comments.seams())
    assert first["comment_posted"] is True
    assert second["comment_posted"] is False and second["comment"]["reason"] == "already posted for this head"
    assert comments.targets == ["7"]
    body = comments.bodies[0]
    assert withdrawal_marker(MERGED) in body
    assert "iteration 1" in body and APPROVED[:7] in body and MERGED[:7] in body
    assert SECRET not in body


def test_dry_run_posts_nothing_and_an_unlistable_pr_posts_nothing(tmp_path: Path) -> None:
    comments = Comments()
    result = withdraw_consensus(_ctx(tmp_path, dry_run=True), _record(), comments.seams())
    assert result["comment_posted"] is False and comments.bodies == []
    unlistable = WithdrawalSeams(lister=lambda _pr: None, poster=comments.post)
    result = withdraw_consensus(_ctx(tmp_path), _record(), unlistable)
    assert result["comment"]["reason"] == "comments could not be listed" and comments.bodies == []


def test_a_posting_failure_does_not_block_the_restart(tmp_path: Path) -> None:
    def broken(_target: str, _body: str) -> bool:
        raise OSError("gh down")

    ctx = _ctx(tmp_path)
    result = withdraw_consensus(ctx, _record(), WithdrawalSeams(lister=lambda _pr: [], poster=broken))
    assert result["comment_posted"] is False
    assert ctx.review_iterations_done == 0


def test_the_review_state_is_reset_for_a_fresh_stage4(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    assert approved_head_of(ctx) == APPROVED
    withdrawal = withdraw_consensus(ctx, _record(), Comments().seams())
    assert ctx.review_iterations_done == 0
    assert ctx.carried_review_findings == []
    assert approved_head_of(ctx) is None
    assert withdrawn_consensus_of(ctx) == {
        "merged_head": MERGED,
        "approved_head": APPROVED,
        "action_id": withdrawal["action_id"],
        "review_restarted": True,
    }


def test_render_names_both_heads_and_each_failing_run() -> None:
    body = render_withdrawal(None, MERGED, [{"name": "unit", "run_id": "12", "conclusion": "timed_out"}])
    assert "an earlier commit" in body
    assert "- unit (run 12): timed_out" in body
    assert body.endswith(withdrawal_marker(MERGED))
    assert "The review restarts at iteration 1" in body


def test_at_the_restart_cap_the_comment_says_the_flow_stopped() -> None:
    body = render_withdrawal(APPROVED, MERGED, [], CAP_STEP)
    assert "restarts at iteration 1" not in body
    assert "the review was not restarted and the flow stopped" in body


def test_only_failed_runs_are_named_never_pending_or_cancelled_ones() -> None:
    runs = [
        {"run_id": "1", "name": "unit", "conclusion": "failure"},
        {"run_id": "2", "name": "slow", "conclusion": "in_progress"},
        {"run_id": "3", "name": "queued", "conclusion": "queued"},
        {"run_id": "4", "name": "lint", "conclusion": "cancelled"},
        {"run_id": "5", "name": "docs", "conclusion": "success"},
        {"run_id": "6", "name": "e2e", "conclusion": "timed_out"},
    ]
    assert [run["name"] for run in failed_runs_of({"runs": runs})] == ["unit", "e2e"]


def test_stage4_after_a_consensus_withdrawal_starts_at_iteration_one_with_the_full_budget(tmp_path: Path) -> None:
    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True)
    ctx = FlowContext(repo_dir=str(tmp_path), execution_branch=BRANCH, max_review_iterations=6, token_reduce=False)
    ctx.review_iterations_done = 4
    ctx.carried_review_findings = [{"id": "F-1"}]
    consensus = {"approved": True, "target_commit": APPROVED}
    ctx.stage_results["review"] = StageResult(FlowStage.REVIEW, StageStatus.APPROVED, payload={"consensus": consensus})
    record = {"head_sha": MERGED, "runs": [{"run_id": "9", "name": "unit", "conclusion": "failure"}]}
    withdraw_consensus(ctx, record, WithdrawalSeams(lister=lambda _pr: [], poster=lambda _t, _b: True))

    loop = MagicMock(return_value={"consensus": {"approved": True}, "total_iterations": 1})
    with patch("sandbox_executor.flow.review.loop.run_review_loop", loop):
        _run_engine_review_loop(ctx)

    assert loop.call_args.kwargs["first_iteration"] == 1
    assert loop.call_args.kwargs["max_iterations"] == 6
    assert ctx.review_iterations_done == 1


def test_a_merged_head_failure_marks_only_the_pipeline_gate_red(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    record_diff_gate(ctx, DiffGate(True, APPROVED, "pf2:x", "main"))
    record_pipeline_gate(ctx, PipelineGate(APPROVED, "green"))
    withdraw_consensus(ctx, _record(), Comments().seams())

    assert ctx.stage_results["review"].payload["consensus"]["approved"] is False
    assert pipeline_gate_of(ctx) == PipelineGate(MERGED, "red", ("12",), source="merged_head")
    assert diff_gate_of(ctx) == DiffGate(True, APPROVED, "pf2:x", "main")
    with patch("sandbox_executor.flow.review.approval_gates.approval_fingerprint_of", return_value="pf2:x"):
        assert diff_gate_holds(ctx, str(tmp_path), MERGED) is True  # Same PR-own change on the merged head.
        assert approved(ctx, str(tmp_path), MERGED) is False


def test_a_changed_pr_withdrawal_leaves_the_pipeline_gate(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    record_pipeline_gate(ctx, PipelineGate(APPROVED, "green"))
    withdraw_consensus(ctx, {**_record(), "withdrawal_reason": "PR-own change changed"}, Comments().seams())
    assert pipeline_gate_of(ctx) == PipelineGate(APPROVED, "green")
