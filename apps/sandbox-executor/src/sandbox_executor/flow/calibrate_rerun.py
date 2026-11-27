from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from sandbox_executor.calibration import parse_evaluated_commit_sha
from sandbox_executor.flow.base_files import base_branch_name
from sandbox_executor.flow.calibrate_stale_warning import (
    StaleRange,
    WarningSeams,
    post_refreshed,
    resolve_stale,
    warn_stale,
)
from sandbox_executor.flow.calibrate_staleness import (
    HeadMoveCheck,
    _fetch_tolerantly,
    _origin_head,
    _report_content,
    head_move_since_calibration,
)
from sandbox_executor.flow.context import FlowContext, StageResult, StageStatus
from sandbox_executor.flow.github.approval_invalidation import (
    CommentEditor,
    CommentLister,
    CommentPoster,
    CommentRecordLister,
)
from sandbox_executor.flow.github.ci_reverify import rerun_note
from sandbox_executor.plan_paths import calibration_report_candidates, split_plan_branch

logger = logging.getLogger(__name__)

MAX_RERUN_ATTEMPTS = 2
NOT_POSTED = {"posted": False, "edited": 0}


@dataclass(frozen=True)
class RerunSeams:
    head_resolver: Callable[[FlowContext], str] | None = None
    report_reader: Callable[[FlowContext, str], str | None] | None = None
    lister: CommentLister | None = None
    poster: CommentPoster | None = None
    comments: CommentRecordLister | None = None
    editor: CommentEditor | None = None


def _warning_seams(seams: RerunSeams | None) -> WarningSeams:
    seams = seams or RerunSeams()
    if seams.comments is None and seams.lister is not None:
        return WarningSeams.from_lister(seams.lister, poster=seams.poster, editor=seams.editor)
    return WarningSeams(comments=seams.comments, poster=seams.poster, editor=seams.editor)


def report_staleness(context: FlowContext, seams: RerunSeams | None = None) -> HeadMoveCheck:
    seams = seams or RerunSeams()
    branch = context.execution_branch
    if context.dry_run or not branch:
        return HeadMoveCheck(False, reason="dry run or no execution branch")
    parent, plan_id = split_plan_branch(branch)
    if not plan_id:
        return HeadMoveCheck(False, reason=f"no plan id in '{branch}'")
    current = (seams.head_resolver or _origin_head)(context)
    if not current:
        return HeadMoveCheck(False, reason=f"the head of '{branch}' could not be resolved")
    reader = seams.report_reader or _report_content
    nested_then_flat_reports = calibration_report_candidates(parent, plan_id)
    found = ((rel, text) for rel in nested_then_flat_reports if (text := reader(context, rel)) is not None)
    report_rel, content = next(found, (nested_then_flat_reports[0], None))
    if content is None:
        return HeadMoveCheck(False, current_head=current, reason=f"no calibration report at {report_rel}")
    evaluated = parse_evaluated_commit_sha(content)
    if not evaluated:
        return HeadMoveCheck(False, current_head=current, reason=f"no evaluated commit SHA in {report_rel}")
    target = base_branch_name(context)
    # why: a target ref that is behind reads as stale, never as fresh
    _fetch_tolerantly(context.target_worktree, target)
    return head_move_since_calibration(context.target_worktree, evaluated, current, target)


def _safe_staleness(context: FlowContext, seams: RerunSeams | None) -> HeadMoveCheck:
    try:
        return report_staleness(context, seams)
    except Exception as exc:
        logger.warning("Calibration staleness check failed: %s", exc)
        return HeadMoveCheck(False, reason="the staleness check failed")


def post_stale_signal(
    context: FlowContext, check: HeadMoveCheck, seams: RerunSeams | None = None, pr_number: str | None = None
) -> dict[str, Any]:
    stale = StaleRange(check.evaluated_sha, check.current_head, check.reason)
    return warn_stale(context, pr_number, stale, _warning_seams(seams))


@dataclass(frozen=True)
class _Thread:
    seams: RerunSeams | None = None
    pr_number: str | None = None


def _warn_if_stale(context: FlowContext, thread: _Thread, check: HeadMoveCheck | None = None) -> dict[str, Any] | None:
    check = check or _safe_staleness(context, thread.seams)
    return post_stale_signal(context, check, thread.seams, pr_number=thread.pr_number) if check.moved else None


