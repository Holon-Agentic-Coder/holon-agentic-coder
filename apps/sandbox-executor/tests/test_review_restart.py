"""A red merged head withdraws the consensus and restarts Stage 4 at iteration 1, at most three times."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sandbox_executor.flow import FlowContext, FlowStage, PipelineEngine, StageResult, StageStatus
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy
from sandbox_executor.flow.review.approval_gates import DiffGate, record_diff_gate
from sandbox_executor.flow.review.consensus_withdrawal import WithdrawalSeams
from sandbox_executor.flow.review.merged_head_gate import MergedHeadSeams
from sandbox_executor.flow.review_restart import (
    MAX_MERGED_HEAD_REVIEW_RESTARTS,
    approval_stands,
    calibrate_with_review_restart,
    stage5_merged_head_gate,
)
from sandbox_executor.ledger import read_merged_rows

BRANCH = "I-1-x/P-1-A/E-1-A/_"
APPROVED = "a" * 40
MERGED_RED = "b" * 40
MERGED_GREEN = "c" * 40


def _review(status: StageStatus = StageStatus.APPROVED, target_commit: str = APPROVED) -> StageResult:
    consensus = {"approved": status == StageStatus.APPROVED, "target_commit": target_commit, "pr_number": 7}
    return StageResult(
        stage=FlowStage.REVIEW, status=status, payload={"consensus": consensus, "posting": {"dual_posted": True}}
    )


def _restart() -> StageResult:
    payload = {"review_restart": True, "merged_head_ci": {"head_sha": MERGED_RED}}
    return StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SKIPPED, payload=payload)


def _calibrated() -> StageResult:
    return StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS)


class Engine:
    """Scripted stage results per stage, and the (stage, status) of every recorded result."""

    def __init__(self, calibrate: list[StageResult], review: list[StageResult]) -> None:
        self.scripts = {FlowStage.CALIBRATE: calibrate, FlowStage.REVIEW: review}
        self.ran: list[FlowStage] = []
        self.recorded: list[tuple[FlowStage, StageStatus]] = []

    def run_stage(self, stage: FlowStage) -> StageResult:
        self.ran.append(stage)
        script = self.scripts[stage]
        return script.pop(0) if len(script) > 1 else script[0]

    def record(self, stage: FlowStage, result: StageResult) -> None:
        self.recorded.append((stage, result.status))


def _ctx(tmp_path: Path, **kwargs: Any) -> FlowContext:
    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True, exist_ok=True)
    return FlowContext(repo_dir=str(tmp_path), execution_branch=BRANCH, agent="a", model="m", **kwargs)


def test_a_green_stage5_needs_no_restart(tmp_path: Path) -> None:
    engine = Engine([_calibrated()], [_review()])
    result = calibrate_with_review_restart(_ctx(tmp_path), engine.run_stage, engine.record)
    assert result.status == StageStatus.SUCCESS
    assert engine.ran == [FlowStage.CALIBRATE]


def test_a_restart_reruns_stage4_then_stage5(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    engine = Engine([_restart(), _calibrated()], [_review()])
    result = calibrate_with_review_restart(ctx, engine.run_stage, engine.record)
    assert result.status == StageStatus.SUCCESS
    assert engine.ran == [FlowStage.CALIBRATE, FlowStage.REVIEW, FlowStage.CALIBRATE]
    assert engine.recorded == [
        (FlowStage.CALIBRATE, StageStatus.SKIPPED),
        (FlowStage.REVIEW, StageStatus.APPROVED),
        (FlowStage.CALIBRATE, StageStatus.SUCCESS),
    ]
    assert any("restarting Stage 4 at iteration 1 (restart 1 of 3)" in line for line in ctx.logs)


def test_a_restarted_stage4_that_does_not_approve_is_returned(tmp_path: Path) -> None:
    halted = _review(StageStatus.HALTED_FOR_HUMAN)
    engine = Engine([_restart()], [halted])
    result = calibrate_with_review_restart(_ctx(tmp_path), engine.run_stage, engine.record)
    assert result is halted
    assert engine.ran == [FlowStage.CALIBRATE, FlowStage.REVIEW]


def test_an_always_red_merged_head_stops_at_the_cap(tmp_path: Path) -> None:
    engine = Engine([_restart()], [_review()])
    result = calibrate_with_review_restart(_ctx(tmp_path), engine.run_stage, engine.record)
    assert result.stage == FlowStage.CALIBRATE and result.status == StageStatus.FAILED
    assert f"after {MAX_MERGED_HEAD_REVIEW_RESTARTS} review restarts" in str(result.error)
    assert result.payload["review_restart"] is False
    assert engine.ran.count(FlowStage.REVIEW) == MAX_MERGED_HEAD_REVIEW_RESTARTS
    assert engine.recorded[-1] == (FlowStage.CALIBRATE, StageStatus.FAILED)


def test_approval_stands_only_for_an_unwithdrawn_latest_approval(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    first = _review()
    ctx.stage_results["review"] = first
    assert approval_stands(ctx, first)
    ctx.stage_results["review"] = _review(StageStatus.FAILED)
    assert not approval_stands(ctx, first)
    withdrawn = _review()
    withdrawn.payload["consensus"]["withdrawn"] = {"merged_head": MERGED_RED}
    ctx.stage_results["review"] = withdrawn
    assert not approval_stands(ctx, first)


# --- pipeline level --------------------------------------------------------------------------------------------------


class Flow:
    """Fakes for one pipeline run: Stage 4 approvals, merged heads and their CI, comments and publishes."""

    def __init__(
        self, ci: dict[str, str], heads: list[str], reviews: list[str], fingerprints: dict[str, str] | None = None
    ) -> None:
        self.ci, self.heads, self.reviews = ci, heads, reviews
        self.fingerprints = fingerprints or {}  # The PR-own change fingerprint of each commit; none by default.
        self.iterations_at_entry: list[int] = []
        self.published: list[str] = []
        self.comments: list[str] = []
        self.now = 0.0

    def run_review(self, context: FlowContext) -> StageResult:
        self.iterations_at_entry.append(context.review_iterations_done)
        context.review_iterations_done += 2
        result = _review(target_commit=self.reviews.pop(0) if len(self.reviews) > 1 else self.reviews[0])
        fingerprint = self.fingerprints.get(result.payload["consensus"]["target_commit"])
        if fingerprint:
            result.payload["consensus"].update(approval_fingerprint=fingerprint, base_branch="main")
            target = result.payload["consensus"]["target_commit"]
            record_diff_gate(context, DiffGate(True, target, fingerprint, "main"))  # The diff approval, CI aside.
        context.stage_results["review"] = result
        return result

    def seams(self) -> MergedHeadSeams:
        head = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]

        def probe(sha: str) -> list[dict[str, Any]]:
            conclusion = self.ci[sha]
            return [{"check_run_id": 1, "name": "unit", "status": "completed", "conclusion": conclusion, "run_id": "9"}]

        def sleep(seconds: float) -> None:
            self.now += seconds

        return MergedHeadSeams(
            policy=CiWaitPolicy(60.0, clock=lambda: self.now, sleep=sleep),
            probe=probe,
            registration_timeout=0,
            head_reader=lambda: head,
            bookkeeping_move=lambda _a, _h: False,
            withdrawal=WithdrawalSeams(lister=lambda _pr: list(self.comments), poster=self.post),
            detail_lookup=lambda check: {"name": check["name"], "failed_steps": ["Run tests"], "timing_gate": False},
        )

    def post(self, _target: str, body: str) -> bool:
        self.comments.append(body)
        return True

    def run_calibrate(self, context: FlowContext) -> StageResult:
        stopped, record = stage5_merged_head_gate(context, self.seams())
        if stopped is not None:
            return stopped
        self.published.append(record["head_sha"])
        return StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS, payload={"merged_head_ci": record})

    def run(self, ctx: FlowContext, checkpoint: Path, from_stage: str = "review") -> None:
        engine = PipelineEngine(ctx, from_stage=from_stage, checkpoint_path=str(checkpoint))
        with (
            patch("sandbox_executor.flow.pipeline.run_review_stage", side_effect=self.run_review),
            patch("sandbox_executor.flow.pipeline.run_calibrate_stage", side_effect=self.run_calibrate),
            patch("sandbox_executor.flow.pipeline.rerun_calibration", side_effect=lambda _c, result, _s: result),
            patch("sandbox_executor.flow.pipeline.pre_run_guard_error", return_value=None),
            patch("sandbox_executor.flow.pipeline._resume_context_error", return_value=None),
            patch("sandbox_executor.flow.pipeline.validate_stage_ledger"),
            patch(
                "sandbox_executor.flow.review.approval_coverage.approval_fingerprint_of",
                side_effect=lambda _repo, head, _base: self.fingerprints.get(str(head)),
            ),
        ):
            engine.run()


def _ledger_rows(tmp_path: Path) -> list[dict[str, Any]]:
    return read_merged_rows(str(tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"))


def test_pipeline_restarts_stage4_on_a_red_merged_head_and_calibrates_the_green_one(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, auto_calibrate=True)
    ctx.review_iterations_done = 3
    flow = Flow({MERGED_RED: "failure", MERGED_GREEN: "success"}, [MERGED_RED, MERGED_GREEN], [APPROVED, MERGED_RED])
    flow.run(ctx, tmp_path / "checkpoint.json")

    assert flow.iterations_at_entry == [3, 0]  # the restarted Stage 4 starts at iteration 1
    assert flow.published == [MERGED_GREEN]  # nothing calibrated for the red head
    assert len(flow.comments) == 1 and MERGED_RED in flow.comments[0]
    rows = [row for row in _ledger_rows(tmp_path) if "consensus-withdrawn" in row.get("labels", [])]
    assert [(row["approved_head"], row["merged_head"]) for row in rows] == [(APPROVED, MERGED_RED)]
    assert ctx.stage_results["calibrate"].status == StageStatus.SUCCESS
    assert any("review approved, PR ready for maintainer merge" in line for line in ctx.logs)


def test_pipeline_stops_after_the_cap_and_never_reports_the_withdrawn_approval_ready(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, auto_calibrate=True)
    flow = Flow({MERGED_RED: "failure"}, [MERGED_RED], [APPROVED])
    flow.run(ctx, tmp_path / "checkpoint.json")

    assert flow.published == []
    assert len(flow.iterations_at_entry) == 1 + MAX_MERGED_HEAD_REVIEW_RESTARTS
    assert ctx.stage_results["calibrate"].status == StageStatus.FAILED
    assert not any("PR ready for maintainer merge" in line for line in ctx.logs)
    assert len(flow.comments) == 1  # once per merged head
    rows = [row for row in _ledger_rows(tmp_path) if "consensus-withdrawn" in row.get("labels", [])]
    assert [row["review_restarted"] for row in rows] == [True] * MAX_MERGED_HEAD_REVIEW_RESTARTS + [False]
    assert "the review was not restarted and the flow stopped" in rows[-1]["error_context"]


def test_a_resumed_stage5_with_a_withdrawn_consensus_restarts_stage4(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, auto_calibrate=True)
    review = _review()
    review.payload["consensus"].update(approved=False, withdrawn={"merged_head": MERGED_RED, "approved_head": APPROVED})
    ctx.stage_results["review"] = review
    flow = Flow({MERGED_GREEN: "success"}, [MERGED_GREEN], [MERGED_RED])
    flow.run(ctx, tmp_path / "checkpoint.json", from_stage="calibrate")

    assert flow.iterations_at_entry == [0]  # Stage 4 re-ran for the new head
    assert flow.published == [MERGED_GREEN]
    assert flow.comments == [] and _ledger_rows(tmp_path) == []  # no second withdrawal
    assert ctx.stage_results["calibrate"].status == StageStatus.SUCCESS


def test_the_checkpoint_after_a_restart_carries_iteration_zero(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, auto_calibrate=True)
    ctx.review_iterations_done = 5
    flow = Flow({MERGED_RED: "failure"}, [MERGED_RED], [APPROVED])
    checkpoint = tmp_path / "checkpoint.json"
    saved: list[dict[str, Any]] = []
    original = PipelineEngine.save_checkpoint

    def spy(engine: PipelineEngine) -> str:
        path = original(engine)
        saved.append(json.loads(checkpoint.read_text()))
        return path

    with patch.object(PipelineEngine, "save_checkpoint", spy):
        flow.run(ctx, checkpoint)
    after_withdrawal = next(
        snap for snap in saved if (snap.get("context") or snap).get("stage_results", {}).get("calibrate")
    )
    context = after_withdrawal.get("context") or after_withdrawal
    assert context["review_iterations_done"] == 0
    assert context["stage_results"]["review"]["payload"]["consensus"]["withdrawn"]["merged_head"] == MERGED_RED


SAME_CHANGE = "pf2:" + "f" * 40
NEW_CHANGE = "pf2:" + "e" * 40


def _withdrawn_rows(tmp_path: Path) -> list[dict[str, Any]]:
    return [row for row in _ledger_rows(tmp_path) if "consensus-withdrawn" in row.get("labels", [])]


def test_a_carried_approval_with_green_ci_on_the_merged_head_runs_stage4_once(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, auto_calibrate=True)
    fingerprints = {APPROVED: SAME_CHANGE, MERGED_GREEN: SAME_CHANGE}
    flow = Flow({MERGED_GREEN: "success"}, [MERGED_GREEN], [APPROVED], fingerprints)
    flow.run(ctx, tmp_path / "checkpoint.json")

    assert len(flow.iterations_at_entry) == 1  # No reviewer is dispatched again.
    assert flow.published == [MERGED_GREEN]  # After CI on the merged head passed.
    assert flow.comments == [] and _withdrawn_rows(tmp_path) == []
    record = ctx.stage_results["calibrate"].payload["merged_head_ci"]
    assert record["state"] == "passed" and record["approval_carry_over"]["approved_head"] == APPROVED


def test_a_carried_approval_with_a_ci_defect_restarts_stage4_scoped_without_withdrawing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, auto_calibrate=True)
    fingerprints = {APPROVED: SAME_CHANGE, MERGED_RED: SAME_CHANGE, MERGED_GREEN: SAME_CHANGE}
    ci = {MERGED_RED: "failure", MERGED_GREEN: "success"}
    flow = Flow(ci, [MERGED_RED, MERGED_GREEN], [APPROVED, MERGED_RED], fingerprints)
    flow.run(ctx, tmp_path / "checkpoint.json")

    assert len(flow.iterations_at_entry) == 2  # The CI defect restarted Stage 4 once, for the resolver's fix.
    assert flow.published == [MERGED_GREEN]
    assert _withdrawn_rows(tmp_path) == [] and flow.comments == []  # The diff approval stood.
    assert any(f"CI defect (defect) on merged head {MERGED_RED}" in line for line in ctx.logs)
    routed = [row for row in _ledger_rows(tmp_path) if "ci-failure-routing" in row.get("labels", [])]
    assert [row["ci_failure_routing"]["decision"] for row in routed] == ["resolve"]


def test_a_resumed_pending_scoped_restart_reruns_stage4_first(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, auto_calibrate=True)
    review = _review()
    defect = {"head_sha": MERGED_RED, "classification": "defect", "merged_head_ci": {"head_sha": MERGED_RED}}
    review.payload["consensus"].update(approved=False, pipeline_defect=defect)
    ctx.stage_results["review"] = review
    assert not approval_stands(ctx, review)
    flow = Flow({MERGED_GREEN: "success"}, [MERGED_GREEN], [MERGED_RED])
    flow.run(ctx, tmp_path / "checkpoint.json", from_stage="calibrate")

    assert flow.iterations_at_entry == [0]  # Stage 4 ran for the fix before anything was calibrated.
    assert flow.published == [MERGED_GREEN]
    assert flow.comments == [] and _withdrawn_rows(tmp_path) == []


def test_a_rejected_scoped_restart_withdraws_the_consensus(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, skip_push=True)
    ctx.stage_results["review"] = _review()
    record = {
        "state": "failed",
        "head_sha": MERGED_RED,
        "runs": [{"run_id": "9", "name": "unit", "conclusion": "failure"}],
    }
    scoped = StageResult(
        stage=FlowStage.CALIBRATE,
        status=StageStatus.SKIPPED,
        payload={"review_restart": True, "scoped_restart": True, "merged_head_ci": record, "approved_head": APPROVED},
    )
    halted = _review(StageStatus.HALTED_FOR_HUMAN, target_commit=MERGED_GREEN)
    carried = [{"id": "FIND-CI-000", "severity": "important", "summary": "CI failed"}]

    def run_stage(stage: FlowStage) -> StageResult:
        if stage == FlowStage.REVIEW:  # The halted scoped review leaves its count and open findings for a resume.
            ctx.stage_results["review"] = halted
            ctx.review_iterations_done, ctx.carried_review_findings = 2, list(carried)
            return halted
        return scoped

    result = calibrate_with_review_restart(ctx, run_stage, lambda _s, _r: None)
    assert result is halted
    (row,) = _withdrawn_rows(tmp_path)
    assert row["merged_head"] == MERGED_RED and row["approved_head"] == APPROVED
    assert row["review_restarted"] is False and "did not approve" in row["action"]
    assert halted.payload["consensus"]["withdrawn"]["merged_head"] == MERGED_RED
    assert ctx.review_iterations_done == 2 and ctx.carried_review_findings == carried


def test_a_rejected_scoped_restart_comment_names_the_approval_and_the_halt(tmp_path: Path) -> None:
    from sandbox_executor.flow.review.merged_head_resume import withdraw_after_rejected_restart

    ctx = _ctx(tmp_path)
    ctx.stage_results["review"] = _review(StageStatus.HALTED_FOR_HUMAN, target_commit=MERGED_GREEN)
    ctx.review_iterations_done = 3
    record = {"state": "failed", "head_sha": MERGED_RED, "runs": [{"run_id": "9", "name": "unit", "conclusion": "x"}]}
    comments: list[str] = []
    seams = WithdrawalSeams(lister=lambda _pr: [], poster=lambda _pr, body: comments.append(body) or True)

    withdrawn = withdraw_after_rejected_restart(ctx, record, APPROVED, seams)

    assert withdrawn["approved_head"] == APPROVED and withdrawn["review_restarted"] is False
    (body,) = comments
    assert f"approval of `{APPROVED[:7]}`" in body and "scoped review of its fix did not approve" in body
    assert "stopped at Stage 4" in body and "restarts at iteration 1" not in body and "cap was reached" not in body
    assert ctx.review_iterations_done == 3


def test_a_changed_pr_own_change_restarts_stage4_for_a_fresh_review(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, auto_calibrate=True)
    fingerprints = {APPROVED: SAME_CHANGE, MERGED_GREEN: NEW_CHANGE}
    flow = Flow({MERGED_GREEN: "success"}, [MERGED_GREEN], [APPROVED, MERGED_GREEN], fingerprints)
    flow.run(ctx, tmp_path / "checkpoint.json")

    assert len(flow.iterations_at_entry) == 2  # A fresh review of the changed change.
    assert flow.published == [MERGED_GREEN]  # The fresh approval covers the head itself.
    (row,) = _withdrawn_rows(tmp_path)
    assert "changed since approval" in row["error_context"] and row["merged_head"] == MERGED_GREEN
    assert len(flow.comments) == 1 and "changed since approval" in flow.comments[0]
    assert any("Consensus withdrawn: PR-own change changed since approval" in line for line in ctx.logs)
