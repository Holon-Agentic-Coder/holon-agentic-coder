"""Stage 5 routes a red merged head by class: noise is rerun once, a defect restarts Stage 4 scoped, a changed diff
withdraws."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy
from sandbox_executor.flow.review.approval_gates import GREEN, DiffGate, pipeline_gate_of, record_diff_gate
from sandbox_executor.flow.review.ci_failure_routing import _reruns_spent
from sandbox_executor.flow.review.consensus_withdrawal import WithdrawalSeams, withdrawn_consensus_of
from sandbox_executor.flow.review.delta_review import delta_state_of
from sandbox_executor.flow.review.merged_head_gate import MergedHeadSeams
from sandbox_executor.flow.review.merged_head_resume import pending_scoped_restart
from sandbox_executor.flow.review.merged_head_routing import route_merged_head_failure
from sandbox_executor.flow.review_restart import stage5_merged_head_gate
from sandbox_executor.ledger import read_merged_rows

BRANCH = "I-1-x/P-1-A/E-1-A/_"
APPROVED, MERGED = "a" * 40, "b" * 40
START = "2026-11-26T00:00:00Z"
TIMING, TEST_STEP = ["CI Timing Gate (fast)"], ["Run tests"]


def _check(conclusion: str) -> dict[str, Any]:
    return {"check_run_id": 1, "name": "unit", "status": "completed", "conclusion": conclusion, "run_id": "11"}


class Fakes:
    """Scripted merged-head CI (one list per poll, the last repeating), reruns, comments and a fake clock."""

    def __init__(self, *polls: list[dict[str, Any]], steps: list[str] = TIMING) -> None:
        self.polls, self.steps = list(polls), steps
        self.reruns: list[str] = []
        self.comments: list[str] = []
        self.now = 0.0

    def probe(self, _sha: str) -> list[dict[str, Any]]:
        return self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]

    def rerun(self, run_id: str) -> bool:
        self.reruns.append(run_id)
        return True

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def detail(self, check: dict[str, Any]) -> dict[str, Any]:
        gate = all(s.startswith("CI Timing Gate") for s in self.steps)
        return {"name": check["name"], "failed_steps": self.steps, "duration_seconds": 400.0, "timing_gate": gate}

    def seams(self) -> MergedHeadSeams:
        return MergedHeadSeams(
            policy=CiWaitPolicy(60.0, clock=lambda: self.now, sleep=self.sleep),
            probe=self.probe,
            rerun_dispatcher=self.rerun,
            registration_timeout=0,
            head_reader=lambda: MERGED,
            bookkeeping_move=lambda _a, _h: False,
            withdrawal=WithdrawalSeams(
                lambda _pr: list(self.comments), lambda _p, body: self.comments.append(body) or True
            ),
            detail_lookup=self.detail,
            slowest_lookup=lambda _check: [("tests/test_slow.py::test_x", 90.0)],
        )


def _ctx(tmp_path: Path, diff_gate: bool = True) -> FlowContext:
    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True, exist_ok=True)
    ctx = FlowContext(repo_dir=str(tmp_path), execution_branch=BRANCH, agent="a", model="m")
    consensus = {"approved": True, "target_commit": APPROVED, "pr_number": 7}
    ctx.stage_results["review"] = StageResult(
        stage=FlowStage.REVIEW, status=StageStatus.APPROVED, payload={"consensus": consensus}
    )
    ctx.review_iterations_done = 4
    delta_state_of(ctx).update(last_critical=False)
    if diff_gate:
        record_diff_gate(ctx, DiffGate(True, MERGED))  # The approval holds on the merged head.
    return ctx


def _red(**extra: Any) -> dict[str, Any]:
    run = {"run_id": "11", "check_run_id": 1, "name": "unit", "conclusion": "failure"}
    return {"state": "failed", "head_sha": MERGED, "runs": [run], "reruns": [], "reason": "", **extra}


def _rows(tmp_path: Path, label: str) -> list[dict[str, Any]]:
    rows = read_merged_rows(str(tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"))
    return [row for row in rows if label in row.get("labels", [])]


def test_noise_is_rerun_once_and_a_green_repoll_calibrates_without_a_withdrawal(tmp_path: Path) -> None:
    ctx, fakes = _ctx(tmp_path), Fakes([_check("success")])
    stopped, record = route_merged_head_failure(ctx, START, _red(), fakes.seams())
    assert stopped is None and record["state"] == "passed"
    assert fakes.reruns == ["11"] and fakes.comments == []
    assert _rows(tmp_path, "consensus-withdrawn") == [] and withdrawn_consensus_of(ctx) is None
    gate = pipeline_gate_of(ctx)
    assert gate is not None and gate.state == GREEN and gate.head_sha == MERGED
    assert [r["ci_failure_routing"]["decision"] for r in _rows(tmp_path, "ci-failure-routing")] == ["rerun"]


def test_noise_still_red_after_its_rerun_restarts_stage4_scoped_with_the_timing_finding(tmp_path: Path) -> None:
    ctx, fakes = _ctx(tmp_path), Fakes([_check("failure")])
    stopped, _ = route_merged_head_failure(ctx, START, _red(), fakes.seams())
    assert stopped is not None and stopped.status == StageStatus.SKIPPED
    assert stopped.payload["review_restart"] is True and stopped.payload["scoped_restart"] is True
    assert fakes.reruns == ["11"] and fakes.comments == []
    (finding,) = ctx.carried_review_findings
    assert finding["id"] == "FIND-CI-000" and "tests/test_slow.py::test_x" in finding["summary"]


def test_a_defect_restarts_stage4_scoped_keeping_the_diff_approval(tmp_path: Path) -> None:
    ctx, fakes = _ctx(tmp_path), Fakes([_check("failure")], steps=TEST_STEP)
    stopped, _ = route_merged_head_failure(ctx, START, _red(), fakes.seams())
    assert stopped is not None and stopped.payload["scoped_restart"] is True
    assert fakes.reruns == [] and fakes.comments == [] and _rows(tmp_path, "consensus-withdrawn") == []
    assert withdrawn_consensus_of(ctx) is None
    assert ctx.review_iterations_done == 0
    (finding,) = ctx.carried_review_findings
    assert finding["id"] == "FIND-CI-000" and "unit" in finding["summary"]
    defect = pending_scoped_restart(ctx)
    assert defect is not None and defect["classification"] == "defect" and defect["head_sha"] == MERGED
    assert defect["approved_head"] == APPROVED and stopped.payload["approved_head"] == APPROVED
    assert ctx.stage_results["review"].payload["consensus"]["approved"] is False
    assert delta_state_of(ctx)["anchor_head"] == MERGED and delta_state_of(ctx)["last_reviewed_head"] == MERGED
    pipeline = pipeline_gate_of(ctx)
    assert pipeline is not None and pipeline.classification == "defect"


def test_a_defect_at_the_restart_cap_withdraws_saying_the_flow_stopped(tmp_path: Path) -> None:
    ctx, fakes = _ctx(tmp_path), Fakes([_check("failure")], steps=TEST_STEP)
    ctx.merged_head_restart_allowed = False
    stopped, _ = route_merged_head_failure(ctx, START, _red(), fakes.seams())
    assert stopped is not None and "scoped_restart" not in stopped.payload
    withdrawal = stopped.payload["consensus_withdrawal"]
    assert withdrawal["review_restarted"] is False and withdrawn_consensus_of(ctx) is not None
    assert len(fakes.comments) == 1 and "restart cap was reached" in fakes.comments[0]
    assert len(_rows(tmp_path, "consensus-withdrawn")) == 1 and pending_scoped_restart(ctx) is None


def test_noise_still_red_at_the_restart_cap_withdraws(tmp_path: Path) -> None:
    ctx, fakes = _ctx(tmp_path), Fakes([_check("failure")])
    ctx.merged_head_restart_allowed = False
    stopped, _ = route_merged_head_failure(ctx, START, _red(), fakes.seams())
    assert stopped is not None and stopped.payload["consensus_withdrawal"]["review_restarted"] is False
    assert fakes.reruns == ["11"] and len(fakes.comments) == 1


def test_a_changed_reviewable_diff_still_withdraws(tmp_path: Path) -> None:
    ctx, fakes = _ctx(tmp_path), Fakes([_check("failure")])
    record = {**_red(), "runs": [], "withdrawal_reason": "PR-own change changed since approval"}
    stopped, _ = route_merged_head_failure(ctx, START, record, fakes.seams())
    assert stopped is not None and "consensus_withdrawal" in stopped.payload
    assert "scoped_restart" not in stopped.payload and fakes.reruns == []
    assert withdrawn_consensus_of(ctx) is not None and len(fakes.comments) == 1


def test_a_diff_gate_that_does_not_hold_withdraws(tmp_path: Path) -> None:
    ctx, fakes = _ctx(tmp_path, diff_gate=False), Fakes([_check("failure")])
    stopped, _ = route_merged_head_failure(ctx, START, _red(), fakes.seams())
    assert stopped is not None and stopped.payload["consensus_withdrawal"]["merged_head"] == MERGED
    assert fakes.reruns == [] and len(_rows(tmp_path, "consensus-withdrawn")) == 1


def test_the_rerun_budget_survives_a_checkpoint_round_trip(tmp_path: Path) -> None:
    ctx, fakes = _ctx(tmp_path), Fakes([_check("success")])
    route_merged_head_failure(ctx, START, _red(), fakes.seams())
    restored = FlowContext.from_dict(ctx.to_dict())
    assert _reruns_spent(restored, MERGED, "11") == 1
    stopped, _ = route_merged_head_failure(restored, START, _red(), Fakes([_check("failure")]).seams())
    assert stopped is not None and stopped.payload["scoped_restart"] is True  # No second rerun on the same head.


def test_a_partial_rerun_dispatch_is_counted_only_after_the_resolve_routing_is_recorded(tmp_path: Path) -> None:
    ctx, fakes = _ctx(tmp_path), Fakes([_check("failure")])
    fakes.rerun = lambda run_id: fakes.reruns.append(run_id) or run_id == "11"  # type: ignore[method-assign]
    second = {"run_id": "12", "check_run_id": 2, "name": "lint", "conclusion": "failure"}
    record = {**_red(), "runs": [*_red()["runs"], second]}
    stopped, retried = route_merged_head_failure(ctx, START, record, fakes.seams())
    assert stopped is not None and stopped.payload["scoped_restart"] is True and retried["state"] == "failed"
    assert fakes.reruns == ["11", "12"]
    (row,) = _rows(tmp_path, "ci-failure-routing")
    assert row["ci_failure_routing"]["decision"] == "resolve" and row["ci_failure_routing"]["reruns"] == 0
    assert _reruns_spent(ctx, MERGED, "11") == 1 and _reruns_spent(ctx, MERGED, "12") == 0


def test_an_unsettled_repoll_after_the_rerun_fails_stage5_closed(tmp_path: Path) -> None:
    ctx, fakes = _ctx(tmp_path), Fakes([{**_check(""), "status": "in_progress"}])
    stopped, record = route_merged_head_failure(ctx, START, _red(), fakes.seams())
    assert stopped is not None and stopped.status == StageStatus.FAILED and record["state"] == "pending"
    assert withdrawn_consensus_of(ctx) is None and pending_scoped_restart(ctx) is None


def test_a_resumed_stage5_restarts_the_pending_scoped_restart(tmp_path: Path) -> None:
    ctx, fakes = _ctx(tmp_path), Fakes([_check("failure")], steps=TEST_STEP)
    route_merged_head_failure(ctx, START, _red(), fakes.seams())
    restored = FlowContext.from_dict(ctx.to_dict())
    stopped, record = stage5_merged_head_gate(restored, fakes.seams())
    assert stopped is not None and stopped.payload["scoped_restart"] is True and record["head_sha"] == MERGED
    assert fakes.reruns == [] and fakes.comments == []
    assert stopped.payload["approved_head"] == APPROVED  # Named for a withdrawal if the resumed fix review rejects.
