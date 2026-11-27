from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

from sandbox_executor.budget_limits import UNLIMITED, unlimited_stage_warning
from sandbox_executor.budgets import (
    MAX_REPLAN_ATTEMPTS,
    MAX_SUB_INTENT_DEPTH,
    Budgets,
    StageTimer,
    active_stage_timer,
    format_minutes,
    load_budgets,
)
from sandbox_executor.flow.calibrate_rerun import rerun_calibration
from sandbox_executor.flow.calibrate_sync import restore_starting_branch
from sandbox_executor.flow.checkpoint import save_checkpoint
from sandbox_executor.flow.child_progress import has_recorded_decomposition
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.end_of_flow_sync import finish_flow
from sandbox_executor.flow.resume_guard import _resume_context_error
from sandbox_executor.flow.review_restart import approval_stands, calibrate_with_review_restart
from sandbox_executor.flow.stage_status import REVIEW_BOUNDARY_STATUSES
from sandbox_executor.flow.stages.calibrate import run_calibrate_stage
from sandbox_executor.flow.stages.children import resume_children
from sandbox_executor.flow.stages.decomposition import _sub_intent_depth_error
from sandbox_executor.flow.stages.execute import run_execute_stage
from sandbox_executor.flow.stages.intent import run_intent_stage
from sandbox_executor.flow.stages.plan import _format_entropy, run_plan_stage
from sandbox_executor.flow.stages.review import run_review_stage
from sandbox_executor.flow.stages.touch_set_guard import (
    TOUCH_SET_VIOLATION,
    touch_set_replan_exhausted,
    touch_set_replan_feedback,
)
from sandbox_executor.flow.target_sync_hooks import at_flow_start, at_turn_boundary
from sandbox_executor.flow_guard import pre_run_guard_error
from sandbox_executor.git_flow import target_worktree_problem
from sandbox_executor.ledger import validate_stage_ledger
from sandbox_executor.redaction import redact_agent_secrets


