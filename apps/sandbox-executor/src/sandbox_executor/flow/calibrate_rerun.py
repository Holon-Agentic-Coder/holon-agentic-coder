"""Stage 5 rerun on a moved pull-request head: detection, bounded rerun and the stale signal.

A calibration report covers exactly one execution head. When the pull request head moves after Stage 5 (a conflict
merge with main, a resolver push, a review-loop re-run), the report no longer names the head that merges, so the two
re-entrant trigger sites (the pipeline's Stage 5 block and the review loop's report-back gate) call into this module:
it detects the move, posts a de-duplicated stale comment on the pull request and reruns Stage 5, bounded by
``MAX_RERUN_ATTEMPTS``. Invariant: a calibration commit is never, by itself, a head move — Stage 5's own report,
reconciliation and promotion commits are discounted, so a rerun can never trigger another rerun (the no-loop
guarantee). Merging stays with the human maintainer; this module only refreshes the report a human reads.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sandbox_executor.calibration import extract_branch_components, parse_evaluated_commit_sha, read_git_file
from sandbox_executor.flow.calibrate_rebuild import is_ancestor, is_calibration_commit
from sandbox_executor.flow.context import FlowContext, StageResult, StageStatus
from sandbox_executor.flow.github.approval_invalidation import (
    CommentLister,
    CommentPoster,
    approval_pr_number,
    gh_comment_lister,
    gh_comment_poster,
)
from sandbox_executor.flow.github.ci_failure_detail import short_sha
from sandbox_executor.flow.github.ci_reverify import rerun_note
from sandbox_executor.flow.host_remote import DEFAULT_REMOTE, HostRemoteError, fetch_base
from sandbox_executor.git import run_git

logger = logging.getLogger(__name__)

MAX_RERUN_ATTEMPTS = 2
STALE_MARKER_PREFIX = "<!-- holon-calibration-stale:"
REFRESHED_MARKER_PREFIX = "<!-- holon-calibration-refreshed:"


@dataclass(frozen=True)
class HeadMoveCheck:
    """Whether the pull-request head moved past the head the calibration report evaluated, and why."""

    moved: bool
    evaluated_sha: str = ""
    current_head: str = ""
    reason: str = ""


@dataclass(frozen=True)
class RerunSeams:
    """Optional collaborators of the staleness check and the stale signal; tests inject fakes, the flow the defaults."""

    head_resolver: Callable[[FlowContext], str] | None = None
    report_reader: Callable[[FlowContext, str], str | None] | None = None
    lister: CommentLister | None = None
    poster: CommentPoster | None = None


def _same_commit(first: str, second: str) -> bool:
    """Whether two SHAs (either may be abbreviated) name the same commit."""
    return bool(first and second) and (first.startswith(second) or second.startswith(first))


def head_move_since_calibration(repo_dir: str, evaluated_sha: str, current_head: str) -> HeadMoveCheck:
    """Whether ``current_head`` moved past ``evaluated_sha``, discounting trailing calibration-only commits.

    An evaluated SHA that is not an ancestor of the head (a force push or rewritten history) is always a move, and
    so is any merge commit or non-calibration commit between the two. A range git cannot walk counts as a move
    (conservative), with the reason naming the gap.
    """
    if not evaluated_sha or not current_head:
        return HeadMoveCheck(False, evaluated_sha, current_head, "the evaluated SHA or the current head is unknown")
    if _same_commit(evaluated_sha, current_head):
        return HeadMoveCheck(False, evaluated_sha, current_head, "the head is the evaluated commit")
    if not is_ancestor(repo_dir, evaluated_sha, current_head):
        return HeadMoveCheck(True, evaluated_sha, current_head, "the evaluated commit is not an ancestor of the head")
    listed = run_git(["rev-list", f"{evaluated_sha}..{current_head}", "--"], cwd=repo_dir, check=False)
    if listed.returncode != 0 or not isinstance(listed.stdout, str):
        return HeadMoveCheck(True, evaluated_sha, current_head, "the commits past the evaluated commit are unreadable")
    foreign = [sha for sha in listed.stdout.split() if not is_calibration_commit(repo_dir, sha)]
    if foreign:
        reason = f"{len(foreign)} non-calibration commit(s) landed after the evaluated commit"
        return HeadMoveCheck(True, evaluated_sha, current_head, reason)
    return HeadMoveCheck(False, evaluated_sha, current_head, "only calibration commits follow the evaluated commit")


def _origin_head(context: FlowContext) -> str:
    """The origin head of the execution branch after a tolerant fetch; the local ref, then '', when origin lacks it."""
    branch, repo_dir = context.execution_branch or "", context.target_worktree
    try:
        fetch_base(repo_dir, branch, DEFAULT_REMOTE)
    except HostRemoteError as exc:
        logger.debug("Could not fetch origin/%s before the staleness check: %s", branch, exc)
    for ref in (f"refs/remotes/{DEFAULT_REMOTE}/{branch}", f"refs/heads/{branch}"):
        res = run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo_dir, check=False)
        if res.returncode == 0 and isinstance(res.stdout, str) and res.stdout.strip():
            return res.stdout.strip()
    return ""


def _report_content(context: FlowContext, report_rel: str) -> str | None:
    """The calibration report at the branch tip (origin first, worktree file as a last resort); None when absent."""
    branch = context.execution_branch or ""
    for ref in (f"refs/remotes/{DEFAULT_REMOTE}/{branch}", f"refs/heads/{branch}"):
        content = read_git_file(ref, report_rel, target_worktree=context.target_worktree)
        if content:
            return content
    path = os.path.join(context.target_worktree, report_rel)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    return None


def report_staleness(context: FlowContext, seams: RerunSeams | None = None) -> HeadMoveCheck:
    """Compare the current pull-request head with the head the plan's calibration report evaluated.

    A missing report, an unparsable evaluated SHA or an unresolvable head is unmoved with the reason naming the gap:
    there is nothing to rerun against, and Stage 5 proper owns first-time calibration.
    """
    seams = seams or RerunSeams()
    branch = context.execution_branch
    if context.dry_run or not branch:
        return HeadMoveCheck(False, reason="dry run or no execution branch")
    plan_id = extract_branch_components(branch).get("plan_id")
    if not plan_id:
        return HeadMoveCheck(False, reason=f"no plan id in '{branch}'")
    current = (seams.head_resolver or _origin_head)(context)
    if not current:
        return HeadMoveCheck(False, reason=f"the head of '{branch}' could not be resolved")
    report_rel = f"plans/{plan_id}_calibration.md"
    content = (seams.report_reader or _report_content)(context, report_rel)
    if content is None:
        return HeadMoveCheck(False, current_head=current, reason=f"no calibration report at {report_rel}")
    evaluated = parse_evaluated_commit_sha(content)
    if not evaluated:
        return HeadMoveCheck(False, current_head=current, reason=f"no evaluated commit SHA in {report_rel}")
    return head_move_since_calibration(context.target_worktree, evaluated, current)


def _safe_staleness(context: FlowContext, seams: RerunSeams | None) -> HeadMoveCheck:
    """``report_staleness`` that never raises: an unexpected error is unmoved, so a trigger site cannot crash."""
    try:
        return report_staleness(context, seams)
    except Exception as exc:
        logger.warning("Calibration staleness check failed: %s", exc)
        return HeadMoveCheck(False, reason="the staleness check failed")


def _stale_marker(check: HeadMoveCheck) -> str:
    return f"{STALE_MARKER_PREFIX}{check.evaluated_sha}:{check.current_head} -->"


def _refreshed_marker(head: str) -> str:
    return f"{REFRESHED_MARKER_PREFIX}{head} -->"


def _post_marked_comment(
    context: FlowContext, pr_number: str | None, body: str, marker: str, seams: RerunSeams
) -> dict[str, Any]:
    """Post ``body`` once per ``marker`` on the PR (the approval's when none is given); never raises."""
    pr_number = pr_number or approval_pr_number(context)
    if context.dry_run or context.skip_push or not pr_number:
        return {"posted": False, "reason": "dry run, push skipped or no pull request"}
    repo_dir = context.target_worktree
    try:
        existing = (seams.lister or gh_comment_lister(repo_dir))(pr_number)
        if existing is None:
            return {"posted": False, "reason": "comments could not be listed"}
        if any(marker in comment for comment in existing):
            return {"posted": False, "reason": "already posted for this marker"}
        posted = (seams.poster or gh_comment_poster(repo_dir))(pr_number, f"{body}\n\n{marker}")
        return {"posted": posted, "reason": "posted" if posted else "the comment could not be posted"}
    except Exception as exc:
        logger.warning("Calibration signal comment failed: %s", exc)
        return {"posted": False, "reason": "comment posting failed"}


def post_stale_signal(
    context: FlowContext, check: HeadMoveCheck, seams: RerunSeams | None = None, pr_number: str | None = None
) -> dict[str, Any]:
    """Post the once-per-(evaluated, head) comment that the report is stale; the payload flag stays authoritative."""
    body = (
        "## ⚠️ Calibration report is stale\n\n"
        f"The calibration report covers `{short_sha(check.evaluated_sha)}` but the pull request head is "
        f"`{short_sha(check.current_head)}` ({check.reason}). Stage 5 must rerun; do not merge on this report."
    )
    return _post_marked_comment(context, pr_number, body, _stale_marker(check), seams or RerunSeams())


def _post_refreshed_signal(
    context: FlowContext, head: str, seams: RerunSeams | None, pr_number: str | None = None
) -> dict[str, Any]:
    """Post the comment that supersedes the stale one after a successful rerun; the stale one is never deleted."""
    body = f"Calibration refreshed: the report now covers head `{short_sha(head)}`."
    return _post_marked_comment(context, pr_number, body, _refreshed_marker(head), seams or RerunSeams())


def rerun_calibration(
    context: FlowContext,
    result: StageResult,
    run_stage: Callable[[FlowContext], StageResult],
    seams: RerunSeams | None = None,
    pr_number: str | None = None,
) -> StageResult:
    """Rerun Stage 5 (at most ``MAX_RERUN_ATTEMPTS`` times) while the pull-request head has moved past the report.

    An unmoved head is a no-op by construction: no stage call, hence no new ledger rows and the unchanged ``result``.
    A moved head posts the stale signal, reruns ``run_stage`` (production: ``run_calibrate_stage``, which syncs to the
    new head, rebuilds the stale /calibrated branch, appends fresh rows, publishes and re-verifies CI) and posts the
    superseding refreshed comment. A head that outran every attempt leaves ``calibration_stale`` set in the payload,
    so the stage is never reported as covering a head it does not; a failed rerun propagates as the stage failure.
    """
    if context.dry_run or result.status is not StageStatus.SUCCESS:
        return result
    reran = 0
    check = _safe_staleness(context, seams)
    while check.moved and reran < MAX_RERUN_ATTEMPTS:
        context.log(
            f"Stage 5 rerun: the head moved after calibration (evaluated {check.evaluated_sha[:12]}, head "
            f"{check.current_head[:12]}; {check.reason}); rerunning (attempt {reran + 1}/{MAX_RERUN_ATTEMPTS})."
        )
        post_stale_signal(context, check, seams, pr_number=pr_number)
        result = run_stage(context)
        reran += 1
        if result.status is not StageStatus.SUCCESS:
            return result
        check = _safe_staleness(context, seams)
    if check.moved:
        stale_reason = f"the pull request head is {check.current_head[:12]} ({check.reason})"
        result.payload = {**result.payload, "calibration_stale": True, "stale_reason": stale_reason}
        context.log(f"Stage 5 rerun: the report is still stale after {reran} attempt(s): {stale_reason}.")
    elif reran:
        head = str(result.payload.get("calibrated_head") or check.evaluated_sha)
        ci_verdict = rerun_note(result.payload.get("ci_verdict") or {}, head)
        result.payload = {**result.payload, "ci_verdict": ci_verdict}
        _post_refreshed_signal(context, head, seams, pr_number=pr_number)
    return result


def _flow_run_stage() -> Callable[[FlowContext], StageResult]:
    # Local import: the stage module reaches review modules, which reach this one through the review loop.
    from sandbox_executor.flow.stages.calibrate import run_calibrate_stage

    return run_calibrate_stage


def review_calibration_freshness(
    context: FlowContext,
    pr_number: str | None,
    run_stage: Callable[[FlowContext], StageResult] | None = None,
    seams: RerunSeams | None = None,
) -> dict[str, Any] | None:
    """The review loop's report-back gate, after the consensus post: post the stale signal and, in-flow, rerun Stage 5.

    The rerun fires only when this flow already calibrated (``auto_calibrate`` with a successful Stage 5 result); a
    standalone review loop posts the signal alone, which still marks the pull request. Never raises, and returns
    None when there is no pull request, no report, or no move.
    """
    try:
        if not pr_number or not context.execution_branch:
            return None
        check = report_staleness(context, seams)
        if not check.moved:
            return None
        record: dict[str, Any] = {
            "moved": True,
            "evaluated_sha": check.evaluated_sha,
            "current_head": check.current_head,
            "reason": check.reason,
            "signal": post_stale_signal(context, check, seams, pr_number=pr_number),
            "rerun": False,
        }
        prior = context.stage_results.get("calibrate")
        if context.auto_calibrate and prior is not None and prior.status is StageStatus.SUCCESS:
            stage = run_stage or _flow_run_stage()
            refreshed = rerun_calibration(context, prior, stage, seams=seams, pr_number=pr_number)
            record["rerun"] = refreshed is not prior
    except Exception as exc:  # the gate informs; it never fails the review loop
        logger.warning("Calibration freshness gate failed: %s", exc)
        return None
    return record
