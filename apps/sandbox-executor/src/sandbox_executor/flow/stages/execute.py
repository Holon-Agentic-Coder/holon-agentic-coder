"""Stage 3 (Plan Execution): dispatch the executor, adopt its execution branch and resume failed partial
executions.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.decomposition_rule import (
    DECISION_DECOMPOSED,
    DECISION_OVER_BUDGET,
    DecompositionDecision,
    InvalidPlanDecisionError,
)
from sandbox_executor.entrypoint.executor import OVER_BUDGET_EXIT_CODE
from sandbox_executor.flow.admission import admission_stage_failure
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _dispatch_role, _recorded_test_fields, _stage_failure
from sandbox_executor.flow.execution_publish import held_execution_failure, hold_unrecorded_executions
from sandbox_executor.flow.execution_recovery import rejected_push_failure, rejected_push_recovery
from sandbox_executor.flow.git_refs import (
    _discover_child_branch,
    _fetch_branch_prefix_error,
    _latest_execution_summary,
    _read_ledger_rows,
    _resolve_branch_ref,
    _snapshot_child_branches,
    _track_local_branch,
)
from sandbox_executor.flow.host_remote import fetch_base, publish_new_child_branches, remote_error
from sandbox_executor.flow.partial_head import _newest_partial_head
from sandbox_executor.flow.partial_salvage import salvage_for_resume
from sandbox_executor.flow.plan_refresh import plan_refresh_stage_failure, refresh_payload
from sandbox_executor.flow.stages.decomposition import _run_decomposed_plan, _sub_intent_depth_error
from sandbox_executor.flow.stages.empty_diff_outcome import empty_diff_failure
from sandbox_executor.flow.stages.execute_veto import execution_veto_error
from sandbox_executor.flow.stages.plan_decision import _over_budget_failure, _plan_budget_decision
from sandbox_executor.flow.stages.plan_support import replan_feedback_env
from sandbox_executor.flow.stages.sync_conflict import sync_conflict_result
from sandbox_executor.flow.stages.token_scope_guard import token_scope_failure
from sandbox_executor.flow.stages.touch_set_guard import touch_set_violation_result
from sandbox_executor.flow.target_sync_hooks import at_stage_boundary, consume_target_merge_notice
from sandbox_executor.flow.target_sync_result import SyncBoundary
from sandbox_executor.flow_guard import SKIP_PUSH_DISPATCH_ERROR
from sandbox_executor.git import changed_paths, run_git
from sandbox_executor.ledger import build_execution_id
from sandbox_executor.naming import MissingTickerError, model_code, ticker_for
from sandbox_executor.redaction import redact_agent_secrets


def _resumable_partial_branch(repo_dir: str, branches: set[str]) -> str | None:
    """Newest failed execution branch whose latest summary row carries a ``partial_head`` to resume from.

    ``branches`` is the pre-dispatch snapshot of execution branches under the plan; each is read at
    ``refs/remotes/origin/<branch>``. The executor re-reads and verifies the partial head itself.
    """
    newest = _newest_partial_head(repo_dir, branches)
    return newest[0] if newest else None


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(slots=True)
class ExecuteState:
    """What the Stage 3 steps share: each step reads the fields an earlier step set and fills its own."""

    start_time: str
    plan_prefix: str = ""
    repo_dir: str = ""
    preferred_suffix: str = ""
    target_branch: str = ""
    precheck: DecompositionDecision | None = None
    existing_executions: set[str] = field(default_factory=set)
    executor_args: list[str] = field(default_factory=list)
    exit_code: int = 0
    push_error: str | None = None
    exec_branch: str = ""
    exec_id: str = ""
    exec_ref: str = ""
    summary_row: dict[str, Any] | None = None
    container_status: str | None = None
    base_payload: dict[str, Any] = field(default_factory=dict)
    changed: list[str] = field(default_factory=list)
    tested_head: str = ""


class ExecuteStage:
    """Stage 3: dispatch the executor agent, adopt its execution branch and apply the empty-diff guard.

    The executor container implements the plan, appends executions.jsonl and commits; the host pushes and guards
    against an empty diff outside metadata paths. With ``resume_partial``, the newest failed execution branch under the
    plan carrying a ``partial_head`` is passed to the executor to continue partial work. The stage never halts for a
    human and executes only the plan row's decision (*execute*, *decomposed* or *over budget*).
    """

    def __init__(self, context: FlowContext, resume_partial: bool = False) -> None:
        self.context = context
        self.resume_partial = resume_partial
        self.state = ExecuteState(start_time=_utc_now())

    def steps(self) -> tuple[Callable[[], StageResult | None], ...]:
        """The guarding steps, in run order; :meth:`record_execution` follows the last."""
        return (
            self.preflight,
            self.check_token_scopes,
            self.admit_and_refresh_plan,
            self.resume_point,
            self.salvage_partial,
            self.dispatch_executor,
            self.adopt_execution_branch,
            self.check_container_outcome,
            self.guard_empty_diff,
        )

    def run(self) -> StageResult:
        """Run each step until one returns a result, else record the execution."""
        self.context.log("Starting Stage 3: Plan Execution")
        for step in self.steps():
            result = step()
            if result is not None:
                return result
        return self.record_execution()

    def _fail(self, error: str, **payload: Any) -> StageResult:
        return _stage_failure(self.context, FlowStage.EXECUTE, self.state.start_time, error, **payload)

    def _bare_failure(self, error: str) -> StageResult:
        """A failed result that, unlike :meth:`_fail`, is neither logged here nor stored in the context."""
        return StageResult(
            stage=FlowStage.EXECUTE, status=StageStatus.FAILED, start_time=self.state.start_time, error=error
        )

    def preflight(self) -> StageResult | None:
        """Require a plan branch and a ticker, honour a failure veto, answer a dry run and refuse ``skip_push``."""
        context, state = self.context, self.state
        if not context.plan_branch:
            err = "Cannot run Execute stage without an active plan_branch in FlowContext."
            context.log(err)
            return self._bare_failure(err)
        state.plan_prefix = context.plan_branch.removesuffix("/_").rstrip("/")
        state.repo_dir = context.target_worktree
        try:
            # The new execution branch ends in the runner's ticker and model code; a ticker-less runner fails here.
            state.preferred_suffix = f"-{ticker_for(context.agent)}-{model_code(context.model)}"
        except MissingTickerError as exc:
            return self._fail(f"Execute stage failed: {exc}")
        veto_error = execution_veto_error(context)
        if veto_error is not None:
            return self._bare_failure(veto_error)
        if context.dry_run:
            return self._dry_run_result()
        if context.skip_push:
            return self._fail(SKIP_PUSH_DISPATCH_ERROR)
        return None

    def _dry_run_result(self) -> StageResult:
        """Validation only: name the branch the executor would create, without dispatching or writing anything."""
        context, start_time = self.context, self.state.start_time
        exec_id = build_execution_id(int(time.time()), self.state.plan_prefix, context.agent, context.model)
        exec_branch = f"{self.state.plan_prefix}/{exec_id}/_"
        context.execution_branch = exec_branch
        context.log(f"Stage 3 (dry run): executor dispatch skipped; execution branch would be '{exec_branch}'.")
        admission_stage_failure(context, start_time)  # a dry run only logs the admission decision
        plan_refresh_stage_failure(context, start_time)  # and the plan refresh a waited flow would get
        result = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            start_time=start_time,
            end_time=_utc_now(),
            payload={
                "execution_id": exec_id,
                "execution_branch": exec_branch,
                "token_reduce": context.token_reduce,
                "dry_run": True,
            },
        )
        context.stage_results["execute"] = result
        return result

    def check_token_scopes(self) -> StageResult | None:
        """Refuse a plan touching ``.github/workflows/**`` whose GitHub token lacks the ``workflow`` scope."""
        return token_scope_failure(self.context, self.state.start_time)

    def admit_and_refresh_plan(self) -> StageResult | None:
        """Sync with the target branch, refuse a plan the budget decision rejects, then run admission and refresh."""
        context, state = self.context, self.state
        state.target_branch = context.intent_data.get("target_branch", "main")
        if (failure := at_stage_boundary(context, FlowStage.EXECUTE, SyncBoundary.STAGE_START)) is not None:
            return failure
        try:
            state.precheck = precheck = _plan_budget_decision(context)
        except InvalidPlanDecisionError as exc:
            return self._fail(str(exc), dispatched=False)
        if precheck is not None and precheck.decision == DECISION_OVER_BUDGET:
            return _over_budget_failure(
                context, state.start_time, precheck.entropy, precheck.entropy_budget, dispatched=False
            )
        if precheck is not None and precheck.decision == DECISION_DECOMPOSED:
            # Refuse an over-deep sub-intent list before dispatch, so no executor container or execution branch is
            # spent.
            depth_error = _sub_intent_depth_error(context)
            if depth_error is not None:
                return self._fail(depth_error, dispatched=False)
        # A flow admitted after an overlap wait merges origin/<target> into its plan branch before anything is cut
        # from it.
        return admission_stage_failure(context, state.start_time) or plan_refresh_stage_failure(
            context, state.start_time
        )

    def resume_point(self) -> StageResult | None:
        """Snapshot the plan's execution branches on origin, fetch the target branch and seed the executor arguments."""
        context, state = self.context, self.state
        existing_executions = _snapshot_child_branches(state.repo_dir, state.plan_prefix, "E")
        if existing_executions is None:
            return self._fail(
                f"Execute stage failed: could not fetch origin branches under {state.plan_prefix} "
                "before dispatching the executor."
            )
        state.existing_executions = existing_executions
        base_error = remote_error(fetch_base, state.repo_dir, state.target_branch)
        if base_error is not None:
            return self._fail(f"Execute stage failed: {base_error}")
        state.executor_args = [str(context.plan_branch), context.agent, context.model]
        return None

    def salvage_partial(self) -> StageResult | None:
        """On a resume, salvage leftovers and pass the newest resumable partial branch to the executor."""
        if not self.resume_partial:
            return None
        context, state = self.context, self.state
        state.existing_executions, salvage_error = salvage_for_resume(
            context, state.plan_prefix, state.existing_executions
        )
        if salvage_error:
            return self._fail(salvage_error, dispatched=False)
        partial_branch = _resumable_partial_branch(state.repo_dir, state.existing_executions)
        if partial_branch:
            state.executor_args.append(partial_branch)
            context.log(f"Stage 3 resume: continuing partial work from {partial_branch}")
        else:
            context.log("Stage 3 resume: no failed execution branch carries partial work; starting from the plan tip.")
        return None

    def dispatch_executor(self) -> StageResult | None:
        """Run the executor container, then push each new execution branch that carries its ledger row."""
        context, state = self.context, self.state
        with replan_feedback_env("", consume_target_merge_notice(context)):
            state.exit_code = exit_code = _dispatch_role(context, "executor", state.executor_args)
        # The container only commits; the host pushes each committed branch with its ledger row, failed ones included.
        left_alone = hold_unrecorded_executions(context, state.plan_prefix, state.existing_executions)
        state.push_error = remote_error(publish_new_child_branches, state.repo_dir, state.plan_prefix, "E", left_alone)
        held = left_alone - state.existing_executions
        exit_failed = exit_code not in (0, OVER_BUDGET_EXIT_CODE)  # an over-budget exit's committed row decides
        log = context.log if held or exit_failed else None  # a failure below returns before the recovery error
        recovery = rejected_push_recovery(state.repo_dir, state.plan_prefix, left_alone, state.push_error, log)
        if held_failure := held_execution_failure(context, state.start_time, held, exit_code):
            return held_failure
        if exit_failed:
            return self._fail(
                f"Execute stage failed: executor agent exited with code {exit_code}.", exit_code=exit_code
            )
        return rejected_push_failure(context, state.start_time, recovery, exit_code)

    def adopt_execution_branch(self) -> StageResult | None:
        """Find the executor's new execution branch on origin, track it locally and read its summary row."""
        context, state = self.context, self.state
        fetch_error = _fetch_branch_prefix_error(state.repo_dir, state.plan_prefix)
        if fetch_error is not None:
            return self._fail(
                f"Execute stage failed: the executor agent exited {state.exit_code} but origin branches under "
                f"{state.plan_prefix} could not be fetched to adopt its execution branch: {fetch_error}",
                exit_code=state.exit_code,
            )
        exec_branch, ambiguity = _discover_child_branch(
            state.repo_dir,
            state.plan_prefix,
            "E",
            before=state.existing_executions,
            preferred_suffix=state.preferred_suffix,
        )
        if ambiguity:
            return self._fail(f"Execute stage failed: {ambiguity}.")
        if not exec_branch:
            return self._fail(
                f"Execute stage failed: no execution branch under {context.plan_branch} was found on origin "
                "after the executor agent exited.",
                exit_code=state.exit_code,
            )
        state.exec_branch = exec_branch
        state.exec_id = exec_id = exec_branch.removesuffix("/_").rsplit("/", 1)[-1]
        context.execution_branch = exec_branch
        _track_local_branch(context, exec_branch)
        state.exec_ref = exec_ref = f"refs/remotes/origin/{exec_branch}"
        tip = run_git(["rev-parse", "--verify", f"{exec_ref}^{{commit}}"], cwd=state.repo_dir, check=False)
        state.tested_head = tip.stdout.strip() if tip.returncode == 0 else ""
        rows = _read_ledger_rows(state.repo_dir, exec_ref, "executions.jsonl")
        state.summary_row = summary_row = _latest_execution_summary(rows, exec_id, exec_ref)
        state.container_status = str(summary_row.get("status")) if summary_row else None
        state.base_payload = {
            "execution_id": exec_id,
            "execution_branch": exec_branch,
            "status": state.container_status,
            "exit_code": state.exit_code,
            "token_reduce": context.token_reduce,
            **refresh_payload(context),
        }
        return None

    def check_container_outcome(self) -> StageResult | None:
        """Act on an over-budget, failed, decomposed or sync-conflicted container outcome."""
        state = self.state
        summary_row, container_status = state.summary_row, state.container_status
        if summary_row is not None and container_status == DECISION_OVER_BUDGET:
            # Defence in depth: the pre-dispatch check normally catches this before the container starts.
            return _over_budget_failure(
                self.context,
                state.start_time,
                summary_row.get("entropy"),
                summary_row.get("entropy_budget"),
                dispatched=True,
                **state.base_payload,
            )
        if state.exit_code != 0:
            return self._fail(
                f"Execute stage failed: executor agent exited with code {state.exit_code}.", **state.base_payload
            )
        if summary_row is not None and container_status == DECISION_DECOMPOSED:
            return self._decomposed_outcome()
        conflict_outcome = sync_conflict_result(self.context, state.start_time, summary_row, state.base_payload)
        state.container_status = str(summary_row.get("status")) if summary_row else None
        return conflict_outcome

    def _decomposed_outcome(self) -> StageResult:
        """Run the plan row's sub-intent list, or fail when the plan row does not record 'decomposed'."""
        context, state = self.context, self.state
        precheck = state.precheck
        if precheck is None or precheck.decision != DECISION_DECOMPOSED:
            recorded = precheck.decision if precheck is not None else "no readable decision"
            msg = f"Execute stage failed: the executor reported 'decomposed' but the plan row records '{recorded}'."
            return self._fail(msg, **state.base_payload)
        record = {**precheck.to_plan_fields(), "plan_branch": context.plan_branch}
        return _run_decomposed_plan(context, state.start_time, record, state.base_payload)

    def guard_empty_diff(self) -> StageResult | None:
        """Empty-diff guard: shared definition with the executor entrypoint and the review loop."""
        context, state = self.context, self.state
        plan_ref = _resolve_branch_ref(state.repo_dir, context.plan_branch)
        try:
            if not plan_ref:
                raise RuntimeError(f"plan branch '{context.plan_branch}' is not available locally or on origin")
            state.changed = changed_paths(state.repo_dir, plan_ref, state.exec_ref)
        except (RuntimeError, TimeoutError) as exc:
            return self._fail(
                f"Execute stage failed: could not verify the execution diff, so it is treated as a failure: "
                f"{redact_agent_secrets(str(exc))}",
                **state.base_payload,
            )
        if not state.changed:
            return empty_diff_failure(
                context, state.start_time, state.exec_branch, state.summary_row, state.base_payload
            )
        return None

    def record_execution(self) -> StageResult:
        """Require a successful summary row and a diff inside the touch set, then record the successful stage."""
        context, state = self.context, self.state
        summary_row = state.summary_row
        if summary_row is None:
            return self._fail(
                f"Execute stage failed: '{state.exec_branch}' carries no executions.jsonl summary row for "
                f"{state.exec_id}.",
                **state.base_payload,
            )
        if state.container_status != "success":
            detail = summary_row.get("summary") or "no summary"
            return self._fail(
                f"Execute stage failed: executor agent recorded status '{state.container_status}': {detail}",
                **state.base_payload,
            )
        violation = touch_set_violation_result(
            context, state.start_time, summary_row, state.changed, state.base_payload
        )
        if violation is not None:
            return violation
        # The commit Stage 3 tested, captured before STAGE_END sync so Stage 4 applies test evidence only to it.
        if not state.tested_head:
            tip = run_git(["rev-parse", "--verify", f"{state.exec_ref}^{{commit}}"], cwd=state.repo_dir, check=False)
            state.tested_head = tip.stdout.strip() if tip.returncode == 0 else ""
        if (
            not state.base_payload.get("sync_conflict_resolved")
            and (end_err := at_stage_boundary(context, FlowStage.EXECUTE, SyncBoundary.STAGE_END)) is not None
        ):
            return end_err
        end_time = _utc_now()
        context.log(f"Stage 3 completed: Execution branch '{state.exec_branch}' changes {len(state.changed)} path(s).")
        result = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            start_time=state.start_time,
            end_time=end_time,
            # The executor container's own test measurement, when its summary row records one; None means not measured.
            payload={
                **state.base_payload,
                "changed_files": len(state.changed),
                "head_sha": state.tested_head,
                **_recorded_test_fields(summary_row),
            },
        )
        context.stage_results["execute"] = result
        return result


def run_execute_stage(context: FlowContext, resume_partial: bool = False) -> StageResult:
    """Execute Stage 3 (see :class:`ExecuteStage`); the entry point the pipeline calls."""
    return ExecuteStage(context, resume_partial).run()
