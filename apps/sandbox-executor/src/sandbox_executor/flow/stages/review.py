"""Stage 4 (Review Loop): run the review loop on the execution branch and halt at consensus approval,
strictly enforcing the human-only PR merge rule.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from sandbox_executor.budgets import format_minutes
from sandbox_executor.calibration import is_metadata_path
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _stage_failure, _test_evidence
from sandbox_executor.flow.review.loop import _reviewers_objected, _run_engine_review_loop
from sandbox_executor.flow.review.records import render_reviewer_summary_line
from sandbox_executor.flow.stage_conflicts import check_and_resolve_stage_conflicts
from sandbox_executor.git import run_git
from sandbox_executor.knowledge import ConsultEngine
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.review_ensemble import ConsensusVerdict

logger = logging.getLogger(__name__)


REVIEW_GIT_ENV: dict[str, str] = {
    "GIT_AUTHOR_NAME": "Holon Review Loop Agent",
    "GIT_AUTHOR_EMAIL": "review-loop-agent@holon-agentic-coder.com",
    "GIT_COMMITTER_NAME": "Holon Review Loop Agent",
    "GIT_COMMITTER_EMAIL": "review-loop-agent@holon-agentic-coder.com",
}


TESTS_NOT_MEASURED_ERROR = (
    "Autonomous review consensus failed: test results not measured: no recorded Stage 3 test result and no CI checks."
)


def _checkout_execution_branch(context: FlowContext) -> str | None:
    """Check out the execution branch in the host checkout before a stage that writes to the working tree.

    Stages 1-3 only fetch and track the branches their containers push, so the operator's checkout is still
    wherever 'holon flow' started. Stage 4 appends its review ledger rows to the checkout and Stage 5 writes the
    calibration report and reconciles 'executions.jsonl' in it, as 'holon review-loop' and 'holon calibrate' do
    when run on the branch. Both stages call this so that a '--skip-review' run, or a resume entering at Stage 5,
    never writes into the operator's branch. A plain 'git checkout' carries non-conflicting local changes over
    and refuses conflicting ones, so nothing is ever discarded.

    Returns:
        None when HEAD is (now) on the execution branch, otherwise the redacted reason the checkout failed.
    """
    repo_dir = context.target_worktree
    current = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir, check=False)
    current_branch = current.stdout.strip() if current.returncode == 0 and isinstance(current.stdout, str) else ""
    if current_branch == context.execution_branch:
        return None
    co_res = run_git(["checkout", context.execution_branch], cwd=repo_dir, check=False)
    if co_res.returncode == 0:
        return None
    stderr = co_res.stderr.strip() if isinstance(co_res.stderr, str) else ""
    return redact_agent_secrets(stderr) or f"exit code {co_res.returncode}"


def run_review_stage(context: FlowContext) -> StageResult:  # noqa: C901, PLR0912, PLR0915
    """Execute Stage 4: Review Loop.

    Evaluates diffs, test logs, and safety invariants. Enforces the human-only PR merge rule:
    Upon consensus approval, halts unconditionally with status HALTED_FOR_HUMAN and NEVER merges to main.
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
        target_branch = context.intent_data.get("target_branch", "main")
        check_and_resolve_stage_conflicts(
            repo_dir, target_branch=target_branch, stage_name="review", branch=context.execution_branch
        )

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
        if is_git:
            stat_res = run_git(
                ["diff", "--numstat", context.plan_branch, context.execution_branch],
                cwd=repo_dir,
                check=False,
            )
            files_changed = 0
            insertions = 0
            deletions = 0
            for line in stat_res.stdout.splitlines():
                parts = line.strip().split("	")
                if len(parts) >= 3:
                    file_path = parts[2].strip().removeprefix("./")
                    if " => " in file_path:
                        file_path = file_path.split(" => ")[-1].strip().rstrip("}").strip()
                    if is_metadata_path(file_path):
                        continue
                    files_changed += 1
                    if parts[0] != "-":
                        insertions += int(parts[0])
                    if parts[1] != "-":
                        deletions += int(parts[1])
            diff_summary = {
                "files_changed": files_changed,
                "insertions": insertions,
                "deletions": deletions,
            }

    loop_res = _run_engine_review_loop(context)
    # The payload reports the engine's configured budget, not the remaining passes of this run.
    consensus_info = {**loop_res.get("consensus", {}), "max_review_iterations": context.max_review_iterations}

    consensus_reached = bool(consensus_info.get("approved"))
    mergeability = loop_res.get("mergeability", {})
    is_dirty = bool(mergeability.get("is_dirty"))
    merge_status = str(mergeability.get("merge_state_status", "UNKNOWN"))
    mergeable_state = str(mergeability.get("mergeable", "UNKNOWN"))

    proxy_env_active = bool(context.token_reduce)

    review_package = {
        "intent_branch": context.intent_branch,
        "plan_branch": context.plan_branch,
        "execution_branch": context.execution_branch,
        "diff_summary": diff_summary,
        "test_results": {
            "exit_code": exit_code,
            # None = not measured: Stage 3 recorded no test result, so the package never claims a pass.
            "pass_rate": test_pass_rate,
            "measured": test_pass_rate is not None,
            "passed": False if exit_code != 0 else (None if test_pass_rate is None else test_pass_rate == 1.0),
        },
        "mergeability": mergeability,
        "token_reduce": context.token_reduce,
        "proxy_active": proxy_env_active,
        "consensus": consensus_info,
        "ci_checks": loop_res.get("ci_checks"),
        "trailers": loop_res.get("trailers"),
        "consulted_knowledge_ids": review_consulted_ids,
        "posting": loop_res.get("posting"),
        "pr": loop_res.get("pr"),
    }

    end_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    if is_dirty:
        target_branch = context.intent_data.get("target_branch", "main")
        err_msg = (
            f"[CRITICAL: CI SIGNAL SUPPRESSED] Pull Request has merge conflicts against {target_branch} "
            f"(mergeStateStatus={merge_status}, mergeable={mergeable_state}). "
            "GitHub Actions suppresses test workflows on refs/pull/<PR>/merge. "
            "Halting consensus approval."
        )
        context.log(err_msg)
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
            },
            error=err_msg,
        )
        context.stage_results["review"] = result
        return result

    if merge_status == "MISSING_REF":
        target_branch = context.intent_data.get("target_branch", "main")
        unresolved_ref = (mergeability.get("diagnostics") or {}).get("missing_ref") or context.execution_branch
        err_msg = (
            f"Review halted: cannot evaluate mergeability for ref {unresolved_ref} against {target_branch} "
            f"(mergeable={mergeable_state}, mergeStateStatus={merge_status}). "
            "Verify that the branch exists locally or on origin."
        )
        context.log(err_msg)
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
            },
            error=err_msg,
        )
        context.stage_results["review"] = result
        return result

    # A dry run simulates the loop and is never approved; it still halts for a human like a real review would.
    dry_run_simulation = bool(context.dry_run) and consensus_info.get("status") == "not_measured"
    if not consensus_reached and not dry_run_simulation:
        ci_checks = loop_res.get("ci_checks") or {}
        loop_breach = loop_res.get("budget_breach")
        summaries = "; ".join(render_reviewer_summary_line(r) for r in loop_res.get("reviewers") or [])
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
            err_msg = "Autonomous review consensus failed: test suite reported failures."
        context.log(f"Stage 4 failed: {err_msg}")
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
            },
            error=err_msg,
        )
        context.stage_results["review"] = result
        return result

    human_instruction = f"holon review approve {context.intent_branch or 'intent'}"
    halt_reason = (
        "Dry-run review loop finished; consensus not measured and not approved."
        if dry_run_simulation
        else "Review Loop reached consensus approval."
    )
    context.log(
        f"[SAFETY HALT] {halt_reason} "
        "Autonomous merge to main is strictly prohibited. "
        f"Pipeline halted for human review. To approve manually, run: '{human_instruction}'"
    )

    result = StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.HALTED_FOR_HUMAN,
        start_time=start_time,
        end_time=end_time,
        payload={
            "review_package": review_package,
            "consensus": consensus_info,
            "halted_for_human": True,
            "human_merge_gate_enforced": True,
            "human_approval_command": human_instruction,
            "mergeability": mergeability,
            "posting": loop_res.get("posting"),
            "pr": loop_res.get("pr"),
        },
        error=None,
    )
    context.stage_results["review"] = result
    return result
