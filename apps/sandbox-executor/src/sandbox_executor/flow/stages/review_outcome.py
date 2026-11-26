"""Stage 4 review stage outcome construction for consensus approval and dry-run halts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.review.verdict import render_final_verdicts


@dataclass(frozen=True)
class ReviewOutcomeContext:
    """Inputs required to construct the Stage 4 review outcome."""

    start_time: str
    end_time: str
    review_package: dict[str, Any]
    consensus_info: dict[str, Any]
    mergeability: dict[str, Any]
    loop_res: dict[str, Any]
    reviewers: list[dict[str, Any]]
    dry_run_simulation: bool


def build_review_stage_outcome(
    context: FlowContext,
    outcome: ReviewOutcomeContext,
) -> StageResult:
    """Build the final StageResult for Stage 4 when not failed (approval or dry-run halt)."""
    human_instruction = f"holon review approve {context.intent_branch or 'intent'}"
    if outcome.dry_run_simulation:
        halt_reason = "Dry-run review loop finished; consensus not measured and not approved."
        context.log(
            f"[SAFETY HALT] {halt_reason} "
            "Autonomous merge to main is strictly prohibited. "
            f"Pipeline halted for human review. To approve manually, run: '{human_instruction}'"
        )
        result = StageResult(
            stage=FlowStage.REVIEW,
            status=StageStatus.HALTED_FOR_HUMAN,
            start_time=outcome.start_time,
            end_time=outcome.end_time,
            payload={
                "review_package": outcome.review_package,
                "consensus": outcome.consensus_info,
                "halted_for_human": True,
                "human_merge_gate_enforced": True,
                "human_approval_command": human_instruction,
                "mergeability": outcome.mergeability,
                "posting": outcome.loop_res.get("posting"),
                "pr": outcome.loop_res.get("pr"),
                "review_accounting": outcome.loop_res.get("accounting"),
            },
            error=None,
        )
    else:
        verdicts = render_final_verdicts(outcome.reviewers)
        context.log(
            f"Stage 4 approved: Review Loop reached consensus approval (final verdicts: {verdicts}). "
            "The PR is ready for the maintainer to merge; the flow never merges to main."
        )
        result = StageResult(
            stage=FlowStage.REVIEW,
            status=StageStatus.APPROVED,
            start_time=outcome.start_time,
            end_time=outcome.end_time,
            payload={
                "review_package": outcome.review_package,
                "consensus": outcome.consensus_info,
                "approved": True,
                "ready_for_maintainer_merge": True,
                "human_merge_gate_enforced": True,
                "halted_for_human": False,
                "human_approval_command": human_instruction,
                "mergeability": outcome.mergeability,
                "posting": outcome.loop_res.get("posting"),
                "pr": outcome.loop_res.get("pr"),
                "review_accounting": outcome.loop_res.get("accounting"),
            },
            error=None,
        )
    context.stage_results["review"] = result
    return result
