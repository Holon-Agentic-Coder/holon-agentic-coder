"""A red merged head withdraws the consensus and restarts Stage 4 at iteration 1, at most three times."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sandbox_executor.flow import FlowContext, FlowStage, PipelineEngine, StageResult, StageStatus
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy
from sandbox_executor.flow.review.consensus_withdrawal import WithdrawalSeams
from sandbox_executor.flow.review.merged_head_gate import MergedHeadSeams
from sandbox_executor.flow.review_restart import (
    MAX_MERGED_HEAD_REVIEW_RESTARTS,
    approval_stands,
    calibrate_with_review_restart,
    stage5_merged_head_gate,
)

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

    def __init__(self, ci: dict[str, str], heads: list[str], reviews: list[str]) -> None:
        self.ci, self.heads, self.reviews = ci, heads, reviews
        self.iterations_at_entry: list[int] = []
        self.published: list[str] = []
        self.comments: list[str] = []
        self.now = 0.0

    def run_review(self, context: FlowContext) -> StageResult:
        self.iterations_at_entry.append(context.review_iterations_done)
        context.review_iterations_done += 2
        result = _review(target_commit=self.reviews.pop(0) if len(self.reviews) > 1 else self.reviews[0])
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

    def run(self, ctx: FlowContext, checkpoint: Path) -> None:
        engine = PipelineEngine(ctx, from_stage="review", checkpoint_path=str(checkpoint))
        with (
            patch("sandbox_executor.flow.pipeline.run_review_stage", side_effect=self.run_review),
            patch("sandbox_executor.flow.pipeline.run_calibrate_stage", side_effect=self.run_calibrate),
            patch("sandbox_executor.flow.pipeline.rerun_calibration", side_effect=lambda _c, result, _s: result),
            patch("sandbox_executor.flow.pipeline.pre_run_guard_error", return_value=None),
            patch("sandbox_executor.flow.pipeline._resume_context_error", return_value=None),
            patch("sandbox_executor.flow.pipeline.validate_stage_ledger"),
        ):
            engine.run()


def _ledger_rows(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


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
