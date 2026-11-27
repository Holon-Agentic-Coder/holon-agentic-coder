"""Stage 4 (Review Loop): run the review loop on the execution branch and return APPROVED at consensus,
preserving human-only merge boundaries without merging to main.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from sandbox_executor.budgets import format_minutes
from sandbox_executor.calibration import is_metadata_path
from sandbox_executor.flow.calibrate_sync import record_starting_branch
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _stage_failure, _test_evidence
from sandbox_executor.flow.github.adapter import GitHubAdapter
from sandbox_executor.flow.review.ci_outcome import NO_BLOCKER_RECORDED, describe_ci_outcome
from sandbox_executor.flow.review.ledger_commit import _close_review_stage
from sandbox_executor.flow.review.loop import _reviewers_objected, _run_engine_review_loop
from sandbox_executor.flow.review.proxy_env import review_proxy_env
from sandbox_executor.flow.review.records import render_reviewer_summary_line
from sandbox_executor.flow.review.verdict import not_approved_verdicts, render_final_verdicts
from sandbox_executor.flow.stages.review_outcome import ReviewOutcomeContext, build_review_stage_outcome
from sandbox_executor.flow.target_sync_hooks import at_stage_boundary
from sandbox_executor.flow.target_sync_result import SyncBoundary
from sandbox_executor.flow.test_evidence import _test_results_package
from sandbox_executor.git import parse_numstat, run_git
from sandbox_executor.git_revisions import qualify_branch_revision, revision_args
from sandbox_executor.knowledge import ConsultEngine
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.review_accounting import accounting_of, render_accounting_lines
from sandbox_executor.review_ensemble import ConsensusVerdict
from sandbox_executor.worktree_head import ReattachStatus, reattach_branch, restore_branch

logger = logging.getLogger(__name__)


TESTS_NOT_MEASURED_ERROR = (
    "Autonomous review consensus failed: test results not measured: no recorded Stage 3 test result and no CI checks."
)


def _checkout_execution_branch(context: FlowContext) -> str | None:
    """Check out the execution branch in the host checkout before a stage that writes to the working tree.

    Stages 1-3 only fetch and track the branches their containers push, so the operator's checkout is still
    wherever 'holon flow' started. Stage 4 appends its review ledger rows to the checkout and Stage 5 writes the
    calibration report and reconciles 'executions.jsonl' in it, as 'holon review-loop' and 'holon calibrate' do
    when run on the branch. Both stages call this so that a resume entering at Stage 5 never writes into the
    operator's branch. A HEAD detached at the branch tip is re-attached in place; otherwise a plain 'git checkout'
    carries non-conflicting local changes over and refuses conflicting ones, so nothing is ever discarded.

    Returns:
        None when HEAD is (now) on the execution branch, otherwise the redacted reason the checkout failed.
    """
    record_starting_branch(context, run_git)
    repo_dir = context.target_worktree
    if reattach_branch(repo_dir, context.execution_branch, run_git).status is not ReattachStatus.REFUSED:
        return None
    co_res = run_git(["checkout", context.execution_branch], cwd=repo_dir, check=False)
    if co_res.returncode == 0:
        return None
    stderr = co_res.stderr.strip() if isinstance(co_res.stderr, str) else ""
    return redact_agent_secrets(stderr) or f"exit code {co_res.returncode}"


def run_review_stage(context: FlowContext) -> StageResult:
    """Execute Stage 4; every exit, a raise included, commits pending ledger rows and hands the worktree back."""
    on_branch: list[str] = []  # armed by the body after its checkout, so a failed one is not blamed on the loop
    try:
        return _run_review_stage_body(context, on_branch)
    finally:  # Rows still pending are committed on the execution branch before the worktree is handed back.
        _close_review_stage(context, on_branch, restore_branch)


def _run_review_stage_body(context: FlowContext, on_branch: list[str]) -> StageResult:  # noqa: C901, PLR0912, PLR0915
    """Execute Stage 4: Review Loop.

    Evaluates diffs, test logs, and safety invariants. Enforces the human-only PR merge rule:
    Upon consensus approval returns APPROVED: the PR is left ready for the maintainer to merge;
    the flow never merges to main.
    Operates unattended: never prompts for interactive review questions.
    """
    start_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log("Starting Stage 4: Review Loop")

    if not context.execution_branch:
        err = "Cannot run Review stage without an active execution_branch in FlowContext."
        context.log(err)
        return StageResult(stage=FlowStage.REVIEW, status=StageStatus.FAILED, start_time=start_time, error=err)

    repo_dir = context.target_worktree
    if not context.dry_run:
        checkout_err = _checkout_execution_branch(context)
        if checkout_err:
            return _stage_failure(
                context,
                FlowStage.REVIEW,
                start_time,
                f"Review stage failed: could not check out execution branch '{context.execution_branch}' "
                f"before the review loop: {checkout_err}",
            )
        on_branch.append(context.execution_branch)
        if sync_failure := at_stage_boundary(context, FlowStage.REVIEW, SyncBoundary.STAGE_START):
            return sync_failure

    exec_result = context.stage_results.get("execute")

    # Consult review heuristics
    review_consulted_ids: list[str] = []
    try:
        db_path = os.path.join(repo_dir, "holon-knowledge/.index/action_index.db")
        if os.path.exists(db_path):
            consult_engine = ConsultEngine(project_db_path=db_path)
            consult_res = consult_engine.consult_records(
                any_labels=["review", "pr_review", "code_review"],
                top_k=6,
                max_tokens=1200,
            )
            review_consulted_ids = consult_res.consulted_ids
            if review_consulted_ids:
                context.log(f"Consulted {len(review_consulted_ids)} review heuristics: {review_consulted_ids}")
    except Exception as e:
        logger.debug("Non-fatal consult in review stage: %s", e)

    test_pass_rate, exit_code = _test_evidence(exec_result)

    diff_summary = {"files_changed": 0, "insertions": 0, "deletions": 0}
    if not context.dry_run and context.plan_branch:
        is_git = run_git(["rev-parse", "--is-inside-work-tree"], cwd=repo_dir, check=False).returncode == 0
        branches = (context.plan_branch, context.execution_branch or "")
        revs = [qualify_branch_revision(repo_dir, b, run=run_git) for b in branches] if is_git else [None]
        if all(revs):  # an unresolvable branch keeps the zero summary instead of parsing a git error
            stat_res = run_git(["diff", "--numstat", *revision_args(*revs)], cwd=repo_dir, check=False)
            files_changed = 0
            insertions = 0
            deletions = 0
            for ins, dels, file_path in parse_numstat(stat_res.stdout):
                if is_metadata_path(file_path):
                    continue
                files_changed += 1
                insertions += max(ins, 0)
                deletions += max(dels, 0)
            diff_summary = {
                "files_changed": files_changed,
                "insertions": insertions,
                "deletions": deletions,
            }

    # One adapter for the whole stage: every PR, CI, mergeability and posting call of the loop goes through it.
    github = GitHubAdapter(context.target_worktree, review_proxy_env(context))
    loop_res = _run_engine_review_loop(context, github=github)
    for line in render_accounting_lines(accounting_of(loop_res)):
        context.log(f"[Review Loop]{line}")
    # The payload reports the engine's configured budget, not the remaining passes of this run.
    consensus_info = {**loop_res.get("consensus", {}), "max_review_iterations": context.max_review_iterations}

    consensus_reached = bool(consensus_info.get("approved"))
    mergeability = loop_res.get("mergeability", {})
    is_dirty = bool(mergeability.get("is_dirty"))
    merge_status = str(mergeability.get("merge_state_status", "UNKNOWN"))
    mergeable_state = str(mergeability.get("mergeable", "UNKNOWN"))

    # A spawned sidecar or an attached user-owned proxy both route host-side review traffic.
    proxy_env_active = bool(context.token_reduce or getattr(context, "proxy_attach", False))

    review_package = {
        "intent_branch": context.intent_branch,
        "plan_branch": context.plan_branch,
        "execution_branch": context.execution_branch,
        "diff_summary": diff_summary,
        "test_results": _test_results_package(exec_result.payload if exec_result else {}, test_pass_rate, exit_code),
        "mergeability": mergeability,
        "token_reduce": context.token_reduce,
        "proxy_active": proxy_env_active,
        "consensus": consensus_info,
        "ci_checks": loop_res.get("ci_checks"),
        "trailers": loop_res.get("trailers"),
        "consulted_knowledge_ids": review_consulted_ids,
        "posting": loop_res.get("posting"),
        "pr": loop_res.get("pr"),
        "review_accounting": loop_res.get("accounting"),
    }

    end_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    # A dry run simulates the loop and is never approved; it still halts for a human like a real review would.
    dry_run_simulation = bool(context.dry_run) and consensus_info.get("status") == "not_measured"
    reviewers = loop_res.get("reviewers") or []
    # Defence in depth: a consensus whose final verdicts are not all a clean APPROVED never halts as approved.
    not_approved = not_approved_verdicts(reviewers)
    err_msg: str | None = None

    if is_dirty:
        target_branch = context.intent_data.get("target_branch", "main")
        err_msg = (
            f"[CRITICAL: CI SIGNAL SUPPRESSED] Pull Request has merge conflicts against {target_branch} "
            f"(mergeStateStatus={merge_status}, mergeable={mergeable_state}). "
            "GitHub Actions suppresses test workflows on refs/pull/<PR>/merge. "
            "Halting consensus approval."
        )
    elif merge_status == "MISSING_REF":
        target_branch = context.intent_data.get("target_branch", "main")
        unresolved_ref = (mergeability.get("diagnostics") or {}).get("missing_ref") or context.execution_branch
        err_msg = (
            f"Review halted: cannot evaluate mergeability for ref {unresolved_ref} against {target_branch} "
            f"(mergeable={mergeable_state}, mergeStateStatus={merge_status}). "
            "Verify that the branch exists locally or on origin."
        )
    elif (not consensus_reached or not_approved) and not dry_run_simulation:
        ci_checks = loop_res.get("ci_checks") or {}
        loop_breach = loop_res.get("budget_breach")
        summaries = "; ".join(render_reviewer_summary_line(r) for r in reviewers)
        if loop_res.get("error"):
            # A guard refusal (empty_diff / diff_unverified) or an exhausted engine review budget:
            # no tests decided this outcome.
            err_msg = f"Autonomous review halted: {loop_res['error']}"
        elif loop_breach:
            # The per-iteration budget stopped the loop: that, not the test result, ended the review.
            err_msg = (
                f"Autonomous review stopped: iteration {loop_breach.get('iteration')} exceeded the review iteration "
                f"budget of {format_minutes(loop_breach.get('budget_seconds', 0))} min."
            )
        elif ci_checks.get("measured") is False and not context.dry_run:
            err_msg = TESTS_NOT_MEASURED_ERROR
        elif loop_res.get("ensemble_verdict") == str(ConsensusVerdict.FAILED):
            err_msg = f"Autonomous review failed: the reviewer ensemble did not complete ({summaries})."
        elif loop_res.get("no_convergence"):
            err_msg = (
                f"Autonomous review halted after {consensus_info.get('iterations')} iteration(s): "
                f"finding set did not converge across 3 consecutive iterations ({summaries}; rejected: no-convergence)."
            )
        elif _reviewers_objected(loop_res):
            err_msg = (
                f"Autonomous review requested changes: reviewers reported findings or did not approve ({summaries}). "
                "Findings and dispositions are recorded in executions.jsonl; no automatic resolver runs."
            )
        else:
            # The CI failure class, named only when it was routed on the head the reviewers saw.
            routing = (context.review_state.get("ci_routing") or {}).get("last") or {}
            routing = routing if routing.get("head_sha") == consensus_info.get("target_commit") else None
            err_msg = describe_ci_outcome(
                ci_checks, loop_res.get("ci_wait"), bool(loop_res.get("tests_failed")), routing=routing
            )
            if blockers := loop_res.get("consensus_blockers"):
                # why: a red or pending CI outcome stays named next to the checks that withheld consensus.
                withheld = f"consensus withheld by {'; '.join(blockers)} ({summaries})."
                ci_text = "" if err_msg == NO_BLOCKER_RECORDED else f"{err_msg} "
                err_msg = f"{ci_text}Autonomous review blocked: {withheld}"

    if err_msg is not None:
        # Every Stage 4 failure, mergeability halts included, reports the reviewers' final verdicts.
        if reviewers:
            err_msg += f" Final verdicts: {render_final_verdicts(reviewers)}" + (
                f"; not approved: {', '.join(not_approved)}." if not_approved else "."
            )
        context.log(err_msg if is_dirty or merge_status == "MISSING_REF" else f"Stage 4 failed: {err_msg}")
        result = StageResult(
            stage=FlowStage.REVIEW,
            status=StageStatus.FAILED,
            start_time=start_time,
            end_time=end_time,
            payload={
                "review_package": review_package,
                "consensus": consensus_info,
                "halted_for_human": False,
                "human_merge_gate_enforced": False,
                "mergeability": mergeability,
                "posting": loop_res.get("posting"),
                "pr": loop_res.get("pr"),
                "review_accounting": loop_res.get("accounting"),
            },
            error=err_msg,
        )
        context.stage_results["review"] = result
        return result

    return build_review_stage_outcome(
        context,
        ReviewOutcomeContext(
            start_time=start_time,
            end_time=end_time,
            review_package=review_package,
            consensus_info=consensus_info,
            mergeability=mergeability,
            loop_res=loop_res,
            reviewers=reviewers,
            dry_run_simulation=dry_run_simulation,
        ),
    )
