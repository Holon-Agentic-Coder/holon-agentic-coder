"""Resuming the Stage 5 restart a red merged head asked for before a checkpoint, and settling a scoped restart.

A withdrawn consensus or a pending scoped restart (:mod:`.merged_head_routing`) skips Stage 5 again on resume until
Stage 4 approves; a scoped review of the CI fix that does not approve withdraws the consensus then.
"""

from __future__ import annotations

from typing import Any

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.github.ci_failure_detail import short_sha
from sandbox_executor.flow.github.ci_head_checks import FAILED
from sandbox_executor.flow.review.consensus_withdrawal import (
    REJECTED_SCOPED_FIX,
    WithdrawalSeams,
    withdraw_consensus,
    withdrawn_consensus_of,
)
from sandbox_executor.flow.review.merged_head_gate import skipped_record
from sandbox_executor.flow.review.merged_head_routing import Decision


def pending_scoped_restart(context: FlowContext) -> dict[str, Any] | None:
    """The merged-head defect a scoped restart was asked for while Stage 4 has not run again, else None."""
    review = context.stage_results.get(FlowStage.REVIEW.value)
    defect = ((review.payload.get("consensus") if review else None) or {}).get("pipeline_defect")
    return defect if isinstance(defect, dict) else None


def withdraw_after_rejected_restart(
    context: FlowContext, record: dict[str, Any], approved_head: str | None, seams: WithdrawalSeams | None = None
) -> dict[str, Any]:
    """Withdraw the consensus of ``approved_head`` when the scoped review of a CI-defect fix did not approve.

    Nothing restarts: the flow halts on that Stage 4, whose iteration count and open findings are kept for a resume.
    """
    context.log(f"The scoped review of the CI fix on {short_sha(record.get('head_sha'))} did not approve.")
    rejected = {**record, "withdrawal_reason": REJECTED_SCOPED_FIX, "approved_head": approved_head}
    return withdraw_consensus(context, rejected, seams, restart=False, halted=True)


def resumed_restart(context: FlowContext, start: str) -> Decision | None:
    """On resume, the Stage 5 restart a withdrawn consensus or a pending scoped restart asks for, else None."""
    withdrawn, defect = withdrawn_consensus_of(context), pending_scoped_restart(context)
    if context.dry_run or context.skip_push or not (withdrawn or defect):
        return None
    if withdrawn:
        record = {**skipped_record(withdrawn.get("merged_head"), "consensus withdrawn"), "state": FAILED}
        context.log("Calibrate stage: the Stage 4 approval was withdrawn; Stage 4 must approve the new head first.")
        payload = {"review_restart": True, "merged_head_ci": record, "consensus_withdrawal": withdrawn}
    else:
        record = defect.get("merged_head_ci") or skipped_record(defect.get("head_sha"), "CI defect")
        context.log("Calibrate stage: CI found a defect on the merged head; Stage 4 must approve the fix first.")
        payload = {"review_restart": True, "scoped_restart": True, "merged_head_ci": record, "ci_routing": defect}
        payload["approved_head"] = defect.get("approved_head")
    return StageResult(FlowStage.CALIBRATE, StageStatus.SKIPPED, start, start, payload=payload), record
