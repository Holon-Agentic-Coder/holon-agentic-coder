"""The Holon Flow pipeline engine: runs the five stages in order, with checkpoints and resumption."""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

from sandbox_executor.budgets import (
    MAX_REPLAN_ATTEMPTS,
    MAX_SUB_INTENT_DEPTH,
    Budgets,
    StageTimer,
    active_stage_timer,
    format_minutes,
    load_budgets,
)
from sandbox_executor.flow.checkpoint import save_checkpoint
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.stages.calibrate import run_calibrate_stage
from sandbox_executor.flow.stages.decomposition import _sub_intent_depth_error
from sandbox_executor.flow.stages.execute import run_execute_stage
from sandbox_executor.flow.stages.intent import run_intent_stage
from sandbox_executor.flow.stages.plan import _format_entropy, run_plan_stage
from sandbox_executor.flow.stages.review import run_review_stage
from sandbox_executor.flow_guard import pre_run_guard_error
from sandbox_executor.git_flow import target_worktree_problem
from sandbox_executor.ledger import validate_stage_ledger
from sandbox_executor.redaction import redact_agent_secrets


class PipelineEngine:
    """Orchestrates the lifecycle stages of the Holon Flow pipeline."""

    STAGE_ORDER: ClassVar[list[FlowStage]] = [
        FlowStage.INTENT,
        FlowStage.PLAN,
        FlowStage.EXECUTE,
        FlowStage.REVIEW,
        FlowStage.CALIBRATE,
    ]
    # Stages whose finished result a post-hoc stage-budget overrun only records, never downgrades (see
    # timed_execute_stage).
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
        self.checkpoint_path = checkpoint_path
        self.budgets = budgets or load_budgets(os.environ)
        self.clock = clock
        # True only while the first stage of a run resumed at Stage 3 is pending; deliberately not checkpointed.
        self._resume_partial_pending = False
        self.worktree_validator: Callable[[str], str | None] | None = None

    def stage_budget_seconds(self, stage: FlowStage, result: StageResult | None = None) -> float:
        """Ceiling for ``stage``; the review stage gets one review-iteration budget per pass it ran."""
        if stage == FlowStage.REVIEW:
            iterations = 1
            if result is not None:
                consensus = result.payload.get("consensus") if isinstance(result.payload, dict) else None
                if isinstance(consensus, dict):
                    try:
                        # Passes run, not the last label: a resumed engine labels its single pass N. A present
                        # "passes" wins even at 0 (a dispatchless run), falling back to the label only when absent.
                        raw = consensus["passes"] if "passes" in consensus else consensus.get("iterations", 1)
                        iterations = max(1, int(raw))
                    except (TypeError, ValueError):
                        iterations = 1
            # Same source as run_review_loop's per-iteration timer, so HOLON_STAGE_BUDGET_SECONDS applies to both.
            return self.budgets.stage_seconds("review_iteration") * iterations
        return self.budgets.stage_seconds(stage.value)

    def timed_execute_stage(self, stage: FlowStage) -> StageResult:
        """Run a stage under its wall-clock ceiling and attach elapsed and budget to the result.

        Stages run in-process, so the ceiling is enforced when the stage returns: a stage that ran past its budget is
        marked failed with the budget that fired, which stops the pipeline before the next stage. The review and
        calibration stages are exempt from that downgrade: the review loop already enforces the per-iteration budget
        itself and a review that reached consensus has posted its approval (failing it afterwards would skip the
        pre-merge calibration), and calibration is the last stage, whose calibrated branch is already pushed. For
        those an overrun is only logged and recorded as ``budget_overrun`` in the payload. The ACT-BUDGET ledger action
        is written only by the in-container turn supervisor, never here, so a failure is not recorded twice.

        While the stage runs, its timer is the active host stage timer (:func:`budgets.active_stage_timer`), so every
        role container started through ``cli.run_docker_container`` during the stage receives the remaining time minus
        a safety margin as ``HOLON_STAGE_BUDGET_SECONDS`` and stops before this ceiling. The review stage is excluded:
        its ceiling is one review-iteration budget per iteration, which is only known afterwards, so a stage-wide
        remainder would be wrong from the second iteration on (the review loop dispatches no role container).
        """
        timer = StageTimer(stage.value, self.stage_budget_seconds(stage), clock=self.clock)
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
        if elapsed > budget_seconds and result.status not in (StageStatus.FAILED, StageStatus.SKIPPED):
            # A decomposed Execute stage spans whole child pipelines, each of whose stages ran under its own budget.
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

    def before_stage(self, stage: FlowStage) -> None:
        """Lifecycle hook invoked before executing a stage."""
        self.context.log(f"--> [Flow Engine] Entering stage: {stage.value.upper()}")

    def execute_stage(self, stage: FlowStage) -> StageResult:  # noqa: PLR0911
        """Dispatch stage execution to the appropriate handler."""
        if stage == FlowStage.INTENT:
            return run_intent_stage(self.context)
        elif stage == FlowStage.PLAN:
            return run_plan_stage(self.context)
        elif stage == FlowStage.EXECUTE:
            # Only the first Execute stage of a Stage 3 resume continues partial work; a re-plan starts afresh.
            resume_partial, self._resume_partial_pending = self._resume_partial_pending, False
            if resume_partial:
                return run_execute_stage(self.context, resume_partial=True)
            return run_execute_stage(self.context)
        elif stage == FlowStage.REVIEW:
            if self.context.skip_review:
                return StageResult(
                    stage=FlowStage.REVIEW,
                    status=StageStatus.SKIPPED,
                    payload={"skipped_by_flag": True},
                )
            return run_review_stage(self.context)
        elif stage == FlowStage.CALIBRATE:
            if self.context.skip_calibrate:
                return StageResult(
                    stage=FlowStage.CALIBRATE,
                    status=StageStatus.SKIPPED,
                    payload={"skipped_by_flag": True},
                )
            return run_calibrate_stage(self.context)
        else:
            return StageResult(
                stage=stage,
                status=StageStatus.FAILED,
                error=f"Unsupported stage '{stage}'",
            )

    def after_stage(self, stage: FlowStage, result: StageResult) -> None:
        """Lifecycle hook invoked after executing a stage."""
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
        """Handle unexpected exceptions during stage execution."""
        err_msg = redact_agent_secrets(f"Unhandled exception in stage '{stage.value}': {error}")
        self.context.log(err_msg)
        res = StageResult(
            stage=stage,
            status=StageStatus.FAILED,
            error=err_msg,
        )
        self.context.stage_results[stage.value] = res
        return res

    def save_checkpoint(self) -> str:
        """Save active pipeline state to checkpoint file."""
        return save_checkpoint(self.context, self.checkpoint_path)

    def _parent_was_decomposed(self) -> bool:
        """True when Stage 3 succeeded by running the plan's sub-intent list as child pipelines."""
        execute = self.context.stage_results.get(FlowStage.EXECUTE.value)
        return bool(execute is not None and execute.status == StageStatus.SUCCESS and execute.payload.get("decomposed"))

    def _replan_after_over_budget(self, result: StageResult) -> StageResult:
        """Return an over-budget plan to Stage 2 up to ``MAX_REPLAN_ATTEMPTS`` times, then fail Stage 3 clearly.

        Each attempt asks the planner, through transient feedback that is never checkpointed or written to a ledger,
        for a plan within the budget or an explicit sub-intent list (only a smaller plan at the sub-intent depth limit,
        where a list would be refused), and re-runs Stage 2 and Stage 3 in place.
        """
        while (
            result.stage == FlowStage.EXECUTE
            and result.status == StageStatus.FAILED
            and result.payload.get("over_budget")
            and self.context.replan_attempts < MAX_REPLAN_ATTEMPTS
        ):
            self.context.replan_attempts += 1
            entropy = _format_entropy(result.payload.get("entropy"))
            budget = _format_entropy(result.payload.get("entropy_budget"))
            self.context.log(
                f"Stage 3: plan entropy {entropy} exceeds the entropy budget {budget}; returning to Stage 2 "
                f"(re-plan attempt {self.context.replan_attempts} of {MAX_REPLAN_ATTEMPTS})."
            )
            if _sub_intent_depth_error(self.context) is None:
                request = "write a smaller plan within the budget, or add an explicit sub-intent list."
            else:
                # A sub-intent list here would be refused for depth, wasting the attempt: ask for one smaller plan only.
                request = (
                    "write a smaller single plan within the budget. Do not add a sub-intent list: this intent is at "
                    f"the sub-intent depth limit of {MAX_SUB_INTENT_DEPTH}, so a sub-intent list would be refused."
                )
            self.context.intent_data["replan_feedback"] = (
                f"The previous plan '{self.context.plan_branch}' has entropy {entropy}, above the entropy budget "
                f"{budget}, and lists no sub-intent list. Re-plan attempt {self.context.replan_attempts} of "
                f"{MAX_REPLAN_ATTEMPTS}: {request}"
            )
            self.context.plan_branch = None
            self.before_stage(FlowStage.PLAN)
            try:
                plan_result = self.timed_execute_stage(FlowStage.PLAN)
            finally:
                self.context.intent_data.pop("replan_feedback", None)
            self.after_stage(FlowStage.PLAN, plan_result)
            self.save_checkpoint()
            if plan_result.status != StageStatus.SUCCESS:
                return plan_result
            self.before_stage(FlowStage.EXECUTE)
            result = self.timed_execute_stage(FlowStage.EXECUTE)
            self.after_stage(FlowStage.EXECUTE, result)
            self.save_checkpoint()

        exhausted = result.stage == FlowStage.EXECUTE and result.status == StageStatus.FAILED
        if exhausted and result.payload.get("over_budget"):
            result.error = (
                f"Execute stage failed: plan entropy {_format_entropy(result.payload.get('entropy'))} exceeds the "
                f"entropy budget {_format_entropy(result.payload.get('entropy_budget'))} after "
                f"{self.context.replan_attempts} re-plan attempts and the plan lists no sub-intent list."
            )
            result.payload = {**result.payload, "replan_attempts": self.context.replan_attempts}
            self.context.stage_results[FlowStage.EXECUTE.value] = result
            self.context.log(result.error)
            self.save_checkpoint()
        return result

    def run(self) -> dict[str, StageResult]:  # noqa: C901, PLR0912, PLR0915
        """Execute the configured pipeline stages sequentially."""
        start_idx = 0
        if self.from_stage:
            try:
                start_idx = self.STAGE_ORDER.index(self.from_stage)
            except ValueError:
                valid = [s.value for s in self.STAGE_ORDER]
                raise ValueError(f"Invalid resumption stage '{self.from_stage}'. Must be one of: {valid}") from None

        # A --from-stage execute or --checkpoint resume at Stage 3 (cli.py sets from_stage from the checkpoint).
        self._resume_partial_pending = self.from_stage == FlowStage.EXECUTE
        self.context.log(
            f"Pipeline run initiated from stage: {self.STAGE_ORDER[start_idx].value.upper()} "
            f"(dry_run={self.context.dry_run}, auto_calibrate={self.context.auto_calibrate})"
        )

        # Reject an invalid run before any stage runs or a checkpoint is written, so a non-dry resume never half-runs
        # Stage 4 or Stage 5 (see flow_guard.pre_run_guard_error).
        guard_error = pre_run_guard_error(self.context, self.worktree_validator)
        if guard_error is not None:
            first_stage = self.STAGE_ORDER[start_idx]
            _stage_failure(self.context, first_stage, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), guard_error)
            self.context.log(f"Pipeline halted due to failure in stage '{first_stage.value}'.")
            return self.context.stage_results

        for i in range(start_idx, len(self.STAGE_ORDER)):
            stage = self.STAGE_ORDER[i]

            if stage in (FlowStage.REVIEW, FlowStage.CALIBRATE) and self._parent_was_decomposed():
                # Review and calibration already ran per child; the parent's own execution branch is ledger-only.
                self.context.log(f"Stage '{stage.value}' skipped: the plan was decomposed into child pipelines.")
                result = StageResult(stage=stage, status=StageStatus.SKIPPED, payload={"reason": "decomposed"})
                self.after_stage(stage, result)
                self.save_checkpoint()
                continue

            if stage == FlowStage.REVIEW and self.context.skip_review:
                self.context.log("Stage 4 (REVIEW) skipped due to --skip-review flag.")
                result = StageResult(
                    stage=FlowStage.REVIEW,
                    status=StageStatus.SKIPPED,
                    payload={"skipped_by_flag": True},
                )
                self.after_stage(stage, result)
                self.save_checkpoint()
                continue

            if stage == FlowStage.CALIBRATE and self.context.skip_calibrate:
                self.context.log("Stage 5 (CALIBRATE) skipped due to --skip-calibrate flag.")
                result = StageResult(
                    stage=FlowStage.CALIBRATE,
                    status=StageStatus.SKIPPED,
                    payload={"skipped_by_flag": True},
                )
                self.after_stage(stage, result)
                self.save_checkpoint()
                continue

            self.before_stage(stage)
            result = self.timed_execute_stage(stage)

            self.after_stage(stage, result)
            self.save_checkpoint()

            if stage == FlowStage.EXECUTE:
                result = self._replan_after_over_budget(result)

            if result.status == StageStatus.FAILED:
                # result.stage, not stage: a failed re-plan ends the run in Stage 2.
                self.context.log(f"Pipeline halted due to failure in stage '{result.stage.value}'.")
                break

            # Only Stage 4 (the review consensus halt before merge) may halt; no earlier stage waits for a human.
            if result.status == StageStatus.HALTED_FOR_HUMAN:
                if stage == FlowStage.REVIEW:
                    if self.context.auto_calibrate and not self.context.skip_calibrate:
                        cal_stage = FlowStage.CALIBRATE
                        self.before_stage(cal_stage)
                        cal_result = self.timed_execute_stage(cal_stage)
                        self.after_stage(cal_stage, cal_result)
                        self.save_checkpoint()
                    elif self.context.skip_calibrate:
                        self.context.log("Stage 5 (CALIBRATE) skipped due to --skip-calibrate flag.")
                        cal_result = StageResult(
                            stage=FlowStage.CALIBRATE,
                            status=StageStatus.SKIPPED,
                            payload={"skipped_by_flag": True},
                        )
                        self.after_stage(FlowStage.CALIBRATE, cal_result)
                        self.save_checkpoint()

                self.context.log(
                    f"Pipeline safely halted for human intervention at stage '{stage.value}'. "
                    "Halting execution without merging."
                )
                break

        return self.context.stage_results


def run_flow_pipeline(
    context: FlowContext,
    from_stage: FlowStage | str | None = None,
    checkpoint_path: str | None = None,
) -> dict[str, Any]:
    """Top-level invocation wrapper for running the Holon Flow pipeline."""
    try:
        engine = PipelineEngine(context, from_stage=from_stage, checkpoint_path=checkpoint_path)
        engine.worktree_validator = target_worktree_problem
        engine.run()
        return context.to_dict()
    finally:
        if context.token_reduce:
            try:
                from sandbox_executor.cli import teardown_token_reduction_proxy

                teardown_token_reduction_proxy()
            except Exception:
                pass
