from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sandbox_executor import cli
from sandbox_executor.flow import StageResult, StageStatus, format_final_head_ci


def _calibrated_head_line(calibrate_result: Any) -> str:
    payload = calibrate_result.payload
    head = payload.get("calibrated_head")
    if payload.get("calibration_stale"):
        stale = payload.get("stale_reason") or "the pull request head moved after calibration"
        covers = f"covers {head[:12]}, " if head else ""
        return f"calibration report is STALE: {covers}{stale} — not ready to merge on this report"
    if head:
        return f"calibration report covers execution head {head[:12]}"
    return "calibration report not refreshed for the current head"


def _report_flow_summary(context: Any, result_dict: dict[str, Any], json_output: bool) -> None:
    result_dict.setdefault("target_worktree", context.target_worktree)
    if json_output:
        print(json.dumps(result_dict, indent=2))
        return

    print("\n================== Holon Flow Summary ==================")
    print(f"  target worktree: {context.target_worktree}")
    stage_order = ["intent", "plan", "execute", "review", "calibrate"]
    for st in stage_order:
        res = context.stage_results.get(st)
        print(f"  {res.summary_line()}" if res else f"  {st}: not run")
        if res and res.status.value == "failed" and res.error:
            print(f"    -> Error: {cli.redact_cli_error(res.error)}")
        if st == "calibrate" and res:
            print(f"    {_calibrated_head_line(res)}")
        final_head_ci = res.payload.get("final_head_ci") if res else None
        if st == "calibrate" and final_head_ci and final_head_ci.get("state") != "skipped":
            print(f"    {format_final_head_ci(final_head_ci)}")
    print("========================================================\n")
    decomposition_lines = cli.format_decomposition_summary(context.stage_results.get("execute"))
    for line in decomposition_lines:
        print(line)
    if decomposition_lines:
        print()
    has_failure = any(res.status == StageStatus.FAILED for res in context.stage_results.values())
    _report_review_outcome(context.stage_results.get("review"), has_failure=has_failure)


def _report_review_outcome(review_res: StageResult | None, *, has_failure: bool = False) -> None:
    if not review_res:
        return
    raw_consensus = review_res.payload.get("consensus")
    consensus = raw_consensus if isinstance(raw_consensus, Mapping) else {}
    is_halt = review_res.status == StageStatus.HALTED_FOR_HUMAN or bool(review_res.payload.get("halted_for_human"))
    is_approved = review_res.status == StageStatus.APPROVED or (is_halt and bool(consensus.get("approved")))
    if is_approved:
        if not has_failure:
            print(
                "[REVIEW APPROVED] Review Loop reached consensus approval; "
                "the PR is ready for the maintainer to merge (the flow never merges to main).\n"
            )
    elif is_halt:
        print("[SAFETY HALT] Pipeline safely halted for human review (autonomous merge prohibited).")
        cmd = review_res.payload.get("human_approval_command")
        if cmd:
            print(f"To approve manually, run: {cmd}\n")
