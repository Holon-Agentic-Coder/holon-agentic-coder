"""Stage 5 rerun on a moved pull-request head: detection, bounded rerun and the stale signal.

A calibration report covers exactly one execution head. When the pull request's own change moves after Stage 5 (a
resolver push, a review-loop re-run, a conflict merge that touches the PR's files), the report no longer covers the
head that merges, so the two re-entrant trigger sites (the pipeline's Stage 5 block and the review loop's report-back
gate) call into this module: it detects the move (``calibrate_staleness``), reruns Stage 5, bounded by
``MAX_RERUN_ATTEMPTS``, and keeps one stale comment on the pull request through ``calibrate_stale_warning`` only when
the stale report stays. Invariant: a calibration commit, a ledger-only commit and a merge of the target that leaves
the PR-own change unchanged are never, by themselves, a head move, so a rerun can never trigger another rerun (the
no-loop guarantee). Merging stays with the human maintainer; this module only refreshes the report a human reads.
"""

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
    """Optional collaborators of the staleness check and the stale warning; tests inject fakes, the flow the defaults.

    ``comments`` lists comment records (id and body) so the warning can be edited through ``editor``; the legacy
    body-only ``lister`` still deduplicates when no records lister is given.
    """

    head_resolver: Callable[[FlowContext], str] | None = None
    report_reader: Callable[[FlowContext, str], str | None] | None = None
    lister: CommentLister | None = None
    poster: CommentPoster | None = None
    comments: CommentRecordLister | None = None
    editor: CommentEditor | None = None


def _warning_seams(seams: RerunSeams | None) -> WarningSeams:
    """The comment seams of the warning lifecycle: the records lister first, else the legacy body-only lister."""
    seams = seams or RerunSeams()
    if seams.comments is None and seams.lister is not None:
        return WarningSeams.from_lister(seams.lister, poster=seams.poster, editor=seams.editor)
    return WarningSeams(comments=seams.comments, poster=seams.poster, editor=seams.editor)


def report_staleness(context: FlowContext, seams: RerunSeams | None = None) -> HeadMoveCheck:
    """Compare the current pull-request head with the head the plan's calibration report evaluated.

    A missing report, an unparsable evaluated SHA or an unresolvable head is unmoved with the reason naming the gap:
    there is nothing to rerun against, and Stage 5 proper owns first-time calibration.
    """
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
    reader, candidates = seams.report_reader or _report_content, calibration_report_candidates(parent, plan_id)
    found = ((rel, text) for rel in candidates if (text := reader(context, rel)) is not None)
    report_rel, content = next(found, (candidates[0], None))  # nested report first, then flat
    if content is None:
        return HeadMoveCheck(False, current_head=current, reason=f"no calibration report at {report_rel}")
    evaluated = parse_evaluated_commit_sha(content)
    if not evaluated:
        return HeadMoveCheck(False, current_head=current, reason=f"no evaluated commit SHA in {report_rel}")
    target = base_branch_name(context)
    _fetch_tolerantly(context.target_worktree, target)  # a target ref that is behind reads as stale, never as fresh
    return head_move_since_calibration(context.target_worktree, evaluated, current, target)


def _safe_staleness(context: FlowContext, seams: RerunSeams | None) -> HeadMoveCheck:
    """``report_staleness`` that never raises: an unexpected error is unmoved, so a trigger site cannot crash."""
    try:
        return report_staleness(context, seams)
    except Exception as exc:
        logger.warning("Calibration staleness check failed: %s", exc)
        return HeadMoveCheck(False, reason="the staleness check failed")


def post_stale_signal(
    context: FlowContext, check: HeadMoveCheck, seams: RerunSeams | None = None, pr_number: str | None = None
) -> dict[str, Any]:
    """Post, or edit in place, the single warning that the report is stale; the payload flag stays authoritative."""
    stale = StaleRange(check.evaluated_sha, check.current_head, check.reason)
    return warn_stale(context, pr_number, stale, _warning_seams(seams))


@dataclass(frozen=True)
class _Thread:
    """The comment seams and the pull request number one trigger-site call comments with."""

    seams: RerunSeams | None = None
    pr_number: str | None = None


def _warn_if_stale(context: FlowContext, thread: _Thread, check: HeadMoveCheck | None = None) -> dict[str, Any] | None:
    """Post, or edit in place, the stale warning when the report (rechecked unless ``check`` is given) is still stale.

    The one posting rule of the rerun trigger: a warning only for a stale report that stays. None when it covers the
    head, for example a rerun that published a fresh report and then failed on a red final-head CI.
    """
    check = check or _safe_staleness(context, thread.seams)
    return post_stale_signal(context, check, thread.seams, pr_number=thread.pr_number) if check.moved else None


def _settle_rerun(
    context: FlowContext, result: StageResult, check: HeadMoveCheck, reran: int, thread: _Thread
) -> tuple[StageResult, dict[str, Any] | None]:
    """After the rerun loop: flag and warn about a report that is still stale, else clear the warning it replaced."""
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
        resolve_stale(context, thread.pr_number, check.evaluated_sha or head, warning)
        post_refreshed(context, thread.pr_number, head, warning)
    elif check.evaluated_sha and check.current_head:  # current without a rerun: clear any leftover warning
        resolve_stale(context, thread.pr_number, check.evaluated_sha, warning)
    return result, None


