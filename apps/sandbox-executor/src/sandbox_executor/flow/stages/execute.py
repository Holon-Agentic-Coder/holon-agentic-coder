"""Stage 3 (Plan Execution): dispatch the executor, adopt its execution branch and resume failed partial
executions.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.decomposition_rule import DECISION_DECOMPOSED, DECISION_OVER_BUDGET, InvalidPlanDecisionError
from sandbox_executor.entrypoint.executor import OVER_BUDGET_EXIT_CODE, _sanitize_string
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _dispatch_role, _stage_failure, recorded_test_pass_rate
from sandbox_executor.flow.git_refs import (
    _append_superseding_execution_row,
    _discover_child_branch,
    _fetch_branch_prefix_error,
    _latest_execution_summary,
    _read_ledger_rows,
    _resolve_branch_ref,
    _snapshot_child_branches,
    _track_local_branch,
)
from sandbox_executor.flow.host_remote import fetch_base, publish_new_child_branches, remote_error
from sandbox_executor.flow.stage_conflicts import check_and_resolve_stage_conflicts
from sandbox_executor.flow.stages.decomposition import _run_decomposed_plan, _sub_intent_depth_error
from sandbox_executor.flow.stages.plan_decision import _over_budget_failure, _plan_budget_decision
from sandbox_executor.flow_guard import SKIP_PUSH_DISPATCH_ERROR
from sandbox_executor.git import EMPTY_DIFF_FAILURE_REASON, EMPTY_DIFF_STATUS, changed_paths, empty_diff_summary
from sandbox_executor.knowledge import ActionIndexDatabase, CredentialRedactor, compute_context_fingerprint
from sandbox_executor.ledger import build_execution_id
from sandbox_executor.redaction import redact_agent_secrets

logger = logging.getLogger(__name__)


# The executor entrypoint has historically written "failure"; guard rows use "failed". Both mean failure.
FAILED_EXECUTION_STATUSES: frozenset[str] = frozenset({"failed", "failure"})


def failed_partial_head(repo_dir: str, ref: str, execution_id: str) -> str | None:
    """The ``partial_head`` on ``ref``'s latest summary row for ``execution_id`` when that row is a failure.

    Both failure statuses count: ``failure`` (a budget breach, an agent error or any other failure) and
    ``EMPTY_DIFF_STATUS`` (an empty or unverified diff), since either row can carry salvaged partial work.
    """
    row = _latest_execution_summary(_read_ledger_rows(repo_dir, ref, "executions.jsonl"), execution_id) or {}
    partial_head = row.get("partial_head")
    if row.get("status") in ("failure", EMPTY_DIFF_STATUS) and isinstance(partial_head, str) and partial_head:
        return partial_head
    return None


def _resumable_partial_branch(repo_dir: str, branches: set[str]) -> str | None:
    """Newest failed execution branch whose latest summary row carries a ``partial_head`` to resume from.

    ``branches`` is the pre-dispatch snapshot of execution branches under the plan; each is read at
    ``refs/remotes/origin/<branch>``. The executor re-reads and verifies the partial head itself.
    """

    def seq(branch: str) -> int:
        match = re.search(r"/E-(\d+)-[^/]+/_$", branch)
        return int(match.group(1)) if match else -1

    for branch in sorted(branches, key=seq, reverse=True):
        exec_id = branch.removesuffix("/_").rsplit("/", 1)[-1]
        if failed_partial_head(repo_dir, f"refs/remotes/origin/{branch}", exec_id):
            return branch
    return None


def run_execute_stage(context: FlowContext, resume_partial: bool = False) -> StageResult:  # noqa: C901, PLR0911, PLR0912, PLR0915
    """Execute Stage 3: dispatch the executor agent, adopt its execution branch and apply the empty-diff guard.

    The executor container (the same one 'holon execute' launches) implements the plan, appends
    executions.jsonl and pushes. An execution whose diff against the plan branch is empty outside
    holon-knowledge/ledger/, plans/ and executions/ fails: a superseding 'failed' row is recorded if the container
    claimed success, and the stage fails so no PR is created or updated.

    With ``resume_partial`` (a Stage 3 resume), the newest failed execution branch under the plan whose latest summary
    row carries a ``partial_head`` is passed to the executor as a fourth argument, so it continues that partial work.

    The stage never halts for a human. It executes only the decision the plan row records (``stages.plan_decision``):

    - *execute*: the executor implements the plan as described above;
    - *decomposed*: each item of the plan row's sub-intent list (never the executor's row, which only refers to it, and
      a disagreement is an error) runs through the full pipeline as a child intent; all children must succeed;
    - *over budget* (above the budget, no list): fails with ``payload["over_budget"]``; the engine re-plans (bounded).
    """
    start_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log("Starting Stage 3: Plan Execution")

    if not context.plan_branch:
        err = "Cannot run Execute stage without an active plan_branch in FlowContext."
        context.log(err)
        return StageResult(stage=FlowStage.EXECUTE, status=StageStatus.FAILED, start_time=start_time, error=err)

    plan_prefix = context.plan_branch.removesuffix("/_").rstrip("/")
    repo_dir = context.target_worktree

    # Deterministic failure veto check
    exec_fp = compute_context_fingerprint(
        target=context.plan_branch,
        tool=context.agent,
        command_prefix="pytest",
    )
    db_path = os.path.join(repo_dir, "holon-knowledge/.index/action_index.db")
    try:
        if os.path.exists(db_path):
            index_db = ActionIndexDatabase(db_path=db_path)
            conn = index_db.get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT is_shadow, state FROM active_vetoes WHERE context_fingerprint = ? AND state != 'lifted';",
                (exec_fp,),
            )
            veto_match = cur.fetchone()
            conn.close()
            if veto_match:
                is_shadow = bool(veto_match["is_shadow"])
                if is_shadow:
                    context.log(f"[VETO SHADOW WARNING] Known failure pattern matches context {exec_fp}")
                else:
                    err = f"Execution blocked by deterministic failure veto for context fingerprint: {exec_fp}"
                    context.log(f"[VETO BLOCKED] {err}")
                    return StageResult(
                        stage=FlowStage.EXECUTE,
                        status=StageStatus.FAILED,
                        start_time=start_time,
                        error=err,
                    )
    except Exception as e:
        logger.debug("Non-fatal veto check exception: %s", e)

    if context.dry_run:
        # Validation only: name the branch the executor would create, without dispatching or writing anything.
        exec_id = build_execution_id(int(time.time()), plan_prefix, context.agent, context.model)
        exec_branch = f"{plan_prefix}/{exec_id}/_"
        context.execution_branch = exec_branch
        context.log(f"Stage 3 (dry run): executor dispatch skipped; execution branch would be '{exec_branch}'.")
        result = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            start_time=start_time,
            end_time=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            payload={
                "execution_id": exec_id,
                "execution_branch": exec_branch,
                "token_reduce": context.token_reduce,
                "dry_run": True,
            },
        )
        context.stage_results["execute"] = result
        return result

    if context.skip_push:
        return _stage_failure(context, FlowStage.EXECUTE, start_time, SKIP_PUSH_DISPATCH_ERROR)

    target_branch = context.intent_data.get("target_branch", "main")
    check_and_resolve_stage_conflicts(
        repo_dir, target_branch=target_branch, stage_name="execute", branch=context.plan_branch
    )

    try:
        precheck = _plan_budget_decision(context)
    except InvalidPlanDecisionError as exc:
        return _stage_failure(context, FlowStage.EXECUTE, start_time, str(exc), dispatched=False)
    if precheck is not None and precheck.decision == DECISION_OVER_BUDGET:
        return _over_budget_failure(context, start_time, precheck.entropy, precheck.entropy_budget, dispatched=False)
    if precheck is not None and precheck.decision == DECISION_DECOMPOSED:
        # Refuse an over-deep sub-intent list before dispatch, so no executor container or execution branch is spent.
        depth_error = _sub_intent_depth_error(context)
        if depth_error is not None:
            return _stage_failure(context, FlowStage.EXECUTE, start_time, depth_error, dispatched=False)

    existing_executions = _snapshot_child_branches(repo_dir, plan_prefix, "E")
    if existing_executions is None:
        return _stage_failure(
            context,
            FlowStage.EXECUTE,
            start_time,
            f"Execute stage failed: could not fetch origin branches under {plan_prefix} "
            "before dispatching the executor.",
        )
    base_error = remote_error(fetch_base, repo_dir, target_branch)
    if base_error is not None:
        return _stage_failure(context, FlowStage.EXECUTE, start_time, f"Execute stage failed: {base_error}")
    executor_args = [context.plan_branch, context.agent, context.model]
    if resume_partial:
        partial_branch = _resumable_partial_branch(repo_dir, existing_executions)
        if partial_branch:
            executor_args.append(partial_branch)
            context.log(f"Stage 3 resume: continuing partial work from {partial_branch}")
        else:
            context.log("Stage 3 resume: no failed execution branch carries partial work; starting from the plan tip.")
    exit_code = _dispatch_role(context, "executor", executor_args)
    # The container only commits. The host pushes every execution branch it committed, a failed one included, so the
    # failure row and any partial work reach origin for a resume.
    push_error = remote_error(publish_new_child_branches, repo_dir, plan_prefix, "E", existing_executions)
    # The executor exits OVER_BUDGET_EXIT_CODE only once its over_budget row is committed; that row decides.
    if exit_code not in (0, OVER_BUDGET_EXIT_CODE):
        return _stage_failure(
            context,
            FlowStage.EXECUTE,
            start_time,
            f"Execute stage failed: executor agent exited with code {exit_code}.",
            exit_code=exit_code,
        )

    if push_error is not None:
        return _stage_failure(
            context, FlowStage.EXECUTE, start_time, f"Execute stage failed: {push_error}", exit_code=exit_code
        )
    fetch_error = _fetch_branch_prefix_error(repo_dir, plan_prefix)
    if fetch_error is not None:
        return _stage_failure(
            context,
            FlowStage.EXECUTE,
            start_time,
            f"Execute stage failed: the executor agent exited {exit_code} but origin branches under {plan_prefix} "
            f"could not be fetched to adopt its execution branch: {fetch_error}",
            exit_code=exit_code,
        )
    exec_branch, ambiguity = _discover_child_branch(
        repo_dir,
        plan_prefix,
        "E",
        before=existing_executions,
        preferred_suffix=f"-{_sanitize_string(context.agent)}-{_sanitize_string(context.model)}",
    )
    if ambiguity:
        return _stage_failure(context, FlowStage.EXECUTE, start_time, f"Execute stage failed: {ambiguity}.")
    if not exec_branch:
        return _stage_failure(
            context,
            FlowStage.EXECUTE,
            start_time,
            f"Execute stage failed: no execution branch under {context.plan_branch} was found on origin "
            "after the executor agent exited.",
            exit_code=exit_code,
        )

    exec_id = exec_branch.removesuffix("/_").rsplit("/", 1)[-1]
    context.execution_branch = exec_branch
    _track_local_branch(context, exec_branch)
    exec_ref = f"refs/remotes/origin/{exec_branch}"
    summary_row = _latest_execution_summary(_read_ledger_rows(repo_dir, exec_ref, "executions.jsonl"), exec_id)
    container_status = str(summary_row.get("status")) if summary_row else None
    base_payload: dict[str, Any] = {
        "execution_id": exec_id,
        "execution_branch": exec_branch,
        "status": container_status,
        "exit_code": exit_code,
        "token_reduce": context.token_reduce,
    }

    if summary_row is not None and container_status == DECISION_OVER_BUDGET:
        # Defence in depth: the pre-dispatch check normally catches this before the container starts.
        return _over_budget_failure(
            context,
            start_time,
            summary_row.get("entropy"),
            summary_row.get("entropy_budget"),
            dispatched=True,
            **base_payload,
        )
    if exit_code != 0:
        return _stage_failure(
            context,
            FlowStage.EXECUTE,
            start_time,
            f"Execute stage failed: executor agent exited with code {exit_code}.",
            **base_payload,
        )

    if summary_row is not None and container_status == DECISION_DECOMPOSED:
        if precheck is None or precheck.decision != DECISION_DECOMPOSED:
            recorded = precheck.decision if precheck is not None else "no readable decision"
            msg = f"Execute stage failed: the executor reported 'decomposed' but the plan row records '{recorded}'."
            return _stage_failure(context, FlowStage.EXECUTE, start_time, msg, **base_payload)
        record = {**precheck.to_plan_fields(), "plan_branch": context.plan_branch}
        return _run_decomposed_plan(context, start_time, record, base_payload)

    # Empty-diff guard: shared definition with the executor entrypoint and the review loop.
    plan_ref = _resolve_branch_ref(repo_dir, context.plan_branch)
    try:
        if not plan_ref:
            raise RuntimeError(f"plan branch '{context.plan_branch}' is not available locally or on origin")
        changed = changed_paths(repo_dir, plan_ref, exec_ref)
    except (RuntimeError, TimeoutError) as exc:
        return _stage_failure(
            context,
            FlowStage.EXECUTE,
            start_time,
            f"Execute stage failed: could not verify the execution diff, so it is treated as a failure: "
            f"{redact_agent_secrets(str(exc))}",
            **base_payload,
        )

    if not changed:
        guard_summary = empty_diff_summary(context.plan_branch)
        guard_payload = {
            **base_payload,
            "status": EMPTY_DIFF_STATUS,
            "failure_reason": EMPTY_DIFF_FAILURE_REASON,
            "container_status": container_status,
            "superseding_row_recorded": False,
        }
        agent_exit = f"the executor agent exited with code {exit_code}"
        if summary_row is None:
            outcome = (
                f"{agent_exit} but '{exec_branch}' carries no executions.jsonl summary row for {exec_id}, "
                "so no superseding row was recorded"
            )
        elif container_status in FAILED_EXECUTION_STATUSES:
            detail = summary_row.get("summary") or "no summary"
            outcome = (
                f"{agent_exit} and the container already recorded status '{container_status}': {detail}; "
                "no superseding row was written"
            )
        else:
            superseding = {k: v for k, v in summary_row.items() if k != "test_pass_rate"}
            try:
                prior_revision = int(summary_row.get("ledger_revision") or 1)
            except (TypeError, ValueError):
                prior_revision = 1
            superseding.update(
                {
                    "status": EMPTY_DIFF_STATUS,
                    "failure_reason": EMPTY_DIFF_FAILURE_REASON,
                    "summary": guard_summary,
                    "created_at": datetime.now(UTC).isoformat(),
                    "ledger_revision": max(2, prior_revision + 1),
                }
            )
            write_error = _append_superseding_execution_row(context, exec_branch, superseding)
            guard_payload["superseding_row_recorded"] = write_error is None
            if write_error:
                context.log(f"Warning: could not record the empty-diff failure row on '{exec_branch}': {write_error}")
                outcome = (
                    f"{agent_exit} and the container recorded status '{container_status}', but recording the "
                    f"superseding '{EMPTY_DIFF_STATUS}' row (failure_reason '{EMPTY_DIFF_FAILURE_REASON}') failed, "
                    f"so the ledger still shows '{container_status}': {write_error}"
                )
            else:
                outcome = (
                    f"{agent_exit} and the container recorded status '{container_status}'; recorded a superseding "
                    f"status '{EMPTY_DIFF_STATUS}' row (failure_reason '{EMPTY_DIFF_FAILURE_REASON}')"
                )
        return _stage_failure(
            context,
            FlowStage.EXECUTE,
            start_time,
            f"Execute stage failed: {guard_summary}; {outcome}; no PR will be created or updated.",
            **guard_payload,
        )

    if summary_row is None:
        return _stage_failure(
            context,
            FlowStage.EXECUTE,
            start_time,
            f"Execute stage failed: '{exec_branch}' carries no executions.jsonl summary row for {exec_id}.",
            **base_payload,
        )
    if container_status != "success":
        detail = summary_row.get("summary") or "no summary"
        return _stage_failure(
            context,
            FlowStage.EXECUTE,
            start_time,
            f"Execute stage failed: executor agent recorded status '{container_status}': {detail}",
            **base_payload,
        )

    end_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log(f"Stage 3 completed: Execution branch '{exec_branch}' changes {len(changed)} path(s).")
    result = StageResult(
        stage=FlowStage.EXECUTE,
        status=StageStatus.SUCCESS,
        start_time=start_time,
        end_time=end_time,
        # The executor container's own test measurement, when its summary row records one; None means not measured.
        payload={
            **base_payload,
            "changed_files": len(changed),
            "test_pass_rate": recorded_test_pass_rate(summary_row.get("test_pass_rate")),
        },
    )
    context.stage_results["execute"] = result
    return result


def _warn_ledger_failure(context: FlowContext, label: str, ledger_path: str, exc: BaseException) -> None:
    """Report a non-fatal ledger failure at WARNING level and in the operator-facing context log.

    The exception text is passed through the canonical ``CredentialRedactor`` first, so a
    secret carried in the failure detail never reaches either log.
    """
    detail = CredentialRedactor.redact_text(str(exc))
    message = f"Ledger append failed ({label}) for {ledger_path}: {type(exc).__name__}: {detail}"
    logger.warning(message)
    context.log(f"Warning: {message}")