class PipelineEngine:
    STAGE_ORDER: ClassVar[list[FlowStage]] = [
        FlowStage.INTENT,
        FlowStage.PLAN,
        FlowStage.EXECUTE,
        FlowStage.REVIEW,
        FlowStage.CALIBRATE,
    ]
    # why: a post-hoc stage-budget overrun only records these stages' finished results, never downgrades them.
    POST_HOC_BUDGET_EXEMPT: ClassVar[frozenset[FlowStage]] = frozenset({FlowStage.REVIEW, FlowStage.CALIBRATE})

    def __init__(
        self,
        context: FlowContext,
        from_stage: FlowStage | str | None = None,
        checkpoint_path: str | None = None,
        budgets: Budgets | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.context = context
        self.from_stage = FlowStage.from_str(from_stage) if from_stage else None
        # why: Stage 3 saves its admission state to the same checkpoint, so the context carries the path too.
        self.checkpoint_path = context.checkpoint_path = checkpoint_path
        self.budgets = budgets or load_budgets(os.environ)
        self.clock = clock
        # why: deliberately not checkpointed; it holds only while the first stage of a Stage 3 resume is pending.
        self._resume_partial_pending = False
        self.worktree_validator: Callable[[str], str | None] | None = None

    def stage_budget_seconds(self, stage: FlowStage, result: StageResult | None = None) -> float | None:
        if stage == FlowStage.REVIEW:
            iterations = 1
            if result is not None:
                consensus = result.payload.get("consensus") if isinstance(result.payload, dict) else None
                if isinstance(consensus, dict):
                    try:
                        # why: a resumed engine labels its single pass N, so passes run count, not the last label;
                        # why: a present "passes" wins even at 0 (a dispatchless run).
                        raw = consensus["passes"] if "passes" in consensus else consensus.get("iterations", 1)
                        iterations = max(1, int(raw))
                    except (TypeError, ValueError):
                        iterations = 1
            # why: same source as run_review_loop's per-iteration timer, so HOLON_STAGE_BUDGET_SECONDS applies to both.
            per_iteration = self.budgets.stage_seconds("review_iteration")
            return None if per_iteration is None else per_iteration * iterations
        return self.budgets.stage_seconds(stage.value)

    def timed_execute_stage(self, stage: FlowStage) -> StageResult:
        timer = StageTimer(stage.value, self.stage_budget_seconds(stage), clock=self.clock)
        if timer.unlimited:
            self.context.log(f"WARNING: {unlimited_stage_warning(stage.value)}")
        # why: role containers read HOLON_STAGE_BUDGET_SECONDS from the active stage timer; review times each pass.
        scope = active_stage_timer(timer) if stage != FlowStage.REVIEW else contextlib.nullcontext(timer)
        try:
            with scope:
                result = self.execute_stage(stage)
        except Exception as e:
            result = self.handle_stage_failure(stage, e)
        elapsed = timer.stop()
        budget_seconds = self.stage_budget_seconds(stage, result)
        result.elapsed_seconds = round(elapsed, 3)
        result.budget_seconds = budget_seconds
        if budget_seconds is None:
            result.payload = {**result.payload, "stage_budget": UNLIMITED}
            return result
        if elapsed > budget_seconds and result.status not in (StageStatus.FAILED, StageStatus.SKIPPED):
            # why: a decomposed Execute stage spans whole child pipelines whose stages ran under their own budgets.
            if stage in self.POST_HOC_BUDGET_EXEMPT or result.payload.get("decomposed"):
                result.payload = {
                    **result.payload,
                    "budget_overrun": {
                        "budget_name": "stage",
                        "budget_seconds": budget_seconds,
                        "elapsed_seconds": round(elapsed, 3),
                    },
                }
                self.context.log(
                    f"WARNING: Stage '{stage.value}' ran {elapsed / 60.0:.1f} min against its "
                    f"{format_minutes(budget_seconds)} min budget; keeping its '{result.status.value}' result."
                )
                return result
            result.status = StageStatus.FAILED
            result.error = (
                f"Stage '{stage.value}' exceeded its stage budget: {elapsed / 60.0:.1f} min elapsed against "
                f"{format_minutes(budget_seconds)} min."
            )
            result.payload = {
                **result.payload,
                "budget_breach": {"budget_name": "stage", "budget_seconds": budget_seconds},
            }
            self.context.log(result.error)
        return result

    def _rerun_if_calibrate(self, stage: FlowStage, result: StageResult) -> StageResult:
        if stage != FlowStage.CALIBRATE:
            return result
        return rerun_calibration(self.context, result, run_calibrate_stage)

    def _run_stage_once(self, stage: FlowStage) -> StageResult:
        self.before_stage(stage)
        # why: the Stage 5 rerun happens before the result is recorded, so the checkpoint holds the refreshed result.
        return self._rerun_if_calibrate(stage, self.timed_execute_stage(stage))

    def _record_stage(self, stage: FlowStage, result: StageResult) -> None:
        self.after_stage(stage, result)
        self.save_checkpoint()

    def before_stage(self, stage: FlowStage) -> None:
        self.context.log(f"--> [Flow Engine] Entering stage: {stage.value.upper()}")

    def execute_stage(self, stage: FlowStage) -> StageResult:  # noqa: PLR0911
        if stage == FlowStage.INTENT:
            return run_intent_stage(self.context)
        elif stage == FlowStage.PLAN:
            return run_plan_stage(self.context)
        elif stage == FlowStage.EXECUTE:
            # why: only the first Execute stage of a Stage 3 resume continues partial work; a re-plan starts afresh.
            resume_partial, self._resume_partial_pending = self._resume_partial_pending, False
            previous = self.context.stage_results.get(FlowStage.EXECUTE.value)
            if has_recorded_decomposition(previous, self.context.plan_branch):
                # why: a decomposed Stage 3 never re-dispatches the parent; its children resume from their progress.
                return resume_children(self.context, previous)
            if resume_partial:
                return run_execute_stage(self.context, resume_partial=True)
            return run_execute_stage(self.context)
        elif stage == FlowStage.REVIEW:
            return run_review_stage(self.context)
        elif stage == FlowStage.CALIBRATE:
            if self.context.skip_calibrate:
                return _skipped_by_flag(FlowStage.CALIBRATE)
            return run_calibrate_stage(self.context)
        else:
            return StageResult(stage=stage, status=StageStatus.FAILED, error=f"Unsupported stage '{stage}'")

    def after_stage(self, stage: FlowStage, result: StageResult) -> None:
        if result.budget_seconds is None:
            result.budget_seconds = self.stage_budget_seconds(stage, result)
        if result.elapsed_seconds is None and result.status == StageStatus.SKIPPED:
            result.elapsed_seconds = 0.0
        self.context.stage_results[stage.value] = result
        self.context.log(f"<-- [Flow Engine] Exited stage: {stage.value.upper()} (status={result.status.value})")
        self.context.log(result.summary_line())

        repo_dir = getattr(self.context, "target_worktree", None) or getattr(self.context, "repo_dir", None)
        validate_stage_ledger(repo_dir, stage)

    def handle_stage_failure(self, stage: FlowStage, error: Exception) -> StageResult:
        err_msg = redact_agent_secrets(f"Unhandled exception in stage '{stage.value}': {error}")
        self.context.log(err_msg)
        res = StageResult(stage=stage, status=StageStatus.FAILED, error=err_msg)
        self.context.stage_results[stage.value] = res
        return res

    def save_checkpoint(self) -> str:
        return save_checkpoint(self.context, self.checkpoint_path)

    def _replan_feedback(self, result: StageResult) -> str:
        attempt, files = self.context.replan_attempts, result.payload.get(TOUCH_SET_VIOLATION)
        entropy = _format_entropy(result.payload.get("entropy"))
        budget = _format_entropy(result.payload.get("entropy_budget"))
        why = f"plan entropy {entropy} exceeds the entropy budget {budget}"
        if files:
            why = "the execution changed files outside the plan's touch set"
        self.context.log(f"Stage 3: {why}; returning to Stage 2 (re-plan attempt {attempt} of {MAX_REPLAN_ATTEMPTS}).")
        if files:
            return touch_set_replan_feedback(str(self.context.plan_branch), list(files), attempt)
        if _sub_intent_depth_error(self.context) is None:
            request = "write a smaller plan within the budget, or add an explicit sub-intent list."
        else:
            # why: a sub-intent list here would be refused for depth, wasting the attempt.
            request = (
                "write a smaller single plan within the budget. Do not add a sub-intent list: this intent is at "
                f"the sub-intent depth limit of {MAX_SUB_INTENT_DEPTH}, so a sub-intent list would be refused."
            )
        return (
            f"The previous plan '{self.context.plan_branch}' has entropy {entropy}, above the entropy budget "
            f"{budget}, and lists no sub-intent list. Re-plan attempt {attempt} of {MAX_REPLAN_ATTEMPTS}: {request}"
        )

    def _replan_exhausted_error(self, result: StageResult) -> str | None:
        if not _replan_requested(result):
            return None
        attempts = self.context.replan_attempts
        if result.payload.get(TOUCH_SET_VIOLATION):
            return touch_set_replan_exhausted(list(result.payload[TOUCH_SET_VIOLATION]), attempts)
        return (
            f"Execute stage failed: plan entropy {_format_entropy(result.payload.get('entropy'))} exceeds the "
            f"entropy budget {_format_entropy(result.payload.get('entropy_budget'))} after "
            f"{attempts} re-plan attempts and the plan lists no sub-intent list."
        )

    def _replan_after_over_budget(self, result: StageResult) -> StageResult:
        while _replan_requested(result) and self.context.replan_attempts < MAX_REPLAN_ATTEMPTS:
            self.context.replan_attempts += 1
            feedback = self._replan_feedback(result)
            self.context.plan_branch = None
            if (turn_err := at_turn_boundary(self.context, FlowStage.PLAN, self.context.replan_attempts)) is not None:
                return turn_err
            # why: re-plan feedback is transient, never checkpointed or written to a ledger.
            self.context.intent_data["replan_feedback"] = feedback
            try:
                plan_result = self._run_stage_once(FlowStage.PLAN)
            finally:
                self.context.intent_data.pop("replan_feedback", None)
            self._record_stage(FlowStage.PLAN, plan_result)
            if plan_result.status != StageStatus.SUCCESS:
                return plan_result
            result = self._run_stage_once(FlowStage.EXECUTE)
            self._record_stage(FlowStage.EXECUTE, result)

        error = self._replan_exhausted_error(result)
        if error is not None:
            result.error = error
            result.payload = {**result.payload, "replan_attempts": self.context.replan_attempts}
            self.context.stage_results[FlowStage.EXECUTE.value] = result
            self.context.log(result.error)
            self.save_checkpoint()
        return result

    def run(self) -> dict[str, StageResult]:
        try:
            return self._run_stages()
        finally:
            restore_starting_branch(self.context)

    def _start_index(self) -> int:
        if not self.from_stage:
            return 0
        try:
            return self.STAGE_ORDER.index(self.from_stage)
        except ValueError:
            valid = [s.value for s in self.STAGE_ORDER]
            raise ValueError(f"Invalid resumption stage '{self.from_stage}'. Must be one of: {valid}") from None

    def _run_stages(self) -> dict[str, StageResult]:
        start_idx = self._start_index()
        # why: cli.py sets from_stage from the checkpoint, so a --checkpoint resume at Stage 3 counts too.
        self._resume_partial_pending = self.from_stage == FlowStage.EXECUTE
        first_stage = self.STAGE_ORDER[start_idx]
        self.context.log(
            f"Pipeline run initiated from stage: {first_stage.value.upper()} "
            f"(dry_run={self.context.dry_run}, auto_calibrate={self.context.auto_calibrate})"
        )
        guard_error = pre_run_guard_error(self.context, self.worktree_validator) or _resume_context_error(
            self.context, self.from_stage, self.checkpoint_path
        )
        if guard_error is not None:
            _stage_failure(self.context, first_stage, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), guard_error)
            self.context.log(f"Pipeline halted due to failure in stage '{first_stage.value}'.")
            return self.context.stage_results
        if (start_failure := at_flow_start(self.context, first_stage)) is not None:
            self._record_stage(first_stage, start_failure)
            self.context.log(f"Pipeline halted due to failure in stage '{first_stage.value}'.")
            return self.context.stage_results

        for i in range(start_idx, len(self.STAGE_ORDER)):
            stage = self.STAGE_ORDER[i]

            if stage == FlowStage.CALIBRATE and self.context.skip_calibrate:
                self._skip_calibration()
                continue

            # why: a resumed Stage 5 may restart Stage 4 on a red merged head too.
            if stage == FlowStage.CALIBRATE:
                result = calibrate_with_review_restart(self.context, self._run_stage_once, self._record_stage)
            else:
                result = self._run_stage_once(stage)
                self._record_stage(stage, result)

            if stage == FlowStage.EXECUTE:
                result = self._replan_after_over_budget(result)

            if result.status == StageStatus.FAILED:
                # why: result.stage, not stage, because a failed re-plan ends the run in Stage 2.
                self.context.log(f"Pipeline halted due to failure in stage '{result.stage.value}'.")
                break

            # why: Stage 4 ends at the consensus boundary before merge; no earlier stage halts or waits for a human.
            if result.status in REVIEW_BOUNDARY_STATUSES:
                self._handle_review_boundary(stage, result)
                break
            rev = self.context.stage_results.get(FlowStage.REVIEW.value)
            if stage == FlowStage.CALIBRATE and result.status == StageStatus.SUCCESS and rev is not None:
                self._finalize_approved_flow(stage, rev)

        return self.context.stage_results

    def _handle_review_boundary(self, stage: FlowStage, result: StageResult) -> None:
        if stage == FlowStage.REVIEW:
            self._calibrate_after_consensus_halt()
        cal = self.context.stage_results.get(FlowStage.CALIBRATE.value)
        if approval_stands(self.context, result) and (cal is None or cal.status != StageStatus.FAILED):
            self._finalize_approved_flow(stage)
        elif not approval_stands(self.context, result):
            halt = f"safely halted for human intervention at stage '{stage.value}'. Halting execution without merging."
            self.context.log(f"Pipeline {halt}")

    def _finalize_approved_flow(self, stage: FlowStage, review: StageResult | None = None) -> None:
        if review is not None and not approval_stands(self.context, review):
            return
        end_sync = finish_flow(self.context, self._run_stage_once, self._record_stage)
        if end_sync.blocked or not end_sync.clean:
            self._record_stage(FlowStage.CALIBRATE, end_sync.failure_result(self.context))
            self.context.log(f"Pipeline blocked at end-of-flow sync: {end_sync.reason}")
            return
        done = f"review approved, PR ready for maintainer merge (stage '{stage.value}'); completed successfully."
        self.context.log(f"Pipeline finished at the merge boundary: {done}")

    def _calibrate_after_consensus_halt(self) -> None:
        if self.context.skip_calibrate:
            self._skip_calibration()
        elif self.context.auto_calibrate:
            calibrate_with_review_restart(self.context, self._run_stage_once, self._record_stage)

    def _skip_calibration(self) -> None:
        self.context.log("Stage 5 (CALIBRATE) skipped due to --skip-calibrate flag.")
        self._record_stage(FlowStage.CALIBRATE, _skipped_by_flag(FlowStage.CALIBRATE))


def _skipped_by_flag(stage: FlowStage) -> StageResult:
    return StageResult(stage=stage, status=StageStatus.SKIPPED, payload={"skipped_by_flag": True})


def _replan_requested(result: StageResult) -> bool:
    refused = result.payload.get("over_budget") or result.payload.get(TOUCH_SET_VIOLATION)
    return result.stage == FlowStage.EXECUTE and result.status == StageStatus.FAILED and bool(refused)


def run_flow_pipeline(
    context: FlowContext,
    from_stage: FlowStage | str | None = None,
    checkpoint_path: str | None = None,
) -> dict[str, Any]:
    try:
        engine = PipelineEngine(context, from_stage=from_stage, checkpoint_path=checkpoint_path)
        engine.worktree_validator = target_worktree_problem
        engine.run()
        return context.to_dict()
    finally:
        if context.token_reduce:
            try:
                from sandbox_executor.launcher import sidecar

                sidecar.teardown_token_reduction_proxy()
            except Exception:
                pass