def _rerun_while_stale(
    context: FlowContext, result: StageResult, run_stage: Callable[[FlowContext], StageResult], thread: _Thread
) -> tuple[StageResult, dict[str, Any] | None]:
    """``rerun_calibration``, plus the record of the stale warning it posted (None when it posted none)."""
    if context.dry_run or result.status is not StageStatus.SUCCESS:
        failed = result.status is StageStatus.FAILED and not context.dry_run
        return result, _warn_if_stale(context, thread) if failed else None  # a skipped Stage 5 is rerun by its caller
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
            return result, _warn_if_stale(context, thread)
        check = _safe_staleness(context, thread.seams)
    return _settle_rerun(context, result, check, reran, thread)


def rerun_calibration(
    context: FlowContext,
    result: StageResult,
    run_stage: Callable[[FlowContext], StageResult],
    seams: RerunSeams | None = None,
    pr_number: str | None = None,
) -> StageResult:
    """Rerun Stage 5 (at most ``MAX_RERUN_ATTEMPTS`` times) while the pull-request head has moved past the report.

    An unmoved head is a no-op by construction: no stage call, hence no new ledger rows and the unchanged ``result``.
    A moved head reruns ``run_stage`` (production: ``run_calibrate_stage``, which syncs to the new head, rebuilds the
    stale /calibrated branch, appends fresh rows, publishes and re-verifies CI) without warning about the report it is
    replacing, then supersedes any open warning and posts the refreshed comment; a current report supersedes any
    leftover warning. The stale warning is posted only for a stale report that stays: a head that outran every attempt
    (which also leaves ``calibration_stale`` set in the payload, so the stage is never reported as covering a head it
    does not), a rerun that failed without publishing a covering report (the failure propagates as the stage failure),
    and a failed Stage 5 ``result`` whose old report is stale. A skipped ``result`` returns unchanged, unchecked.
    """
    return _rerun_while_stale(context, result, run_stage, _Thread(seams, pr_number))[0]


def _flow_run_stage() -> Callable[[FlowContext], StageResult]:
    # Local import: the stage module reaches review modules, which reach this one through the review loop.
    from sandbox_executor.flow.stages.calibrate import run_calibrate_stage

    return run_calibrate_stage


def _stale_gate_record(
    context: FlowContext, check: HeadMoveCheck, run_stage: Callable[[FlowContext], StageResult] | None, thread: _Thread
) -> dict[str, Any]:
    """What the review gate does about a stale report: rerun Stage 5, defer to the Stage 5 ahead, or warn."""
    prior = context.stage_results.get("calibrate")
    calibrated = prior is not None and prior.status is StageStatus.SUCCESS
    if not context.auto_calibrate or prior is None or (context.skip_calibrate and not calibrated):
        return {"signal": post_stale_signal(context, check, thread.seams, pr_number=thread.pr_number), "rerun": False}
    if not calibrated:  # Stage 5 is still ahead in this run: it replaces the report, or warns when it fails
        return {"signal": {**NOT_POSTED, "reason": "deferred to Stage 5 of this run"}, "rerun": False}
    refreshed, signal = _rerun_while_stale(context, prior, run_stage or _flow_run_stage(), thread)
    record = {"signal": signal or {**NOT_POSTED, "reason": "the Stage 5 rerun replaced the stale report"}}
    record["rerun"] = refreshed is not prior
    if refreshed is not prior:  # a rerun ran: record its outcome, so a failed refresh is never mistaken for one
        context.stage_results["calibrate"] = refreshed  # the checkpoint and later readers name the final head
        extras = {"rerun_error": refreshed.error, "rerun_stale": refreshed.payload.get("calibration_stale")}
        record.update({"rerun_status": refreshed.status.value}, **{k: v for k, v in extras.items() if v})
    return record


def review_calibration_freshness(
    context: FlowContext,
    pr_number: str | None,
    run_stage: Callable[[FlowContext], StageResult] | None = None,
    seams: RerunSeams | None = None,
) -> dict[str, Any] | None:
    """The review loop's report-back gate, after the consensus post: what happens to a stale calibration report.

    Three cases. A flow that already calibrated (``auto_calibrate`` with a successful Stage 5 result) reruns Stage 5
    without warning first; ``signal`` then names a warning only when one was posted after the attempt. A flow whose
    Stage 5 is still ahead (``auto_calibrate``, no ``skip_calibrate``, a Stage 5 result that is not a success, such as
    a review restart) posts nothing and records the deferral. Everything else (a standalone review loop,
    ``--skip-calibrate``, no Stage 5 result) posts the warning once, which still marks the pull request. Never
    raises, and returns None when there is no pull request, no report, or no move.
    """
    try:
        if not pr_number or not context.execution_branch:
            return None
        check = report_staleness(context, seams)
        if not check.moved:
            if check.evaluated_sha:  # the report is current: no stale warning stays open
                resolve_stale(context, pr_number, check.evaluated_sha, _warning_seams(seams))
            return None
        return asdict(check) | _stale_gate_record(context, check, run_stage, _Thread(seams, pr_number))
    except Exception as exc:  # the gate informs; it never fails the review loop
        logger.warning("Calibration freshness gate failed: %s", exc)
        return None