def _settle_rerun(
    context: FlowContext, result: StageResult, check: HeadMoveCheck, reran: int, thread: _Thread
) -> tuple[StageResult, dict[str, Any] | None]:
    warning = _warning_seams(thread.seams)
    if check.moved:
        stale_reason = f"the pull request head is {check.current_head[:12]} ({check.reason})"
        result.payload = {**result.payload, "calibration_stale": True, "stale_reason": stale_reason}
        context.log(f"Stage 5 rerun: the report is still stale after {reran} attempt(s): {stale_reason}.")
        return result, _warn_if_stale(context, thread, check)
    if reran:
        head = str(result.payload.get("calibrated_head") or check.evaluated_sha)
        ci_verdict = rerun_note(result.payload.get("ci_verdict") or {}, head)
        result.payload = {**result.payload, "ci_verdict": ci_verdict}
        resolved = resolve_stale(context, thread.pr_number, check.evaluated_sha or head, warning)
        superseded = (result.payload.get("stale_warning") or {}).get("edited") or resolved.get("edited")
        # why: a superseded warning already names the fresh report: one comment, not two
        if not superseded:
            post_refreshed(context, thread.pr_number, head, warning)
    elif _found_current_report(check):
        resolve_stale(context, thread.pr_number, check.evaluated_sha, warning)
    return result, None


def _found_current_report(check: HeadMoveCheck) -> bool:
    return bool(check.evaluated_sha and check.current_head)


def _rerun_while_stale(
    context: FlowContext, result: StageResult, run_stage: Callable[[FlowContext], StageResult], thread: _Thread
) -> tuple[StageResult, dict[str, Any] | None]:
    if context.dry_run or result.status is not StageStatus.SUCCESS:
        failed = result.status is StageStatus.FAILED and not context.dry_run
        # why: a skipped Stage 5 is rerun by its caller, so only a failed one warns here
        return result, _warn_if_stale(context, thread) if failed else None
    reran = 0
    check = _safe_staleness(context, thread.seams)
    while check.moved and reran < MAX_RERUN_ATTEMPTS:
        context.log(
            f"Stage 5 rerun: the head moved after calibration (evaluated {check.evaluated_sha[:12]}, head "
            f"{check.current_head[:12]}; {check.reason}); rerunning (attempt {reran + 1}/{MAX_RERUN_ATTEMPTS})."
        )
        result = run_stage(context)
        reran += 1
        if result.status is not StageStatus.SUCCESS:
            failed = result.status is StageStatus.FAILED and not context.dry_run
            return result, _warn_if_stale(context, thread) if failed else None
        check = _safe_staleness(context, thread.seams)
    return _settle_rerun(context, result, check, reran, thread)


def rerun_calibration(
    context: FlowContext,
    result: StageResult,
    run_stage: Callable[[FlowContext], StageResult],
    seams: RerunSeams | None = None,
    pr_number: str | None = None,
) -> StageResult:
    return _rerun_while_stale(context, result, run_stage, _Thread(seams, pr_number))[0]


def _flow_run_stage() -> Callable[[FlowContext], StageResult]:
    # why: local import, because the stage module reaches review modules, which reach this one through the review loop.
    from sandbox_executor.flow.stages.calibrate import run_calibrate_stage

    return run_calibrate_stage


def _stale_gate_record(
    context: FlowContext, check: HeadMoveCheck, run_stage: Callable[[FlowContext], StageResult] | None, thread: _Thread
) -> dict[str, Any]:
    prior = context.stage_results.get("calibrate")
    calibrated = prior is not None and prior.status is StageStatus.SUCCESS
    if not context.auto_calibrate or prior is None or (context.skip_calibrate and not calibrated):
        return {"signal": post_stale_signal(context, check, thread.seams, pr_number=thread.pr_number), "rerun": False}
    stage5_still_ahead = not calibrated
    if stage5_still_ahead:
        # why: Stage 5 of this run replaces the report, or warns when it fails
        return {"signal": {**NOT_POSTED, "reason": "deferred to Stage 5 of this run"}, "rerun": False}
    refreshed, signal = _rerun_while_stale(context, prior, run_stage or _flow_run_stage(), thread)
    record = {"signal": signal or {**NOT_POSTED, "reason": "the Stage 5 rerun replaced the stale report"}}
    rerun_ran = refreshed is not prior
    record["rerun"] = rerun_ran
    if rerun_ran:
        # why: the checkpoint and later readers name the final head, and a failed refresh is never mistaken for one
        context.stage_results["calibrate"] = refreshed
        extras = {"rerun_error": refreshed.error, "rerun_stale": refreshed.payload.get("calibration_stale")}
        record.update({"rerun_status": refreshed.status.value}, **{k: v for k, v in extras.items() if v})
    return record


def review_calibration_freshness(
    context: FlowContext,
    pr_number: str | None,
    run_stage: Callable[[FlowContext], StageResult] | None = None,
    seams: RerunSeams | None = None,
) -> dict[str, Any] | None:
    try:
        if not pr_number or not context.execution_branch:
            return None
        check = report_staleness(context, seams)
        if not check.moved:
            report_is_current = bool(check.evaluated_sha)
            if report_is_current:
                resolve_stale(context, pr_number, check.evaluated_sha, _warning_seams(seams))
            return None
        return asdict(check) | _stale_gate_record(context, check, run_stage, _Thread(seams, pr_number))
    # why: the gate informs; it never fails the review loop
    except Exception as exc:
        logger.warning("Calibration freshness gate failed: %s", exc)
        return None
